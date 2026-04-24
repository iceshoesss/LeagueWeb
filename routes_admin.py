"""管理员面板 API"""

import logging
from datetime import datetime, timedelta, UTC
from bson import ObjectId
from flask import Blueprint, jsonify, request, session

from db import get_db, to_iso_str, to_cst_str, GAME_TIMEOUT_MINUTES
from auth import _admin_required, is_super_admin, is_admin
from data import get_group_rankings

log = logging.getLogger("bgtracker")
admin_bp = Blueprint("admin", __name__)


def get_admin_stats():
    """管理员面板统计数据"""
    db = get_db()
    cutoff_str = (datetime.now(UTC) - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    total_players = db.league_players.count_documents({"verified": True})
    total_matches = db.league_matches.count_documents({"endedAt": {"$ne": None}})
    active_games = db.league_matches.count_documents({
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$gte": cutoff_str}}
        ]
    })
    problem_matches = db.league_matches.count_documents({
        "endedAt": {"$ne": None},
        "$or": [
            {"status": {"$in": ["timeout", "abandoned"]}},
            {"$and": [
                {"status": {"$exists": False}},
                {"players": {"$elemMatch": {"placement": None}}}
            ]}
        ]
    })
    queue_count = db.league_queue.count_documents({})
    waiting_groups = list(db.league_waiting_queue.find())
    waiting_count = sum(len(g.get("players", [])) for g in waiting_groups)

    return {
        "totalPlayers": total_players,
        "totalMatches": total_matches,
        "activeGames": active_games,
        "problemMatches": problem_matches,
        "queueCount": queue_count,
        "waitingCount": waiting_count,
    }


