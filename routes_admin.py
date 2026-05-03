"""管理员面板 API"""

import re
import logging
from datetime import datetime, timedelta, UTC
from bson import ObjectId
from flask import Blueprint, jsonify, request, session

from db import get_db, to_iso_str, to_cst_str, GAME_TIMEOUT_MINUTES
from auth import _admin_required, is_super_admin, is_admin
from data import get_group_rankings
from sse import evt_active_games, evt_queue, evt_waiting_queue, evt_matches, evt_problem_matches, evt_bracket

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


def get_admin_matches(page=1, per_page=20, status_filter="all", search=""):
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

    if search:
        search_escaped = re.escape(search)
        query["players.displayName"] = {"$regex": search_escaped, "$options": "i"}

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
    search = request.args.get("search", "").strip()
    matches, total, total_pages = get_admin_matches(page, per_page, status_filter, search)
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

    # 淘汰赛对局：重算该组 rankings
    tg_id = match.get("tournamentGroupId")
    if tg_id:
        from data import recalc_group_rankings
        recalc_group_rankings(db, tg_id)

    evt_active_games.set()
    evt_matches.set()
    evt_problem_matches.set()
    evt_bracket.set()
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

    # 淘汰赛对局：重算该组 rankings
    tg_id = match.get("tournamentGroupId")
    if tg_id:
        from data import recalc_group_rankings
        recalc_group_rankings(db, tg_id)

    evt_active_games.set()
    evt_matches.set()
    evt_problem_matches.set()
    evt_bracket.set()
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
    evt_queue.set()
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
    evt_waiting_queue.set()
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

    # 淘汰赛对局：重算该组 rankings
    tg_id = match.get("tournamentGroupId")
    if tg_id:
        from data import recalc_group_rankings
        recalc_group_rankings(db, tg_id)

    evt_active_games.set()
    evt_matches.set()
    evt_problem_matches.set()
    evt_bracket.set()
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
    # 决赛（最后一轮）不允许晋级
    max_round_doc = db.tournament_groups.find(
        {"tournamentName": tournament_name}, {"round": 1}
    ).sort("round", -1).limit(1)
    max_round_list = list(max_round_doc)
    if max_round_list and current_round >= max_round_list[0].get("round", 1):
        return jsonify({"error": "决赛组不能晋级到下一轮"}), 400

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

    from routes_tournament import invalidate_bracket_cache
    invalidate_bracket_cache()
    # 重算该组 rankings（标记 done 后确保缓存最新）
    from data import recalc_group_rankings
    recalc_group_rankings(db, oid)
    evt_bracket.set()
    return jsonify({"ok": True, "advanced": len(quals)})

