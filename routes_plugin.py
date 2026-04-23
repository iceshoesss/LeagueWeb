"""插件专用 API：upload-rating、check-league、update-placement（含淘汰赛匹配）"""

import hashlib
import logging
from datetime import datetime, UTC
from flask import Blueprint, jsonify, request

from db import get_db
from auth import (check_rate_limit, GAME_UUID_RE, PLUGIN_API_KEY, MIN_PLUGIN_VERSION,
                  _version_tuple)
from cleanup import cleanup_stale_queues

log = logging.getLogger("bgtracker")
plugin_bp = Blueprint("plugin", __name__)


def _generate_verification_code(oid):
    raw = f"bgtracker:{oid}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8].upper()


def _ensure_verification_code(db, player_id, account_id_lo="", mode="solo", region="CN", timestamp=None):
    if not player_id or player_id == "unknown":
        return None

    existing = db.player_records.find_one({"playerId": player_id})
    if existing:
        vc = existing.get("verificationCode")
        if account_id_lo and not existing.get("accountIdLo"):
            db.player_records.update_one(
                {"_id": existing["_id"]},
                {"$set": {"accountIdLo": account_id_lo}},
            )
        return vc

    if timestamp is None:
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {
        "playerId": player_id,
        "accountIdLo": account_id_lo,
        "rating": 0, "lastRating": 0, "ratingChange": 0,
        "mode": mode, "region": region, "timestamp": timestamp, "gameCount": 0,
    }
    result = db.player_records.insert_one(doc)
    vc = _generate_verification_code(result.inserted_id)
    db.player_records.update_one({"_id": result.inserted_id}, {"$set": {"verificationCode": vc}})
    return vc


@plugin_bp.route("/api/plugin/upload-rating", methods=["POST"])
def api_plugin_upload_rating():
    data = request.get_json() or {}
    player_id = data.get("playerId", "").strip()
    account_id_lo = data.get("accountIdLo", "").strip()
    rating = data.get("rating")
    mode = data.get("mode", "solo")
    region = data.get("region", "CN")

    if not player_id or player_id == "unknown":
        return jsonify({"error": "playerId 无效"}), 400
    if not isinstance(rating, (int, float)):
        return jsonify({"error": "rating 必须是数字"}), 400
    if mode not in ("solo", "duo"):
        return jsonify({"error": "mode 必须是 solo 或 duo"}), 400

    if not check_rate_limit(player_id):
        return jsonify({"error": "请求过于频繁，请稍后重试"}), 429

    db = get_db()
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing = db.player_records.find_one({"playerId": player_id})

    if existing:
        set_doc = {
            "lastRating": existing.get("rating", rating),
            "rating": rating,
            "ratingChange": rating - existing.get("rating", rating),
            "mode": mode, "region": region,
            "timestamp": now_str,
            "gameCount": existing.get("gameCount", 0) + 1,
        }
        if account_id_lo:
            set_doc["accountIdLo"] = account_id_lo
        db.player_records.update_one({"_id": existing["_id"]}, {"$set": set_doc})
        verification_code = existing.get("verificationCode")
    else:
        doc = {
            "playerId": player_id, "accountIdLo": account_id_lo,
            "rating": rating, "lastRating": rating, "ratingChange": 0,
            "mode": mode, "region": region, "timestamp": now_str, "gameCount": 1,
        }
        result = db.player_records.insert_one(doc)
        verification_code = _generate_verification_code(result.inserted_id)
        db.player_records.update_one({"_id": result.inserted_id}, {"$set": {"verificationCode": verification_code}})

    resp = {"ok": True}
    if verification_code:
        resp["verificationCode"] = verification_code
    return jsonify(resp)