def get_admin_matches(page=1, per_page=20, status_filter="all"):
    """管理员对局列表"""
    db = get_db()
    query = {}

    if status_filter == "active":
        cutoff_str = (datetime.now(UTC) - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = {
            "$and": [
                {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
                {"startedAt": {"$gte": cutoff_str}}
            ]
        }
    elif status_filter == "completed":
        query = {
            "$and": [
                {"endedAt": {"$ne": None}},
                {"$or": [{"status": {"$exists": False}}, {"status": "completed"}]},
                {"players": {"$not": {"$elemMatch": {"placement": None}}}}
            ]
        }
    elif status_filter == "problem":
        query = {
            "endedAt": {"$ne": None},
            "$or": [
                {"status": {"$in": ["timeout", "abandoned"]}},
                {"$and": [
                    {"status": {"$exists": False}},
                    {"players": {"$elemMatch": {"placement": None}}}
                ]}
            ]
        }
    elif status_filter == "timeout":
        query = {"status": "timeout"}
    elif status_filter == "abandoned":
        query = {"status": "abandoned"}

    total = db.league_matches.count_documents(query)
    matches = list(db.league_matches.find(query)
                   .sort("startedAt", -1)
                   .skip((page - 1) * per_page)
                   .limit(per_page))

    cutoff_str = (datetime.now(UTC) - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for m in matches:
        m["_id"] = str(m["_id"])
        if m.get("tournamentGroupId"):
            m["tournamentGroupId"] = str(m["tournamentGroupId"])
        m["startedAt"] = to_iso_str(m.get("startedAt"))
        m["endedAt"] = to_iso_str(m.get("endedAt"))
        m["matchId"] = (m.get("gameUuid") or "")[:8].upper()
        m["players"] = sorted(m.get("players", []), key=lambda p: p.get("placement") or 999)
        is_active = (m.get("endedAt") is None or m.get("endedAt") == "") and m.get("startedAt", "") >= cutoff_str
        m["isActive"] = is_active
        m["statusLabel"] = (
            "进行中" if is_active else
            "超时" if m.get("status") == "timeout" else
            "掉线" if m.get("status") == "abandoned" else
            "已完成"
        )

    total_pages = max(1, (total + per_page - 1) // per_page)
    return matches, total, total_pages


def get_admin_players(page=1, per_page=50, search=""):
    """管理员选手列表"""
    db = get_db()
    query = {}
    if search:
        query = {"displayName": {"$regex": search, "$options": "i"}}

    total = db.league_players.count_documents(query)
    players = list(db.league_players.find(query)
                   .sort("verifiedAt", -1)
                   .skip((page - 1) * per_page)
                   .limit(per_page))

    battle_tags = [p.get("battleTag", "") for p in players if p.get("battleTag")]
    records = {}
    if battle_tags:
        for rec in db.player_records.find({"playerId": {"$in": battle_tags}}, {"playerId": 1, "verificationCode": 1}):
            records[rec["playerId"]] = rec.get("verificationCode", "")

    for p in players:
        p["_id"] = str(p["_id"])
        p["verifiedAt"] = to_cst_str(p.get("verifiedAt")) or ""
        p["createdAt"] = to_cst_str(p.get("createdAt")) or ""
        p["verificationCode"] = records.get(p.get("battleTag", ""), "")
        p["isSeed"] = bool(p.get("isSeed"))

    total_pages = max(1, (total + per_page - 1) // per_page)
    return players, total, total_pages


@admin_bp.route("/api/admin/stats")
def api_admin_stats():
    if not _admin_required():
        return jsonify({"error": "需要管理员权限"}), 403
    return jsonify(get_admin_stats())


@admin_bp.route("/api/admin/matches")
def api_admin_matches():
    if not _admin_required():
        return jsonify({"error": "需要管理员权限"}), 403
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    status_filter = request.args.get("status", "all")
    matches, total, total_pages = get_admin_matches(page, per_page, status_filter)
    return jsonify({"matches": matches, "total": total, "page": page, "totalPages": total_pages})


@admin_bp.route("/api/admin/players")
def api_admin_players():
    if not _admin_required():
        return jsonify({"error": "需要管理员权限"}), 403
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "")
    players, total, total_pages = get_admin_players(page, 50, search)
    return jsonify({"players": players, "total": total, "page": page, "totalPages": total_pages})


@admin_bp.route("/api/admin/match/<game_uuid>/force-end", methods=["POST"])
def api_admin_force_end(game_uuid):
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if not match:
        return jsonify({"error": "对局不存在"}), 404
    if match.get("endedAt") not in (None, ""):
        return jsonify({"error": "对局已结束"}), 400

    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.league_matches.update_one(
        {"gameUuid": game_uuid},
        {"$set": {"endedAt": now_str, "status": "timeout"}}
    )
    log.info(f"管理员 {admin_tag} 强制结束对局 {game_uuid}")
    return jsonify({"ok": True})


@admin_bp.route("/api/admin/match/<game_uuid>/force-abandon", methods=["POST"])
def api_admin_force_abandon(game_uuid):
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if not match:
        return jsonify({"error": "对局不存在"}), 404
    if match.get("endedAt") not in (None, ""):
        return jsonify({"error": "对局已结束"}), 400

    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.league_matches.update_one(
        {"gameUuid": game_uuid},
        {"$set": {"endedAt": now_str, "status": "abandoned"}}
    )
    log.info(f"管理员 {admin_tag} 强制标记掉线 {game_uuid}")
    return jsonify({"ok": True})


@admin_bp.route("/api/admin/queue/remove", methods=["POST"])
def api_admin_queue_remove():
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "缺少 name"}), 400

    db = get_db()
    result = db.league_queue.delete_one({"name": name})
    if result.deleted_count == 0:
        return jsonify({"error": "该玩家不在报名队列中"}), 404

    log.info(f"管理员 {admin_tag} 踢出报名队列: {name}")
    return jsonify({"ok": True})


@admin_bp.route("/api/admin/waiting/remove", methods=["POST"])
def api_admin_waiting_remove():
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "缺少 name"}), 400

    db = get_db()
    group = db.league_waiting_queue.find_one({"players.name": name})
    if not group:
        return jsonify({"error": "该玩家不在等待组中"}), 404

    remaining = [p for p in group["players"] if p["name"] != name]
    if remaining:
        db.league_waiting_queue.update_one(
            {"_id": group["_id"]},
            {"$set": {"players": remaining}}
        )
    else:
        db.league_waiting_queue.delete_one({"_id": group["_id"]})

    log.info(f"管理员 {admin_tag} 踢出等待组: {name}")
    return jsonify({"ok": True})


@admin_bp.route("/api/admin/match/<game_uuid>/reset", methods=["POST"])
def api_admin_reset_match(game_uuid):
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if not match:
        return jsonify({"error": "对局不存在"}), 404

    db.league_matches.update_one(
        {"gameUuid": game_uuid},
        {"$unset": {"endedAt": "", "status": ""}}
    )
    log.info(f"管理员 {admin_tag} 重置对局状态 {game_uuid}")
    return jsonify({"ok": True})


@admin_bp.route("/api/admin/admins")
def api_admin_admins():
    admin_tag = _admin_required()
    if not admin_tag or not is_super_admin(admin_tag):
        return jsonify({"error": "需要超级管理员权限"}), 403

    db = get_db()
    admins = list(db.league_admins.find().sort("addedAt", 1))
    for a in admins:
        a["_id"] = str(a["_id"])
        a["addedAt"] = to_iso_str(a.get("addedAt"))
    return jsonify(admins)