@admin_bp.route("/api/admin/group/<group_id>/manual-record", methods=["POST"])
def api_admin_manual_record(group_id):
    """补录淘汰赛排名：增量提交，每次可只填 1 人，和 match_edit 逻辑一致"""
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

    # 校验排名值
    for lo, pl in placements.items():
        if not isinstance(pl, int) or pl < 1 or pl > 8:
            return jsonify({"error": f"排名必须是 1-8 的整数"}), 400
    # 检查提交的排名之间是否有重复
    submitted_vals = list(placements.values())
    if len(submitted_vals) != len(set(submitted_vals)):
        return jsonify({"error": "提交的排名中存在重复"}), 400

    # 获取组内玩家
    group_players = [p for p in group.get("players", []) if not p.get("empty") and p.get("accountIdLo")]
    if not group_players:
        return jsonify({"error": "该组没有玩家"}), 400
    n = len(group_players)
    lo_set = {str(p["accountIdLo"]) for p in group_players}

    # 查找该组对局：优先复用超时/掉线对局，其次活跃对局
    existing_match = db.league_matches.find_one({
        "tournamentGroupId": oid,
        "status": {"$in": ["timeout", "abandoned"]},
    })
    if existing_match:
        # 复用超时/掉线对局：清除 endedAt 和 status，回到可补录状态
        db.league_matches.update_one(
            {"_id": existing_match["_id"]},
            {"$unset": {"endedAt": "", "status": ""}}
        )
        existing_match["endedAt"] = None
        existing_match.pop("status", None)
        log.info(f"[manual-record] 管理员 {admin_tag} 复用超时/掉线对局 {existing_match['gameUuid']}，清除 endedAt/status")
    else:
        existing_match = db.league_matches.find_one({"tournamentGroupId": oid, "endedAt": None})

    if not existing_match:
        # 创建空对局（所有玩家 placement=null）
        import uuid as _uuid
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        players = []
        for p in group_players:
            players.append({
                "accountIdLo": str(p["accountIdLo"]),
                "battleTag": p.get("battleTag", ""),
                "displayName": p.get("displayName", ""),
                "heroCardId": p.get("heroCardId", ""),
                "heroName": p.get("heroName", ""),
                "placement": None,
                "points": None,
            })
        existing_match = {
            "gameUuid": str(_uuid.uuid4()),
            "players": players,
            "region": "CN",
            "mode": "solo",
            "startedAt": now_str,
            "endedAt": None,
            "tournamentGroupId": oid,
            "tournamentRound": group.get("round"),
            "manualRecord": True,
        }
        db.league_matches.insert_one(existing_match)
        log.info(f"[manual-record] 管理员 {admin_tag} 创建空对局 R{group.get('round')}G{group.get('groupIndex')}, gameUuid={existing_match['gameUuid']}")

    game_uuid = existing_match["gameUuid"]

    # 收集已有排名，检查提交的排名是否和锁定的重复
    locked_placements = {}
    for p in existing_match.get("players", []):
        lo = str(p.get("accountIdLo", ""))
        if p.get("placement") is not None and lo:
            locked_placements[lo] = p["placement"]
    for lo, pl in placements.items():
        if pl in locked_placements.values():
            conflict_lo = [k for k, v in locked_placements.items() if v == pl]
            return jsonify({"error": f"第{pl}名已被锁定，不能重复"}), 400

    # 逐个更新玩家排名（原子条件写入，防止并发重复）
    updated = 0
    skipped_locked = 0
    for p in existing_match.get("players", []):
        lo = str(p.get("accountIdLo", ""))
        if lo in placements:
            if p.get("placement") is not None:
                skipped_locked += 1
                continue
            if lo not in lo_set:
                return jsonify({"error": f"玩家 {lo} 不在该组中"}), 400
            placement = placements[lo]
            points = 9 if placement == 1 else max(1, 9 - placement)
            # 原子写入：仅当该位置仍为 null 时才更新
            upd = db.league_matches.update_one(
                {"gameUuid": game_uuid, "players.accountIdLo": lo, "players.placement": None},
                {"$set": {"players.$.placement": placement, "players.$.points": points}}
            )
            if upd.modified_count > 0:
                updated += 1
            else:
                skipped_locked += 1

    if updated == 0:
        if skipped_locked > 0:
            return jsonify({"error": f"所有提交的玩家已有排名（已锁定 {skipped_locked} 人）"}), 400
        return jsonify({"error": "未匹配到任何玩家"}), 400

    # 重新读取最新状态
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    players = match.get("players", []) if match else []
    all_filled = all(pl.get("placement") is not None for pl in players)
    i_did_finalize = False
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    if all_filled:
        # 尝试原子终结（endedAt=None → 设置 endedAt）
        fin_result = db.league_matches.update_one(
            {"gameUuid": game_uuid, "endedAt": None},
            {"$set": {"endedAt": now_str}}
        )
        if fin_result.modified_count > 0:
            i_did_finalize = True
    else:
        # 检查是否只剩 1 个空位 → 原子推算 + 终结
        null_indices = [i for i, pl in enumerate(players) if pl.get("placement") is None]
        if len(null_indices) == 1:
            used = {pl["placement"] for pl in players if pl.get("placement") is not None}
            remaining = set(range(1, len(players) + 1)) - used
            if len(remaining) == 1:
                auto_placement = remaining.pop()
                auto_points = 9 if auto_placement == 1 else max(1, 9 - auto_placement)
                auto_result = db.league_matches.update_one(
                    {"gameUuid": game_uuid, f"players.{null_indices[0]}.placement": None, "endedAt": None},
                    {"$set": {
                        f"players.{null_indices[0]}.placement": auto_placement,
                        f"players.{null_indices[0]}.points": auto_points,
                        "endedAt": now_str,
                    }}
                )
                if auto_result.modified_count > 0:
                    i_did_finalize = True
                    log.info(f"[manual-record] 自动推算+终结: players[{null_indices[0]}] placement={auto_placement}")

    if i_did_finalize:
        log.info(f"[manual-record] 全部排名已提交，对局结束 {game_uuid}")

    # BO 累计（仅终结方执行，原子递增防竞态）
    if i_did_finalize:
        bo_n = group.get("boN", 1)
        old_gp = group.get("gamesPlayed", 0)
        new_gp = old_gp + 1
        update_fields = {"gamesPlayed": new_gp}

        if new_gp >= bo_n:
            update_fields["status"] = "done"
            update_fields["endedAt"] = now_str
            from data import try_advance_group
            try_advance_group(db, group)
            from routes_tournament import invalidate_bracket_cache
            invalidate_bracket_cache()
            log.info(f"[manual-record] BO 完成: gp={old_gp}→{new_gp}/{bo_n}, 触发晋级")
        else:
            update_fields["status"] = "waiting"
            log.info(f"[manual-record] BO 进度: gp={old_gp}→{new_gp}/{bo_n}")

        db.tournament_groups.update_one({"_id": oid}, {"$set": update_fields})

    # 每次补录都重算 rankings + 清缓存
    from data import recalc_group_rankings
    recalc_group_rankings(db, oid)
    from routes_tournament import invalidate_bracket_cache
    invalidate_bracket_cache()

    evt_matches.set()
    evt_problem_matches.set()
    evt_bracket.set()
    return jsonify({"ok": True, "gameUuid": game_uuid, "updated": updated, "skipped_locked": skipped_locked, "finalized": i_did_finalize})

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

    # 淘汰赛对局：重算该组 rankings + 清对阵图缓存
    tg_id = match.get("tournamentGroupId")
    if tg_id:
        from data import recalc_group_rankings
        recalc_group_rankings(db, tg_id)
        from routes_tournament import invalidate_bracket_cache
        invalidate_bracket_cache()

    evt_matches.set()
    evt_problem_matches.set()
    evt_bracket.set()
    return jsonify({"ok": True})


