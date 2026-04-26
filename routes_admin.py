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

    # 淘汰赛对局：重算该组 rankings
    tg_id = match.get("tournamentGroupId")
    if tg_id:
        from data import recalc_group_rankings
        recalc_group_rankings(db, tg_id)

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

    # 淘汰赛对局：重算该组 rankings
    tg_id = match.get("tournamentGroupId")
    if tg_id:
        from data import recalc_group_rankings
        recalc_group_rankings(db, tg_id)

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

    from routes_tournament import invalidate_bracket_cache
    invalidate_bracket_cache()
    # 重算该组 rankings（标记 done 后确保缓存最新）
    from data import recalc_group_rankings
    recalc_group_rankings(db, oid)
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
            "startedAt": group.get("startedAt") or now_str,
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

    # 逐个更新玩家排名（跳过已有排名的）
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
            db.league_matches.update_one(
                {"gameUuid": game_uuid, "players.accountIdLo": lo},
                {"$set": {"players.$.placement": placement, "players.$.points": points}}
            )
            updated += 1

    if updated == 0:
        if skipped_locked > 0:
            return jsonify({"error": f"所有提交的玩家已有排名（已锁定 {skipped_locked} 人）"}), 400
        return jsonify({"error": "未匹配到任何玩家"}), 400

    # 7人提交 → 自动推算第8人
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if match:
        players = match.get("players", [])
        null_indices = [i for i, pl in enumerate(players) if pl.get("placement") is None]
        if len(null_indices) == 1:
            used = {pl["placement"] for pl in players if pl.get("placement") is not None}
            remaining = set(range(1, len(players) + 1)) - used
            if len(remaining) == 1:
                auto_placement = remaining.pop()
                auto_points = 9 if auto_placement == 1 else max(1, 9 - auto_placement)
                db.league_matches.update_one(
                    {"gameUuid": game_uuid},
                    {"$set": {f"players.{null_indices[0]}.placement": auto_placement, f"players.{null_indices[0]}.points": auto_points}}
                )
                log.info(f"[manual-record] 自动推算: players[{null_indices[0]}] placement={auto_placement}")

    # 重新读取最新状态
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    players = match.get("players", []) if match else []
    all_filled = all(pl.get("placement") is not None for pl in players)

    # 仅全部提交时才标记对局结束
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if all_filled:
        db.league_matches.update_one(
            {"gameUuid": game_uuid},
            {"$set": {"endedAt": match.get("endedAt") or now_str}, "$unset": {"status": ""}}
        )
        log.info(f"[manual-record] 全部排名已提交，对局结束 {game_uuid}")

    # BO 累计（仅全部提交时触发）
    if all_filled:
        bo_n = group.get("boN", 1)
        old_gp = group.get("gamesPlayed", 0)
        games_played = old_gp + 1
        update_fields = {"gamesPlayed": games_played}

        if games_played >= bo_n:
            update_fields["status"] = "done"
            update_fields["endedAt"] = now_str
            from data import try_advance_group
            try_advance_group(db, group)
            from routes_tournament import invalidate_bracket_cache
            invalidate_bracket_cache()
            log.info(f"[manual-record] BO 完成: gp={old_gp}→{games_played}/{bo_n}, 触发晋级")
        else:
            update_fields["status"] = "waiting"
            log.info(f"[manual-record] BO 进度: gp={old_gp}→{games_played}/{bo_n}")

        db.tournament_groups.update_one({"_id": oid}, {"$set": update_fields})

        # 事件驱动：重算该组 rankings
        from data import recalc_group_rankings
        recalc_group_rankings(db, oid)

    return jsonify({"ok": True, "gameUuid": game_uuid, "updated": updated, "skipped_locked": skipped_locked, "finalized": all_filled})

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

    # 淘汰赛对局：重算该组 rankings
    tg_id = match.get("tournamentGroupId")
    if tg_id:
        from data import recalc_group_rankings
        recalc_group_rankings(db, tg_id)

    return jsonify({"ok": True})