@plugin_bp.route("/api/plugin/check-league", methods=["POST"])
def api_plugin_check_league():
    data = request.get_json() or {}
    game_uuid = data.get("gameUuid", "").strip()
    account_ids = set(str(a) for a in data.get("accountIdLoList", []))

    if not game_uuid or not account_ids:
        log.warning(f"[check-league] 400: 参数不完整 gameUuid={game_uuid!r} account_ids={len(account_ids)} playerId={data.get('playerId','')!r}")
        return jsonify({"error": "参数不完整"}), 400
    if all(a == "0" for a in account_ids):
        log.warning(f"[check-league] 400: accountIdLo 全为 0（LobbyInfo 未就绪）playerId={data.get('playerId','')!r}")
        return jsonify({"error": "LobbyInfo 未就绪"}), 400
    if not GAME_UUID_RE.match(game_uuid):
        log.warning(f"[check-league] 400: gameUuid 格式无效: {game_uuid!r}")
        return jsonify({"error": "gameUuid 格式无效"}), 400
    db = get_db()

    cleanup_stale_queues()

    # ── 淘汰赛 BO 系列赛匹配 ──
    active_tournament_groups = list(db.tournament_groups.find({
        "status": {"$in": ["waiting", "active"]},
        "$expr": {"$lt": ["$gamesPlayed", "$boN"]},
    }))
    game_los = {lo for lo in account_ids if lo and lo != "0"}
    log.info(f"[check-league] 候选淘汰赛组: {len(active_tournament_groups)} 个, 游戏Lo={sorted(game_los)} (含bot共{len(account_ids)}个)")
    matched_tournament_group = None
    for tg in active_tournament_groups:
        tg_los = set()
        for p in tg.get("players", []):
            lo = str(p.get("accountIdLo", ""))
            if lo and lo != "0" and lo.isdigit():  # 只取真实数字 Lo，跳过手机玩家伪 Lo
                tg_los.add(lo)
        lo_match = len(tg_los) >= 5 and tg_los.issubset(game_los)
        log.info(f"[check-league] 组 R{tg.get('round')}G{tg.get('groupIndex')} status={tg.get('status')} gp={tg.get('gamesPlayed')}/{tg.get('boN')} 组Lo={len(tg_los)} lo_match={lo_match}")
        if lo_match:
            matched_tournament_group = tg
            break

    if matched_tournament_group:
        detailed_players = data.get("players", {})
        players = []
        for p in matched_tournament_group.get("players", []):
            lo = str(p.get("accountIdLo", ""))
            detail = detailed_players.get(lo, {})
            players.append({
                "accountIdLo": lo,
                "battleTag": detail.get("battleTag", p.get("battleTag", "")),
                "displayName": detail.get("displayName", p.get("displayName", "")),
                "heroCardId": detail.get("heroCardId", ""),
                "heroName": detail.get("heroName", ""),
                "placement": None,
                "points": None,
            })

        mode = data.get("mode", "solo")
        region = data.get("region", "CN")
        started_at = data.get("startedAt", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))

        db.league_matches.update_one(
            {"gameUuid": game_uuid},
            {"$setOnInsert": {
                "players": players,
                "region": region,
                "mode": mode,
                "startedAt": started_at,
                "endedAt": None,
                "tournamentGroupId": matched_tournament_group["_id"],
                "tournamentRound": matched_tournament_group.get("round"),
            }},
            upsert=True,
        )

        game_num = matched_tournament_group.get("gamesPlayed", 0) + 1
        old_status = matched_tournament_group.get("status")
        db.tournament_groups.update_one(
            {"_id": matched_tournament_group["_id"]},
            {"$set": {"status": "active", "startedAt": started_at},
             "$inc": {"gamesPlayed": 1}}
        )
        log.info(f"[check-league] 淘汰赛匹配: group=R{matched_tournament_group.get('round')}G{matched_tournament_group.get('groupIndex')} gp={matched_tournament_group.get('gamesPlayed')}/{matched_tournament_group.get('boN')} {old_status}→active 第{game_num}局 gameUuid={game_uuid}")

        resp = {"isLeague": True}
        vc = _ensure_verification_code(db, player_id=data.get("playerId", "").strip(),
            account_id_lo=data.get("accountIdLo", "").strip(), mode=mode, region=region, timestamp=started_at)
        if vc:
            resp["verificationCode"] = vc
        return jsonify(resp)

    # ── 积分赛等待队列匹配 ──
    log.info(f"[check-league] 未匹配淘汰赛组，尝试积分赛等待队列")
    waiting_groups = list(db.league_waiting_queue.find().sort("createdAt", 1))
    matched_group = None

    for group in waiting_groups:
        queue_ids = set()
        has_all_ids = True
        for p in group.get("players", []):
            lo = str(p.get("accountIdLo", ""))
            if lo:
                queue_ids.add(lo)
            else:
                has_all_ids = False
                break
        if has_all_ids and len(account_ids) == len(queue_ids) and account_ids == queue_ids:
            matched_group = group
            break

    if matched_group is None:
        is_league = db.league_matches.find_one({"gameUuid": game_uuid}) is not None
        if not is_league:
            log.info(f"[check-league] 未匹配任何队列: gameUuid={game_uuid} isLeague=false")
        resp = {"isLeague": is_league}
        vc = _ensure_verification_code(db, player_id=data.get("playerId", "").strip(),
            account_id_lo=data.get("accountIdLo", "").strip(),
            mode=data.get("mode", "solo"), region=data.get("region", "CN"))
        if vc:
            resp["verificationCode"] = vc
        return jsonify(resp)

    db.league_waiting_queue.delete_one({"_id": matched_group["_id"]})

    detailed_players = data.get("players", {})
    group_los = [str(p.get("accountIdLo", "")) for p in matched_group.get("players", []) if p.get("accountIdLo")]
    tag_map = {}
    if group_los:
        for rec in db.player_records.find({"accountIdLo": {"$in": group_los}}, {"accountIdLo": 1, "playerId": 1}):
            if rec.get("accountIdLo") and rec.get("playerId"):
                tag_map[str(rec["accountIdLo"])] = rec["playerId"]

    players = []
    for p in matched_group.get("players", []):
        lo = str(p.get("accountIdLo", ""))
        detail = detailed_players.get(lo, {})
        queue_name = p.get("name", "")
        tag = tag_map.get(lo, "")
        bt = detail.get("battleTag") or queue_name or tag
        dn = detail.get("displayName") or queue_name or tag
        players.append({
            "accountIdLo": lo, "battleTag": bt, "displayName": dn,
            "heroCardId": detail.get("heroCardId", ""),
            "heroName": detail.get("heroName", ""),
            "placement": None, "points": None,
        })

    mode = data.get("mode", "solo")
    region = data.get("region", "CN")
    started_at = data.get("startedAt", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))

    db.league_matches.update_one(
        {"gameUuid": game_uuid},
        {"$setOnInsert": {
            "players": players, "region": region, "mode": mode,
            "startedAt": started_at, "endedAt": None,
        }},
        upsert=True,
    )

    resp = {"isLeague": True}
    vc = _ensure_verification_code(db, player_id=data.get("playerId", "").strip(),
        account_id_lo=data.get("accountIdLo", "").strip(), mode=mode, region=region, timestamp=started_at)
    if vc:
        resp["verificationCode"] = vc
    return jsonify(resp)