# ── 数据修复 API ─────────────────────────────────────

@admin_bp.route("/api/admin/search-players")
@_admin_required
def search_players():
    """模糊搜索选手（用于数据修复自动补全）"""
    db = get_db()
    q = (request.args.get("q") or "").strip()
    if not q or len(q) < 1:
        return jsonify([])

    import re
    escaped = re.escape(q)
    regex = {"$regex": escaped, "$options": "i"}
    results = list(db.league_players.find(
        {"$or": [{"battleTag": regex}, {"displayName": regex}]},
        {"battleTag": 1, "displayName": 1, "accountIdLo": 1}
    ).limit(10))

    return jsonify([{
        "battleTag": r.get("battleTag", ""),
        "displayName": r.get("displayName", ""),
        "accountIdLo": str(r.get("accountIdLo", "")),
    } for r in results])


def _lookup_account_id(db, battle_tag):
    """从 league_players 查 accountIdLo"""
    lp = db.league_players.find_one({"battleTag": battle_tag})
    if lp and lp.get("accountIdLo"):
        return str(lp["accountIdLo"])
    return None


@admin_bp.route("/api/admin/data-fix/migrate-account", methods=["POST"])
def datafix_migrate_account():
    """账号迁移：将旧账号的比赛记录关联到新账号"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403
    db = get_db()
    data = request.get_json() or {}
    old_tag = (data.get("old_tag") or "").strip()
    new_tag = (data.get("new_tag") or "").strip()
    old_lo = (data.get("old_lo") or "").strip()
    new_lo = (data.get("new_lo") or "").strip()
    apply = data.get("apply", False)

    if not old_tag or not new_tag:
        return jsonify({"error": "旧账号和新账号 BattleTag 不能为空"}), 400

    if not old_lo:
        old_lo = _lookup_account_id(db, old_tag)
    if not new_lo:
        new_lo = _lookup_account_id(db, new_tag)

    if not old_lo:
        return jsonify({"error": f"未找到旧账号 {old_tag} 的 accountIdLo"}), 404
    if not new_lo:
        return jsonify({"error": f"未找到新账号 {new_tag} 的 accountIdLo，需先用插件打一局"}), 404

    new_display = new_tag.split("#")[0] if "#" in new_tag else new_tag
    old_lo_types = [old_lo, int(old_lo)] if old_lo.isdigit() else [old_lo]

    changes = []

    # league_matches
    for m in db.league_matches.find({"players.accountIdLo": {"$in": old_lo_types}}):
        for i, p in enumerate(m.get("players", [])):
            lo_val = p.get("accountIdLo")
            if str(lo_val) == old_lo or lo_val == old_lo:
                old_name = p.get("displayName") or p.get("battleTag") or "?"
                changes.append({
                    "collection": "league_matches",
                    "docId": str(m["_id"]),
                    "index": i,
                    "oldName": old_name,
                    "gameUuid": m.get("gameUuid"),
                })
                if apply:
                    db.league_matches.update_one({"_id": m["_id"]}, {"$set": {
                        f"players.{i}.accountIdLo": new_lo,
                        f"players.{i}.battleTag": new_tag,
                        f"players.{i}.displayName": new_display,
                    }})

    # tournament_groups
    for g in db.tournament_groups.find({"players.accountIdLo": {"$in": old_lo_types}}):
        for i, p in enumerate(g.get("players", [])):
            lo_val = p.get("accountIdLo")
            if str(lo_val) == old_lo or lo_val == old_lo:
                old_name = p.get("displayName") or p.get("battleTag") or "?"
                changes.append({
                    "collection": "tournament_groups",
                    "docId": str(g["_id"]),
                    "index": i,
                    "oldName": old_name,
                    "tournamentName": g.get("tournamentName", ""),
                })
                if apply:
                    db.tournament_groups.update_one({"_id": g["_id"]}, {"$set": {
                        f"players.{i}.accountIdLo": new_lo,
                        f"players.{i}.battleTag": new_tag,
                        f"players.{i}.displayName": new_display,
                    }})

    return jsonify({
        "ok": True,
        "applied": apply,
        "old_tag": old_tag, "old_lo": old_lo,
        "new_tag": new_tag, "new_lo": new_lo,
        "changes": changes,
        "count": len(changes),
    })


@admin_bp.route("/api/admin/data-fix/migrate-filler", methods=["POST"])
def datafix_migrate_filler():
    """补位替换：将补位玩家最近 N 局替换为替补账号"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403
    db = get_db()
    data = request.get_json() or {}
    tag = (data.get("tag") or "").strip()
    lo = (data.get("lo") or "").strip()
    count = data.get("count")
    sub_tag = (data.get("sub_tag") or "无耻之替补#1234").strip()
    sub_lo = (data.get("sub_lo") or "").strip()
    apply = data.get("apply", False)

    if not tag:
        return jsonify({"error": "补位玩家 BattleTag 不能为空"}), 400
    if not count or count <= 0:
        return jsonify({"error": "替换局数必须为正整数"}), 400

    if not lo:
        lo = _lookup_account_id(db, tag)
    if not sub_lo:
        sub_lo = _lookup_account_id(db, sub_tag)

    if not lo:
        return jsonify({"error": f"未找到 {tag} 的 accountIdLo"}), 404
    if not sub_lo:
        return jsonify({"error": f"替补账号 {sub_tag} 未注册，需先用插件打一局"}), 404

    sub_display = sub_tag.split("#")[0] if "#" in sub_tag else sub_tag
    lo_types = [lo, int(lo)] if lo.isdigit() else [lo]

    # 查该玩家所有已结束联赛对局，按时间倒序
    matches = list(db.league_matches.find(
        {"players.accountIdLo": {"$in": lo_types}, "endedAt": {"$ne": None}},
        {"gameUuid": 1, "endedAt": 1, "startedAt": 1, "players": 1, "tournamentGroupId": 1}
    ).sort("endedAt", -1))

    if not matches:
        return jsonify({"error": f"未找到 {tag} 的联赛对局记录"}), 404

    if count > len(matches):
        count = len(matches)

    target_matches = matches[:count]
    changes = []

    for m in target_matches:
        for i, p in enumerate(m.get("players", [])):
            lo_val = p.get("accountIdLo")
            if str(lo_val) == lo or lo_val == lo:
                old_name = p.get("displayName") or p.get("battleTag") or "?"
                changes.append({
                    "collection": "league_matches",
                    "docId": str(m["_id"]),
                    "index": i,
                    "oldName": old_name,
                    "gameUuid": m.get("gameUuid"),
                    "endedAt": m.get("endedAt"),
                    "placement": p.get("placement"),
                    "points": p.get("points"),
                })
                if apply:
                    db.league_matches.update_one({"_id": m["_id"]}, {"$set": {
                        f"players.{i}.accountIdLo": sub_lo,
                        f"players.{i}.battleTag": sub_tag,
                        f"players.{i}.displayName": sub_display,
                    }})

    # 关联的 tournament_groups
    tg_ids = set()
    for m in target_matches:
        if m.get("tournamentGroupId"):
            tg_ids.add(m["tournamentGroupId"])

    for tg_id in tg_ids:
        g = db.tournament_groups.find_one({"_id": tg_id})
        if not g:
            continue
        for i, p in enumerate(g.get("players", [])):
            lo_val = p.get("accountIdLo")
            if str(lo_val) == lo or lo_val == lo:
                old_name = p.get("displayName") or p.get("battleTag") or "?"
                changes.append({
                    "collection": "tournament_groups",
                    "docId": str(g["_id"]),
                    "index": i,
                    "oldName": old_name,
                    "tournamentName": g.get("tournamentName", ""),
                })
                if apply:
                    db.tournament_groups.update_one({"_id": g["_id"]}, {"$set": {
                        f"players.{i}.accountIdLo": sub_lo,
                        f"players.{i}.battleTag": sub_tag,
                        f"players.{i}.displayName": sub_display,
                    }})

    return jsonify({
        "ok": True,
        "applied": apply,
        "tag": tag, "lo": lo,
        "sub_tag": sub_tag, "sub_lo": sub_lo,
        "totalMatches": len(matches),
        "changes": changes,
        "count": len(changes),
    })