@admin_bp.route("/api/admin/admins/add", methods=["POST"])
def api_admin_admins_add():
    admin_tag = _admin_required()
    if not admin_tag or not is_super_admin(admin_tag):
        return jsonify({"error": "需要超级管理员权限"}), 403

    data = request.get_json() or {}
    battle_tag = data.get("battleTag", "").strip()
    if not battle_tag:
        return jsonify({"error": "BattleTag 不能为空"}), 400

    db = get_db()
    if db.league_admins.count_documents({"battleTag": battle_tag}) > 0:
        return jsonify({"error": f"{battle_tag} 已是管理员"}), 400

    db.league_admins.insert_one({
        "battleTag": battle_tag,
        "addedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "addedBy": admin_tag,
        "isSuperAdmin": False,
    })
    log.info(f"超级管理员 {admin_tag} 添加管理员: {battle_tag}")
    return jsonify({"ok": True})


@admin_bp.route("/api/admin/admins/remove", methods=["POST"])
def api_admin_admins_remove():
    admin_tag = _admin_required()
    if not admin_tag or not is_super_admin(admin_tag):
        return jsonify({"error": "需要超级管理员权限"}), 403

    data = request.get_json() or {}
    battle_tag = data.get("battleTag", "").strip()
    if not battle_tag:
        return jsonify({"error": "BattleTag 不能为空"}), 400
    if battle_tag == admin_tag:
        return jsonify({"error": "不能移除自己"}), 400

    db = get_db()
    target = db.league_admins.find_one({"battleTag": battle_tag})
    if not target:
        return jsonify({"error": f"{battle_tag} 不是管理员"}), 404
    if target.get("isSuperAdmin"):
        return jsonify({"error": "不能移除超级管理员"}), 403

    db.league_admins.delete_one({"battleTag": battle_tag})
    log.info(f"超级管理员 {admin_tag} 移除管理员: {battle_tag}")
    return jsonify({"ok": True})


@admin_bp.route("/api/admin/player/add", methods=["POST"])
def api_admin_player_add():
    """管理员手动添加选手（手机玩家/无插件玩家）"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    data = request.get_json() or {}
    battle_tag = data.get("battleTag", "").strip()
    display_name = data.get("displayName", "").strip()

    if not battle_tag:
        return jsonify({"error": "BattleTag 不能为空"}), 400
    if "#" not in battle_tag:
        return jsonify({"error": "BattleTag 格式应为 玩家名#1234"}), 400
    if not display_name:
        display_name = battle_tag.rsplit("#", 1)[0]

    db = get_db()
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    existing = db.league_players.find_one({"battleTag": battle_tag})
    if existing:
        return jsonify({"error": f"{battle_tag} 已注册"}), 400

    db.league_players.insert_one({
        "battleTag": battle_tag,
        "displayName": display_name,
        "accountIdLo": battle_tag,  # 手机玩家用 battleTag 作为伪 Lo
        "verified": True,
        "verifiedAt": now_str,
        "createdAt": now_str,
    })
    log.info(f"管理员 {admin_tag} 手动添加选手: {battle_tag}")
    return jsonify({"ok": True, "battleTag": battle_tag, "displayName": display_name})


@admin_bp.route("/api/admin/player/<path:battle_tag>/seed", methods=["PUT"])
def api_admin_player_seed(battle_tag):
    """设置/取消种子选手"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    player = db.league_players.find_one({"battleTag": battle_tag})
    if not player:
        return jsonify({"error": "选手不存在"}), 404

    new_val = not bool(player.get("isSeed"))
    db.league_players.update_one({"battleTag": battle_tag}, {"$set": {"isSeed": new_val}})
    log.info(f"管理员 {admin_tag} {'设置' if new_val else '取消'}种子选手: {battle_tag}")
    return jsonify({"ok": True, "isSeed": new_val})


