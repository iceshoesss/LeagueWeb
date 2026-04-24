"""积分赛路由：队列、认证、绑定码、补录排名"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, UTC
from flask import Blueprint, jsonify, request, session

from db import get_db, to_iso_str, MIN_MATCH_PLAYERS
from auth import is_admin, GAME_UUID_RE
from cleanup import cleanup_stale_queues, cleanup_expired_bind_codes

log = logging.getLogger("bgtracker")
league_bp = Blueprint("league", __name__)


def _migrate_player_lo(db, battle_tag, old_lo, new_lo):
    """玩家 Lo 变更时（伪 Lo → 真 Lo），同步更新 tournament_groups 和 league_matches 中的旧记录"""
    if not old_lo or old_lo == new_lo:
        return
    # 只在伪 Lo（= battleTag）→ 真 Lo 时触发
    if old_lo != battle_tag:
        return

    log.info(f"[lo-migrate] {battle_tag}: '{old_lo}' → '{new_lo}'，同步历史记录")

    # 1. tournament_groups: players 中匹配伪 Lo 的更新为真 Lo
    r1 = db.tournament_groups.update_many(
        {"players.accountIdLo": old_lo},
        {"$set": {"players.$[elem].accountIdLo": new_lo}},
        array_filters=[{"elem.accountIdLo": old_lo}],
    )

    # 2. league_matches: players 中匹配伪 Lo 的更新为真 Lo
    r2 = db.league_matches.update_many(
        {"players.accountIdLo": old_lo},
        {"$set": {"players.$[elem].accountIdLo": new_lo}},
        array_filters=[{"elem.accountIdLo": old_lo}],
    )

    log.info(f"[lo-migrate] 完成: tournament_groups={r1.modified_count}, league_matches={r2.modified_count}")


# ── API 路由 ──────────────────────────────────────────

@league_bp.route("/api/players")
def api_players():
    from data import get_players
    return jsonify(get_players())


@league_bp.route("/api/players/<path:battle_tag>")
def api_player(battle_tag):
    from data import get_player
    player = get_player(battle_tag)
    if not player:
        return jsonify({"error": "选手不存在"}), 404
    return jsonify(player)


@league_bp.route("/api/match/<game_uuid>")
def api_match(game_uuid):
    from data import get_match
    match = get_match(game_uuid)
    if not match:
        return jsonify({"error": "对局不存在"}), 404
    return jsonify(match)


@league_bp.route("/api/match/<game_uuid>", methods=["DELETE"])
def api_delete_match(game_uuid):
    battle_tag = session.get("battleTag")
    if not battle_tag:
        return jsonify({"error": "请先登录"}), 401
    if not is_admin(battle_tag):
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    result = db.league_matches.delete_one({"gameUuid": game_uuid})
    if result.deleted_count == 0:
        return jsonify({"error": "对局不存在"}), 404

    log.info(f"管理员 {battle_tag} 删除对局 {game_uuid}")
    return jsonify({"ok": True, "gameUuid": game_uuid})


@league_bp.route("/api/matches")
def api_matches():
    from data import get_completed_matches
    return jsonify(get_completed_matches(limit=10))


@league_bp.route("/api/active-games")
def api_active_games():
    from data import get_active_games
    return jsonify(get_active_games())


@league_bp.route("/api/match/<game_uuid>/update-placement", methods=["POST"])
def api_update_placement(game_uuid):
    """手动补录对局排名"""
    battle_tag = session.get("battleTag")
    if not battle_tag:
        return jsonify({"error": "请先登录"}), 401

    data = request.get_json() or {}
    placements = data.get("placements", {})

    if not placements:
        return jsonify({"error": "未提供排名数据"}), 400

    db = get_db()
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if not match:
        return jsonify({"error": "对局不存在"}), 404

    admin = is_admin(battle_tag)

    if not admin:
        my_account_ids = set()
        in_match = False
        for p in match.get("players", []):
            if p.get("battleTag") == battle_tag:
                in_match = True
                my_account_ids.add(str(p.get("accountIdLo", "")))
        if not in_match:
            return jsonify({"error": "你不是这局对局的参与者"}), 403
        for lo in placements:
            if lo not in my_account_ids:
                return jsonify({"error": "你只能补录自己的排名"}), 403

    values = list(placements.values())
    if not values:
        return jsonify({"error": "未提供排名数据"}), 400
    if any(v < 1 or v > 8 for v in values):
        return jsonify({"error": "排名必须在 1-8 之间"}), 400
    if len(values) != len(set(values)):
        return jsonify({"error": "提交的排名中存在重复"}), 400

    players = match.get("players", [])
    updated = 0
    skipped_locked = 0
    for p in players:
        lo = str(p.get("accountIdLo", ""))
        if lo in placements:
            if p.get("placement") is not None:
                skipped_locked += 1
                continue
            placement = placements[lo]
            points = 9 if placement == 1 else max(1, 9 - placement)

            db.league_matches.update_one(
                {"gameUuid": game_uuid, "players.accountIdLo": lo},
                {"$set": {
                    "players.$.placement": placement,
                    "players.$.points": points
                }}
            )
            updated += 1

    if updated == 0:
        if skipped_locked > 0:
            return jsonify({"error": f"所有提交的玩家已有排名（已锁定 {skipped_locked} 人），无法修改"}), 400
        return jsonify({"error": "未匹配到任何玩家"}), 400

    # 7人提交 → 自动推算第8人
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if match:
        players = match.get("players", [])
        null_indices = [i for i, p in enumerate(players) if p.get("placement") is None]
        if len(null_indices) == 1:
            used = {p["placement"] for p in players if p.get("placement") is not None}
            remaining = set(range(1, len(players) + 1)) - used
            if len(remaining) == 1:
                auto_placement = remaining.pop()
                auto_points = 9 if auto_placement == 1 else max(1, 9 - auto_placement)
                db.league_matches.update_one(
                    {"gameUuid": game_uuid},
                    {"$set": {
                        f"players.{null_indices[0]}.placement": auto_placement,
                        f"players.{null_indices[0]}.points": auto_points,
                    }}
                )
                log.info(f"[update-placement] 自动推算: players[{null_indices[0]}] placement={auto_placement}")

    db.league_matches.update_one(
        {"gameUuid": game_uuid},
        {"$set": {"endedAt": match.get("endedAt") or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
         "$unset": {"status": ""}}
    )

    return jsonify({"ok": True, "updated": updated, "skipped_locked": skipped_locked})


# ── 报名队列 API ──────────────────────────────────────

@league_bp.route("/api/queue")
def api_queue():
    db = get_db()
    queue = list(db.league_queue.find().sort("joinedAt", 1))
    for q in queue:
        q["_id"] = str(q["_id"])
        q["joinedAt"] = to_iso_str(q.get("joinedAt"))
        q["lastSeen"] = to_iso_str(q.get("lastSeen"))
    return jsonify(queue)


@league_bp.route("/api/waiting-queue")
def api_waiting_queue():
    db = get_db()
    groups = list(db.league_waiting_queue.find().sort("createdAt", 1))
    for g in groups:
        g["_id"] = str(g["_id"])
        g["createdAt"] = to_iso_str(g.get("createdAt"))
    return jsonify(groups)


@league_bp.route("/api/queue/join", methods=["POST"])
def api_queue_join():
    name = session.get("battleTag") or session.get("displayName", "")
    if not name:
        return jsonify({"error": "请先登录"}), 401

    db = get_db()
    now_str = datetime.now(UTC).isoformat() + "Z"

    cleanup_stale_queues()

    if db.league_queue.find_one({"name": name}):
        return jsonify({"error": "已在报名队列中"}), 400
    if db.league_waiting_queue.find_one({"players.name": name}):
        return jsonify({"error": "已在等待队列中"}), 400

    # 优先补入未满的等待组
    incomplete_group = None
    for g in db.league_waiting_queue.find().sort("createdAt", 1):
        if len(g.get("players", [])) < MIN_MATCH_PLAYERS:
            incomplete_group = g
            break

    player_info = db.league_players.find_one({"battleTag": name})
    account_id_lo = str(player_info.get("accountIdLo", "")) if player_info else ""
    player_entry = {"name": name, "accountIdLo": account_id_lo}

    if incomplete_group:
        db.league_waiting_queue.update_one(
            {"_id": incomplete_group["_id"]},
            {"$push": {"players": player_entry}}
        )
        return jsonify({"ok": True, "name": name, "moved": True})

    db.league_queue.update_one(
        {"name": name},
        {"$setOnInsert": {"name": name, "joinedAt": now_str},
         "$set": {"lastSeen": now_str}},
        upsert=True,
    )

    signup_count = db.league_queue.count_documents({})
    if signup_count >= MIN_MATCH_PLAYERS:
        signup = list(db.league_queue.find().sort("joinedAt", 1).limit(MIN_MATCH_PLAYERS))
        players = []
        names = []
        for p in signup:
            p_name = p["name"]
            names.append(p_name)
            p_info = db.league_players.find_one({"battleTag": p_name})
            p_lo = str(p_info.get("accountIdLo", "")) if p_info else ""
            players.append({"name": p_name, "accountIdLo": p_lo})
        db.league_waiting_queue.insert_one({
            "players": players,
            "createdAt": datetime.now(UTC).isoformat() + "Z",
        })
        db.league_queue.delete_many({"name": {"$in": names}})
        return jsonify({"ok": True, "name": name, "moved": True})

    return jsonify({"ok": True, "name": name, "moved": False})


@league_bp.route("/api/queue/leave", methods=["POST"])
def api_queue_leave():
    name = session.get("battleTag") or session.get("displayName", "")
    if not name:
        return jsonify({"error": "请先登录"}), 401

    db = get_db()
    cleanup_stale_queues()
    db.league_queue.delete_one({"name": name})

    group = db.league_waiting_queue.find_one({"players.name": name})
    if group:
        remaining = [p for p in group["players"] if p["name"] != name]
        if remaining:
            while len(remaining) < MIN_MATCH_PLAYERS:
                filler = db.league_queue.find_one_and_delete({}, sort=[("joinedAt", 1)])
                if not filler:
                    break
                f_name = filler["name"]
                f_info = db.league_players.find_one({"battleTag": f_name})
                f_lo = str(f_info.get("accountIdLo", "")) if f_info else ""
                remaining.append({"name": f_name, "accountIdLo": f_lo})
            db.league_waiting_queue.update_one(
                {"_id": group["_id"]},
                {"$set": {"players": remaining}}
            )
        else:
            db.league_waiting_queue.delete_one({"_id": group["_id"]})
    return jsonify({"ok": True, "name": name})


# ── 注册验证 API ──────────────────────────────────────

@league_bp.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    battle_tag = data.get("battleTag", "").strip()
    verification_code = data.get("verificationCode", "").strip().upper()

    if not battle_tag:
        return jsonify({"error": "BattleTag 不能为空"}), 400
    if not verification_code:
        return jsonify({"error": "验证码不能为空"}), 400

    db = get_db()
    rating = db.player_records.find_one({"playerId": battle_tag})
    if not rating:
        return jsonify({"error": f"未找到 {battle_tag} 的游戏记录，请先使用插件完成一局游戏"}), 404

    stored_code = rating.get("verificationCode")
    if not stored_code:
        return jsonify({"error": "该记录尚未生成验证码，请使用最新版插件完成一局游戏后重试"}), 400

    if verification_code != stored_code.upper():
        return jsonify({"error": "验证码不正确，请检查插件日志中的验证码"}), 400

    raw_lo = rating.get("accountIdLo")
    account_id_lo = str(raw_lo) if raw_lo else ""

    display_name = battle_tag
    hash_idx = battle_tag.find("#")
    if hash_idx > 0:
        display_name = battle_tag[:hash_idx]

    # 读取旧 Lo，用于判断是否需要迁移
    old_player = db.league_players.find_one({"battleTag": battle_tag}, {"accountIdLo": 1})
    old_lo = str(old_player.get("accountIdLo", "")) if old_player else ""

    db.league_players.update_one(
        {"battleTag": battle_tag},
        {"$set": {
            "battleTag": battle_tag,
            "accountIdLo": account_id_lo,
            "displayName": display_name,
            "verified": True,
            "verifiedAt": datetime.now(UTC).isoformat() + "Z",
        },
        "$setOnInsert": {
            "createdAt": datetime.now(UTC).isoformat() + "Z",
        }},
        upsert=True,
    )

    # 伪 Lo → 真 Lo 时同步历史记录
    _migrate_player_lo(db, battle_tag, old_lo, account_id_lo)

    session["battleTag"] = battle_tag
    session["displayName"] = display_name

    return jsonify({"ok": True, "battleTag": battle_tag, "displayName": display_name})


@league_bp.route("/api/verify")
def api_verify():
    battle_tag = request.args.get("battleTag", "").strip()
    if not battle_tag:
        return jsonify({"error": "缺少 battleTag 参数"}), 400

    db = get_db()
    player = db.league_players.find_one({"battleTag": battle_tag})
    if player:
        return jsonify({
            "verified": player.get("verified", False),
            "displayName": player.get("displayName", ""),
        })
    return jsonify({"verified": False})


@league_bp.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    battle_tag = data.get("battleTag", "").strip()
    verification_code = data.get("verificationCode", "").strip().upper()

    if not battle_tag:
        return jsonify({"error": "BattleTag 不能为空"}), 400
    if not verification_code:
        return jsonify({"error": "验证码不能为空"}), 400

    db = get_db()
    rating = db.player_records.find_one({"playerId": battle_tag})
    if not rating:
        return jsonify({"error": f"未找到 {battle_tag} 的游戏记录"}), 404

    stored_code = rating.get("verificationCode")
    if not stored_code:
        return jsonify({"error": "该记录尚未生成验证码，请使用最新版插件完成一局游戏"}), 400

    if verification_code != stored_code.upper():
        return jsonify({"error": "验证码不正确"}), 403

    display_name = battle_tag
    hash_idx = battle_tag.find("#")
    if hash_idx > 0:
        display_name = battle_tag[:hash_idx]

    raw_lo = rating.get("accountIdLo")
    account_id_lo = str(raw_lo) if raw_lo else ""

    # 读取旧 Lo，用于判断是否需要迁移
    old_player = db.league_players.find_one({"battleTag": battle_tag}, {"accountIdLo": 1})
    old_lo = str(old_player.get("accountIdLo", "")) if old_player else ""

    db.league_players.update_one(
        {"battleTag": battle_tag},
        {"$set": {
            "battleTag": battle_tag,
            "accountIdLo": account_id_lo,
            "displayName": display_name,
            "verified": True,
            "verifiedAt": datetime.now(UTC).isoformat() + "Z",
        },
        "$setOnInsert": {
            "createdAt": datetime.now(UTC).isoformat() + "Z",
        }},
        upsert=True,
    )

    # 伪 Lo → 真 Lo 时同步历史记录
    _migrate_player_lo(db, battle_tag, old_lo, account_id_lo)

    session["battleTag"] = battle_tag
    session["displayName"] = display_name

    return jsonify({"ok": True, "battleTag": battle_tag, "displayName": display_name})


@league_bp.route("/api/logout", methods=["POST"])
def api_logout():
    battle_tag = session.get("battleTag")
    if battle_tag:
        db = get_db()
        db.league_queue.delete_one({"name": battle_tag})
        group = db.league_waiting_queue.find_one({"players.name": battle_tag})
        if group:
            remaining = [p for p in group["players"] if p["name"] != battle_tag]
            if remaining:
                while len(remaining) < MIN_MATCH_PLAYERS:
                    filler = db.league_queue.find_one_and_delete({}, sort=[("joinedAt", 1)])
                    if not filler:
                        break
                    f_name = filler["name"]
                    f_info = db.league_players.find_one({"battleTag": f_name})
                    f_lo = str(f_info.get("accountIdLo", "")) if f_info else ""
                    remaining.append({"name": f_name, "accountIdLo": f_lo})
                db.league_waiting_queue.update_one(
                    {"_id": group["_id"]},
                    {"$set": {"players": remaining}}
                )
            else:
                db.league_waiting_queue.delete_one({"_id": group["_id"]})
    session.clear()
    return jsonify({"ok": True})


# ── QQ 绑定 API ──────────────────────────────────────

BIND_CODE_EXPIRE_MINUTES = 5

@league_bp.route("/api/bind-code", methods=["POST"])
def api_bind_code():
    battle_tag = session.get("battleTag")
    if not battle_tag:
        return jsonify({"error": "请先登录"}), 401

    db = get_db()
    cleanup_expired_bind_codes()

    code = secrets.token_hex(3).upper()
    expire = (datetime.now(UTC) + timedelta(minutes=BIND_CODE_EXPIRE_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")

    db.league_players.update_one(
        {"battleTag": battle_tag},
        {"$set": {"bindCode": code, "bindCodeExpire": expire}},
        upsert=True
    )
    return jsonify({"ok": True, "code": code, "expireMinutes": BIND_CODE_EXPIRE_MINUTES})


@league_bp.route("/api/bind-code/verify", methods=["POST"])
def api_bind_code_verify():
    import os
    BOT_API_KEY = os.environ.get("BOT_API_KEY", "")

    data = request.get_json() or {}
    bot_key = data.get("botKey", "")
    code = data.get("code", "").strip().upper()

    if not BOT_API_KEY:
        return jsonify({"error": "绑定功能未启用"}), 503
    if bot_key != BOT_API_KEY:
        return jsonify({"error": "认证失败"}), 403
    if not code:
        return jsonify({"error": "绑定码不能为空"}), 400

    db = get_db()
    cleanup_expired_bind_codes()

    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    player = db.league_players.find_one({
        "bindCode": code,
        "bindCodeExpire": {"$gt": now_str}
    })
    if not player:
        return jsonify({"error": "绑定码无效或已过期"}), 404

    db.league_players.update_one(
        {"_id": player["_id"]},
        {"$unset": {"bindCode": "", "bindCodeExpire": ""}}
    )
    return jsonify({"ok": True, "battleTag": player["battleTag"], "displayName": player.get("displayName", "")})