@admin_bp.route("/api/admin/data-fix/recalc-rankings", methods=["POST"])
def datafix_recalc_rankings():
    """重算所有 tournament_groups 的 rankings 缓存"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403
    db = get_db()
    from data import recalc_group_rankings

    groups = list(db.tournament_groups.find({}, {"_id": 1, "tournamentName": 1, "round": 1, "groupIndex": 1}))
    updated = 0
    for g in groups:
        recalc_group_rankings(db, g["_id"])
        updated += 1

    return jsonify({"ok": True, "updated": updated})


@admin_bp.route("/api/admin/data-fix/set-rule", methods=["POST"])
def datafix_set_rule():
    """修改赛事晋级规则"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403
    db = get_db()
    data = request.get_json() or {}
    tournament_name = (data.get("tournament_name") or "").strip()
    rule = (data.get("rule") or "").strip()

    if not tournament_name or rule not in ("golden", "chicken"):
        return jsonify({"error": "参数无效，rule 须为 golden 或 chicken"}), 400

    result = db.tournament_groups.update_many(
        {"tournamentName": tournament_name},
        {"$set": {"advancementRule": rule}}
    )

    return jsonify({"ok": True, "matched": result.matched_count, "modified": result.modified_count, "rule": rule})


@admin_bp.route("/api/admin/data-fix/redistribute-seeds", methods=["POST"])
def datafix_redistribute_seeds():
    """重分配淘汰赛分组种子（保证每组 1 个种子）"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403
    db = get_db()
    data = request.get_json() or {}
    tournament_name = (data.get("tournament_name") or "").strip()

    if not tournament_name:
        return jsonify({"error": "赛事名称不能为空"}), 400

    groups = list(db.tournament_groups.find({
        "tournamentName": tournament_name,
        "status": "waiting",
        "gamesPlayed": 0,
    }).sort("groupIndex", 1))

    if not groups:
        return jsonify({"error": f"未找到赛事 {tournament_name} 的未开始分组"}), 404

    # 收集所有种子和非种子
    seeds = []
    non_seeds = []
    for g in groups:
        for p in g.get("players", []):
            if p.get("empty"):
                continue
            lo = str(p.get("accountIdLo", ""))
            if not lo:
                continue
            lp = db.league_players.find_one({"battleTag": p.get("battleTag", "")})
            is_seed = lp.get("isSeed", False) if lp else False
            entry = {"battleTag": p.get("battleTag", ""), "accountIdLo": lo,
                     "displayName": p.get("displayName", ""), "isSeed": is_seed}
            if is_seed:
                seeds.append(entry)
            else:
                non_seeds.append(entry)

    if not seeds:
        return jsonify({"error": "没有种子选手，无法分配"}), 400

    import random
    random.shuffle(seeds)
    random.shuffle(non_seeds)

    all_players = seeds + non_seeds
    num_groups = len(groups)
    group_size = 8

    # 分配
    new_groups = [[] for _ in range(num_groups)]
    for idx, p in enumerate(all_players):
        new_groups[idx % num_groups].append(p)

    # 补空位
    for i in range(num_groups):
        while len(new_groups[i]) < group_size:
            new_groups[i].append({"battleTag": None, "accountIdLo": None,
                                  "displayName": "待定", "empty": True})

    # 写入
    for i, g in enumerate(groups):
        db.tournament_groups.update_one(
            {"_id": g["_id"]},
            {"$set": {"players": new_groups[i]}}
        )

    log.info(f"[redistribute-seeds] 管理员 {session.get('battleTag')} 重分配 {tournament_name}: {len(seeds)}种子 {len(non_seeds)}非种子 → {num_groups}组")

    return jsonify({
        "ok": True,
        "tournament": tournament_name,
        "seeds": len(seeds),
        "nonSeeds": len(non_seeds),
        "groups": num_groups,
    })


@admin_bp.route("/api/admin/data-fix/swap-player", methods=["POST"])
def datafix_swap_player():
    """替换错误晋级的玩家"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403
    db = get_db()
    data = request.get_json() or {}
    group_id = (data.get("group_id") or "").strip()
    wrong_lo = (data.get("wrong_lo") or "").strip()
    correct_lo = (data.get("correct_lo") or "").strip()

    if not group_id or not wrong_lo or not correct_lo:
        return jsonify({"error": "参数不完整"}), 400

    from bson import ObjectId
    group = db.tournament_groups.find_one({"_id": ObjectId(group_id) if len(group_id) == 24 else group_id})
    if not group:
        return jsonify({"error": "分组不存在"}), 404

    players = group.get("players", [])

    # 找正确的玩家数据（从上一轮组中）
    correct_player = None
    prev_round_groups = list(db.tournament_groups.find({
        "tournamentName": group.get("tournamentName"),
        "round": group.get("round", 1) - 1,
    }))
    for g in prev_round_groups:
        for p in g.get("players", []):
            if str(p.get("accountIdLo", "")) == correct_lo:
                correct_player = {
                    "battleTag": p.get("battleTag", ""),
                    "accountIdLo": p.get("accountIdLo", ""),
                    "displayName": p.get("displayName", ""),
                    "heroCardId": p.get("heroCardId", ""),
                    "heroName": p.get("heroName", ""),
                    "empty": False,
                }
                break
        if correct_player:
            break

    if not correct_player:
        return jsonify({"error": f"上一轮中未找到 accountIdLo={correct_lo}"}), 404

    # 找错误玩家位置
    wrong_idx = None
    for i, p in enumerate(players):
        if str(p.get("accountIdLo", "")) == wrong_lo:
            wrong_idx = i
            break

    if wrong_idx is None:
        return jsonify({"error": f"当前组中未找到 accountIdLo={wrong_lo}"}), 404

    old_name = players[wrong_idx].get("displayName") or players[wrong_idx].get("battleTag") or "?"
    new_name = correct_player["displayName"] or correct_player["battleTag"] or "?"

    db.tournament_groups.update_one(
        {"_id": group["_id"]},
        {"$set": {f"players.{wrong_idx}": correct_player}}
    )

    log.info(f"[swap-player] 管理员 {session.get('battleTag')} 替换 {group.get('tournamentName')} R{group.get('round')}G{group.get('groupIndex')}: {old_name} → {new_name}")

    return jsonify({
        "ok": True,
        "oldName": old_name,
        "newName": new_name,
        "groupIndex": group.get("groupIndex"),
        "round": group.get("round"),
    })