@admin_bp.route("/api/admin/seed-players")
def api_admin_seed_players():
    """获取所有种子选手列表"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    seeds = list(db.league_players.find({"isSeed": True}).sort("displayName", 1))
    return jsonify([{
        "battleTag": p.get("battleTag", ""),
        "displayName": p.get("displayName", ""),
        "accountIdLo": str(p.get("accountIdLo", "")),
    } for p in seeds])


@admin_bp.route("/api/admin/group/<group_id>/advance", methods=["POST"])
def api_admin_manual_advance(group_id):
    """手动晋级：管理员从等待中/进行中的组指定晋级者"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    try:
        oid = ObjectId(group_id)
    except Exception:
        return jsonify({"error": "无效的 group ID"}), 400

    group = db.tournament_groups.find_one({"_id": oid})
    if not group:
        return jsonify({"error": "分组不存在"}), 404

    data = request.get_json() or {}
    advance_los = data.get("players", [])
    if not advance_los:
        return jsonify({"error": "请指定晋级者 accountIdLo 列表"}), 400
    if len(advance_los) > 4:
        return jsonify({"error": "最多只能选 4 人晋级"}), 400

    # 从分组 players 中找到对应的玩家信息
    group_players = {str(p.get("accountIdLo", "")): p for p in group.get("players", []) if p.get("accountIdLo")}
    quals = []
    for lo in advance_los:
        p = group_players.get(str(lo))
        if not p:
            return jsonify({"error": f"accountIdLo={lo} 不在此分组中"}), 400
        quals.append({
            "battleTag": p.get("battleTag", ""),
            "accountIdLo": p.get("accountIdLo", ""),
            "displayName": p.get("displayName", ""),
            "heroCardId": p.get("heroCardId", ""),
            "heroName": p.get("heroName", ""),
            "empty": False,
        })

    current_round = group.get("round", 1)
    gi = group.get("groupIndex", 1)
    tournament_name = group.get("tournamentName", "赛事")
    advance_lo_set = set(str(lo) for lo in advance_los)

    # grid 布局（海选赛）：只标记晋级/淘汰，不创建下一轮分组
    if group.get("layout") == "grid":
        players = group.get("players", [])
        update_ops = {}
        for i, p in enumerate(players):
            lo = str(p.get("accountIdLo", ""))
            if not lo or p.get("empty"):
                continue
            if lo in advance_lo_set:
                update_ops[f"players.{i}.qualified"] = True
                update_ops[f"players.{i}.eliminated"] = False
            else:
                update_ops[f"players.{i}.qualified"] = False
                update_ops[f"players.{i}.eliminated"] = True
        if update_ops:
            db.tournament_groups.update_one({"_id": oid}, {"$set": update_ops})
        # 标记源组为已完成
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.tournament_groups.update_one({"_id": oid}, {"$set": {"status": "done", "endedAt": now_str}})
        log.info(f"[manual-advance] 管理员 {admin_tag} 海选手动晋级 R{current_round}G{gi}: {len(advance_los)} 人晋级")
        return jsonify({"ok": True, "advanced": len(advance_los)})

    # 淘汰赛（bracket）：创建/填入下一轮分组
    groups_in_round = db.tournament_groups.count_documents({
        "round": current_round, "tournamentName": tournament_name,
    })
    next_group_index = (gi + 1) // 2 if groups_in_round > 1 else 1
    next_round = current_round + 1

    existing = db.tournament_groups.find_one({
        "round": next_round,
        "groupIndex": next_group_index,
        "tournamentName": tournament_name,
    })

    if existing:
        players = existing.get("players", [])
        empty_indices = [i for i, p in enumerate(players) if p.get("empty")]
        update_ops = {}
        for i, q in enumerate(quals):
            if i < len(empty_indices):
                update_ops[f"players.{empty_indices[i]}"] = q
        if update_ops:
            db.tournament_groups.update_one({"_id": existing["_id"]}, {"$set": update_ops})
        log.info(f"[manual-advance] 管理员 {admin_tag} 手动晋级 R{current_round}G{gi} → R{next_round}G{next_group_index}: {len(quals)} 人")
    else:
        all_players = quals + [{"battleTag": None, "accountIdLo": None, "displayName": "待定",
                                "heroCardId": None, "heroName": None, "empty": True}] * 4
        next_bo_n = group.get("boN", 3)
        db.tournament_groups.insert_one({
            "tournamentName": tournament_name,
            "round": next_round,
            "groupIndex": next_group_index,
            "status": "waiting",
            "boN": next_bo_n,
            "gamesPlayed": 0,
            "players": all_players,
            "nextRoundGroupId": None,
            "startedAt": None,
            "endedAt": None,
        })
        log.info(f"[manual-advance] 管理员 {admin_tag} 手动晋级 R{current_round}G{gi} → 创建 R{next_round}G{next_group_index}: {len(quals)} 人")

    # 标记源组为已完成（如果还不是 done）
    if group.get("status") != "done":
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.tournament_groups.update_one({"_id": oid}, {"$set": {"status": "done", "endedAt": now_str}})
        log.info(f"[manual-advance] 源组 R{current_round}G{gi} 标记为 done")

    return jsonify({"ok": True, "advanced": len(quals)})

