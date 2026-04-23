"""管理员面板 API"""

import logging
from datetime import datetime, timedelta, UTC
from flask import Blueprint, jsonify, request, session

from db import get_db, to_iso_str, to_cst_str, GAME_TIMEOUT_MINUTES
from auth import _admin_required, is_super_admin, is_admin

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
        "accountIdLo": "",
        "verified": True,
        "verifiedAt": now_str,
        "createdAt": now_str,
    })
    log.info(f"管理员 {admin_tag} 手动添加选手: {battle_tag}")
    return jsonify({"ok": True, "battleTag": battle_tag, "displayName": display_name})