@plugin_bp.route("/api/plugin/update-placement", methods=["POST"])
def api_plugin_update_placement():
    data = request.get_json() or {}
    game_uuid = data.get("gameUuid", "").strip()
    account_id_lo = str(data.get("accountIdLo", ""))
    player_id = data.get("playerId", "")
    placement = data.get("placement")

    log.info(f"[update-placement] 收到请求: accountIdLo={account_id_lo} gameUuid={game_uuid} placement={placement} playerId={player_id}")

    if not game_uuid or not account_id_lo:
        return jsonify({"error": "参数不完整"}), 400
    if not GAME_UUID_RE.match(game_uuid):
        return jsonify({"error": "gameUuid 格式无效"}), 400
    if not isinstance(placement, int) or placement < 1 or placement > 8:
        return jsonify({"error": "placement 必须是 1-8 的整数"}), 400

    points = 9 if placement == 1 else max(1, 9 - placement)
    if points < 1 or points > 9:
        return jsonify({"error": f"积分计算异常: placement={placement} → points={points}"}), 400

    if player_id and not check_rate_limit(player_id):
        return jsonify({"error": "请求过于频繁，请稍后重试"}), 429

    db = get_db()
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if match is None:
        log.error(f"[update-placement] 未找到对局: gameUuid={game_uuid}")
        return jsonify({"error": "未找到对局"}), 404

    players = match.get("players", [])
    target_index = None
    for i, p in enumerate(players):
        if str(p.get("accountIdLo", "")) == account_id_lo:
            target_index = i
            if p.get("placement") is not None:
                log.warning(f"[update-placement] 重复提交: accountIdLo={account_id_lo} 已有 placement={p['placement']}")
                return jsonify({"error": "该玩家已提交过排名，不可重复提交"}), 409
            break

    if target_index is None:
        log.error(f"[update-placement] 玩家不在对局中: accountIdLo={account_id_lo} gameUuid={game_uuid}")
        return jsonify({"error": "该玩家不在此对局中"}), 404

    result = db.league_matches.update_one(
        {"gameUuid": game_uuid},
        {"$set": {
            f"players.{target_index}.placement": placement,
            f"players.{target_index}.points": points,
        }}
    )

    if result.modified_count == 0:
        log.error(f"[update-placement] 更新失败: accountIdLo={account_id_lo} index={target_index}")
        return jsonify({"error": "更新失败"}), 500

    log.info(f"[update-placement] 已更新: accountIdLo={account_id_lo} → players[{target_index}].placement={placement} points={points}")

    players[target_index]["placement"] = placement
    players[target_index]["points"] = points
    all_done = all(p.get("placement") is not None for p in players)

    if not all_done:
        null_indices = [i for i, p in enumerate(players) if p.get("placement") is None]
        if len(null_indices) == 1:
            used = {p["placement"] for p in players if p.get("placement") is not None}
            remaining = set(range(1, 9)) - used
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
                players[null_indices[0]]["placement"] = auto_placement
                players[null_indices[0]]["points"] = auto_points
                log.info(f"[update-placement] 自动推算: players[{null_indices[0]}] placement={auto_placement} points={auto_points}")
                all_done = True

    finalized = False
    if all_done:
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.league_matches.update_one(
            {"gameUuid": game_uuid},
            {"$set": {"endedAt": now_str}}
        )
        finalized = True
        log.info(f"[update-placement] 对局已结束: gameUuid={game_uuid}")

        # ── 淘汰赛 BO 累计 ──
        tg_id = match.get("tournamentGroupId")
        log.info(f"[update-placement] BO检查: tg_id={tg_id} type={type(tg_id).__name__}")
        if tg_id:
            from data import try_advance_group
            tg = db.tournament_groups.find_one({"_id": tg_id})
            if tg:
                bo_n = tg.get("boN", 1)
                old_gp = tg.get("gamesPlayed", 0)
                old_status = tg.get("status", "waiting")
                games_played = old_gp  # check-league 已 +1

                update_fields = {"gamesPlayed": games_played}
                if games_played >= bo_n:
                    update_fields["status"] = "done"
                    update_fields["endedAt"] = now_str
                    log.info(f"[update-placement] BO 完成: group=R{tg.get('round')}G{tg.get('groupIndex')} gp={old_gp}→{games_played}/{bo_n} {old_status}→done")
                    try_advance_group(db, tg)
                else:
                    update_fields["status"] = "waiting"
                    log.info(f"[update-placement] BO 进度: group=R{tg.get('round')}G{tg.get('groupIndex')} gp={old_gp}→{games_played}/{bo_n} {old_status}→waiting")

                result = db.tournament_groups.update_one({"_id": tg_id}, {"$set": update_fields})
                log.info(f"[update-placement] 组更新结果: matched={result.matched_count} modified={result.modified_count}")
            else:
                log.warning(f"[update-placement] tournament_group 不存在: tg_id={tg_id}")
        else:
            log.info(f"[update-placement] 非淘汰赛对局（无 tournamentGroupId）")

    return jsonify({"ok": True, "finalized": finalized})