@admin_bp.route("/api/admin/group/<group_id>/manual-record", methods=["POST"])
def api_admin_manual_record(group_id):
    """纯手工补录：为对局创建完整排名记录（插件失效时使用）"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    try:
        oid = ObjectId(group_id)
    except Exception:
        return jsonify({"error": "无效的 group ID"}), 400

    group = db.tournament_groups.find_one({"_id": oid})
    if not group:
        return jsonify({"error": "分组不存在"}), 404

    data = request.get_json() or {}
    placements = data.get("placements")  # {accountIdLo: placement}
    if not placements or not isinstance(placements, dict):
        return jsonify({"error": "请提供排名数据 placements"}), 400

    # 获取组内玩家
    group_players = [p for p in group.get("players", []) if not p.get("empty") and p.get("accountIdLo")]
    if not group_players:
        return jsonify({"error": "该组没有玩家"}), 400

    # 校验：每个玩家都必须有排名
    lo_set = {str(p["accountIdLo"]) for p in group_players}
    missing = lo_set - set(placements.keys())
    if missing:
        return jsonify({"error": f"缺少玩家排名: {', '.join(missing)}"}), 400

    # 校验排名值
    placement_vals = list(placements.values())
    if sorted(placement_vals) != list(range(1, len(group_players) + 1)):
        return jsonify({"error": f"排名必须是 1-{len(group_players)} 的不重复整数"}), 400

    # 构建 league_matches 的 players 列表
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    import uuid as _uuid
    players = []
    for p in group_players:
        lo = str(p["accountIdLo"])
        pl = placements[lo]
        pts = 9 if pl == 1 else max(1, 9 - pl)
        players.append({
            "accountIdLo": lo,
            "battleTag": p.get("battleTag", ""),
            "displayName": p.get("displayName", ""),
            "heroCardId": p.get("heroCardId", ""),
            "heroName": p.get("heroName", ""),
            "placement": pl,
            "points": pts,
        })

    # 创建 match 记录
    match_doc = {
        "gameUuid": str(_uuid.uuid4()),
        "players": players,
        "region": "CN",
        "mode": "solo",
        "startedAt": group.get("startedAt") or now_str,
        "endedAt": now_str,
        "tournamentGroupId": oid,
        "tournamentRound": group.get("round"),
        "manualRecord": True,
    }
    db.league_matches.insert_one(match_doc)
    log.info(f"[manual-record] 管理员 {admin_tag} 补录 R{group.get('round')}G{group.get('groupIndex')}: {len(players)} 人, gameUuid={match_doc['gameUuid']}")

    # 更新组状态
    bo_n = group.get("boN", 1)
    old_gp = group.get("gamesPlayed", 0)
    games_played = old_gp + 1
    update_fields = {"gamesPlayed": games_played}

    if games_played >= bo_n:
        update_fields["status"] = "done"
        update_fields["endedAt"] = now_str
        # 触发晋级
        from data import try_advance_group
        try_advance_group(db, group)
        log.info(f"[manual-record] BO 完成: gp={old_gp}→{games_played}/{bo_n}, 触发晋级")
    else:
        update_fields["status"] = "waiting"
        log.info(f"[manual-record] BO 进度: gp={old_gp}→{games_played}/{bo_n}")

    db.tournament_groups.update_one({"_id": oid}, {"$set": update_fields})

    return jsonify({"ok": True, "gameUuid": match_doc["gameUuid"], "gamesPlayed": games_played})

@admin_bp.route("/api/admin/match/<game_uuid>/edit-placement", methods=["PUT"])
def api_admin_edit_placement(game_uuid):
    """管理员修改对局排名（覆盖已有排名）"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if not match:
        return jsonify({"error": "对局不存在"}), 404

    data = request.get_json() or {}
    placements = data.get("placements", {})
    if not placements:
        return jsonify({"error": "未提供排名数据"}), 400

    players = match.get("players", [])
    n = len(players)
    values = [int(v) for v in placements.values()]
    if sorted(values) != list(range(1, n + 1)):
        return jsonify({"error": f"排名必须是 1-{n} 的不重复整数"}), 400

    # 覆盖所有玩家排名
    for i, p in enumerate(players):
        lo = str(p.get("accountIdLo", ""))
        if lo in placements:
            new_placement = int(placements[lo])
            new_points = 9 if new_placement == 1 else max(1, 9 - new_placement)
            db.league_matches.update_one(
                {"gameUuid": game_uuid},
                {"$set": {
                    f"players.{i}.placement": new_placement,
                    f"players.{i}.points": new_points,
                }}
            )

    log.info(f"[edit-placement] 管理员 {admin_tag} 修改对局 {game_uuid} 排名")
    return jsonify({"ok": True})
