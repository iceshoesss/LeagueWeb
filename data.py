"""数据查询：排行榜、选手、对局等"""

import logging
import time
from datetime import datetime, timedelta, UTC

from db import get_db, to_iso_str, to_cst_str, to_epoch, VALID_MATCH_FILTER, GAME_TIMEOUT_MINUTES

log = logging.getLogger("bgtracker")

# ── 排行榜缓存 ───────────────────────────────────────
_leaderboard_cache = {"data": None, "ts": 0}
LEADERBOARD_TTL = 30  # 秒


def get_players():
    """从 league_matches 聚合 + player_records 获取排行榜（带缓存）"""
    now = time.time()
    if _leaderboard_cache["data"] is not None and now - _leaderboard_cache["ts"] < LEADERBOARD_TTL:
        return _leaderboard_cache["data"]

    db = get_db()
    pipeline = [
        {"$match": {"$and": [{"endedAt": {"$ne": None}}, VALID_MATCH_FILTER]}},
        {"$unwind": "$players"},
        {"$match": {"players.points": {"$ne": None}}},
        {"$group": {
            "_id": "$players.battleTag",
            "displayName": {"$first": "$players.displayName"},
            "accountIdLo": {"$first": "$players.accountIdLo"},
            "totalPoints": {"$sum": "$players.points"},
            "leagueGames": {"$sum": 1},
            "wins": {"$sum": {"$cond": [{"$lte": ["$players.placement", 4]}, 1, 0]}},
            "chickens": {"$sum": {"$cond": [{"$eq": ["$players.placement", 1]}, 1, 0]}},
            "totalPlacement": {"$sum": "$players.placement"},
            "lastGameAt": {"$max": "$endedAt"},
        }},
        {"$addFields": {
            "avgPlacement": {"$divide": ["$totalPlacement", "$leagueGames"]},
            "winRate": {"$divide": ["$wins", "$leagueGames"]},
            "chickenRate": {"$divide": ["$chickens", "$leagueGames"]},
        }},
        {"$sort": {"totalPoints": -1}},
    ]

    raw_players = []
    for p in db.league_matches.aggregate(pipeline):
        raw_players.append({
            "_id": str(p["_id"]),
            "battleTag": p["_id"],
            "displayName": p.get("displayName", ""),
            "accountIdLo": p.get("accountIdLo", ""),
            "totalPoints": p.get("totalPoints", 0),
            "leagueGames": p.get("leagueGames", 0),
            "wins": p.get("wins", 0),
            "chickens": p.get("chickens", 0),
            "avgPlacement": round(p.get("avgPlacement", 0), 1),
            "winRate": p.get("winRate", 0),
            "chickenRate": p.get("chickenRate", 0),
            "lastGameAt": to_iso_str(p.get("lastGameAt")),
        })

    # 从 league_players 获取真实 battleTag（带 #tag）
    lo_ids = [p["accountIdLo"] for p in raw_players if p["accountIdLo"]]
    if lo_ids:
        tag_map = {}
        for lp in db.league_players.find({"accountIdLo": {"$in": lo_ids}}, {"accountIdLo": 1, "battleTag": 1}):
            if lp.get("accountIdLo") and lp.get("battleTag"):
                tag_map[str(lp["accountIdLo"])] = lp["battleTag"]
        for p in raw_players:
            real_tag = tag_map.get(p["accountIdLo"])
            if real_tag:
                p["_id"] = real_tag
                p["battleTag"] = real_tag

    _leaderboard_cache["data"] = raw_players
    _leaderboard_cache["ts"] = now
    return raw_players


def get_completed_matches(limit=10):
    """获取已完成的对局（endedAt 非 null，且所有玩家都有 placement）"""
    db = get_db()
    pipeline = [
        {"$match": {
            "$and": [
                {"endedAt": {"$nin": [None]}},
                VALID_MATCH_FILTER,
                {"players": {"$not": {"$elemMatch": {"placement": None}}}},
            ]
        }},
        {"$sort": {"endedAt": -1}},
        {"$limit": limit}
    ]
    matches = list(db.league_matches.aggregate(pipeline))
    for m in matches:
        m["_id"] = str(m["_id"])
        if m.get("tournamentGroupId"):
            m["tournamentGroupId"] = str(m["tournamentGroupId"])
        m["endedAt"] = to_iso_str(m.get("endedAt"))
        m["startedAt"] = to_iso_str(m.get("startedAt"))
        m["players"] = sorted(m.get("players", []), key=lambda p: p.get("placement") or 999)
    return matches


def get_active_games():
    """获取进行中的对局（endedAt 为 null 或字段不存在，且未超时）"""
    db = get_db()
    cutoff_str = (datetime.now(UTC) - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = {
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$gte": cutoff_str}}
        ]
    }
    games = list(db.league_matches.find(query).sort("startedAt", -1))
    for g in games:
        g["_id"] = str(g["_id"])
        if g.get("tournamentGroupId"):
            g["tournamentGroupId"] = str(g["tournamentGroupId"])
        g["startedAtEpoch"] = to_epoch(g.get("startedAt"))
        g["startedAt"] = to_iso_str(g.get("startedAt"))
    return games


def get_player(battle_tag):
    """从 league_matches + league_players 聚合获取单个选手信息"""
    db = get_db()

    lp = db.league_players.find_one({"battleTag": battle_tag})
    if not lp:
        lp = db.league_players.find_one({"displayName": battle_tag})
    real_battle_tag = lp.get("battleTag", battle_tag) if lp else battle_tag
    account_id_lo = str(lp["accountIdLo"]) if lp and lp.get("accountIdLo") else None

    if account_id_lo:
        match_cond = {"players.accountIdLo": account_id_lo}
        inner_match = {"players.accountIdLo": account_id_lo, "players.points": {"$ne": None}}
        group_id = "$players.accountIdLo"
    else:
        match_cond = {"players.battleTag": battle_tag}
        inner_match = {"players.battleTag": battle_tag, "players.points": {"$ne": None}}
        group_id = "$players.battleTag"

    pipeline = [
        {"$match": {"$and": [{"endedAt": {"$ne": None}}, VALID_MATCH_FILTER, match_cond]}},
        {"$unwind": "$players"},
        {"$match": inner_match},
        {"$group": {
            "_id": group_id,
            "displayName": {"$first": "$players.displayName"},
            "accountIdLo": {"$first": "$players.accountIdLo"},
            "totalPoints": {"$sum": "$players.points"},
            "leagueGames": {"$sum": 1},
            "wins": {"$sum": {"$cond": [{"$lte": ["$players.placement", 4]}, 1, 0]}},
            "chickens": {"$sum": {"$cond": [{"$eq": ["$players.placement", 1]}, 1, 0]}},
            "totalPlacement": {"$sum": "$players.placement"},
            "lastGameAt": {"$max": "$endedAt"},
        }},
        {"$addFields": {
            "avgPlacement": {"$divide": ["$totalPlacement", "$leagueGames"]},
            "winRate": {"$divide": ["$wins", "$leagueGames"]},
            "chickenRate": {"$divide": ["$chickens", "$leagueGames"]},
        }},
    ]
    result = list(db.league_matches.aggregate(pipeline))
    if result:
        p = result[0]
        return {
            "_id": real_battle_tag,
            "battleTag": real_battle_tag,
            "displayName": p.get("displayName", ""),
            "accountIdLo": p.get("accountIdLo", ""),
            "totalPoints": p.get("totalPoints", 0),
            "leagueGames": p.get("leagueGames", 0),
            "wins": p.get("wins", 0),
            "chickens": p.get("chickens", 0),
            "avgPlacement": round(p.get("avgPlacement", 0), 1),
            "winRate": p.get("winRate", 0),
            "chickenRate": p.get("chickenRate", 0),
            "lastGameAt": to_iso_str(p.get("lastGameAt")),
        }
    return None


def get_rival_stats(battle_tag, account_id_lo=None):
    """用聚合管道计算最软的虾和最硬的鸭"""
    db = get_db()
    match_key = "players.accountIdLo" if account_id_lo else "players.battleTag"
    match_val = account_id_lo if account_id_lo else battle_tag
    pipeline = [
        {"$match": {
            "$and": [
                {match_key: match_val},
                {"endedAt": {"$ne": None}},
                VALID_MATCH_FILTER,
            ]
        }},
        {"$project": {
            "players.battleTag": 1,
            "players.accountIdLo": 1,
            "players.placement": 1,
            "players.displayName": 1
        }},
        {"$unwind": "$players"},
        {"$group": {
            "_id": "$_id",
            "myPlacement": {"$max": {"$cond": [
                {"$eq": ["$players.accountIdLo" if account_id_lo else "$players.battleTag", match_val]},
                "$players.placement",
                None
            ]}},
            "opponents": {"$push": {
                "name": "$players.displayName",
                "placement": "$players.placement",
                "isMe": {"$eq": ["$players.accountIdLo" if account_id_lo else "$players.battleTag", match_val]}
            }}
        }},
        {"$project": {
            "myPlacement": 1,
            "opponents": {"$filter": {
                "input": "$opponents",
                "as": "p",
                "cond": {"$eq": ["$$p.isMe", False]}
            }}
        }},
        {"$unwind": "$opponents"},
        {"$match": {
            "myPlacement": {"$ne": None},
            "opponents.placement": {"$ne": None}
        }},
        {"$addFields": {
            "belowMe": {"$gt": ["$opponents.placement", "$myPlacement"]},
            "aboveMe": {"$lt": ["$opponents.placement", "$myPlacement"]}
        }},
        {"$group": {
            "_id": "$opponents.name",
            "belowCount": {"$sum": {"$cond": ["$belowMe", 1, 0]}},
            "aboveCount": {"$sum": {"$cond": ["$aboveMe", 1, 0]}}
        }}
    ]

    results = list(db.league_matches.aggregate(pipeline))

    softest = None
    hardest = None
    for r in results:
        if r["belowCount"] > 0 and (not softest or r["belowCount"] > softest["count"]):
            softest = {"name": r["_id"], "count": r["belowCount"]}
        if r["aboveCount"] > 0 and (not hardest or r["aboveCount"] > hardest["count"]):
            hardest = {"name": r["_id"], "count": r["aboveCount"]}

    return {
        "softestShrimp": softest,
        "hardestDuck": hardest,
    }


def get_player_matches(battle_tag, account_id_lo=None):
    """获取某选手的所有对局记录"""
    db = get_db()
    match_key = "players.accountIdLo" if account_id_lo else "players.battleTag"
    match_val = account_id_lo if account_id_lo else battle_tag
    pipeline = [
        {"$match": {
            "$and": [
                {match_key: match_val},
                {"endedAt": {"$nin": [None]}},
                VALID_MATCH_FILTER,
            ]
        }},
        {"$sort": {"endedAt": -1}},
        {"$unwind": "$players"},
        {"$match": {match_key: match_val}},
        {"$project": {
            "gameUuid": 1,
            "endedAt": 1,
            "heroCardId": "$players.heroCardId",
            "heroName": "$players.heroName",
            "placement": "$players.placement",
            "points": "$players.points",
            "status": {"$ifNull": ["$status", "completed"]},
        }}
    ]
    result = []
    for m in db.league_matches.aggregate(pipeline):
        result.append({
            "gameUuid": m["gameUuid"],
            "endedAt": to_iso_str(m.get("endedAt")),
            "heroCardId": m.get("heroCardId", ""),
            "heroName": m.get("heroName", ""),
            "placement": m.get("placement"),
            "points": m.get("points"),
            "status": m.get("status", "completed"),
        })
    return result


def get_match(game_uuid):
    """获取单场对局详情"""
    db = get_db()
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if match:
        match["_id"] = str(match["_id"])
        if match.get("tournamentGroupId"):
            match["tournamentGroupId"] = str(match["tournamentGroupId"])
        match["endedAt"] = to_iso_str(match.get("endedAt"))
        match["startedAt"] = to_iso_str(match.get("startedAt"))
        match["players"] = sorted(match.get("players", []), key=lambda p: p.get("placement") or 999)
    return match


def get_problem_matches():
    """获取所有有问题的对局"""
    db = get_db()
    pipeline = [
        {"$match": {
            "endedAt": {"$nin": [None]},
            "$or": [
                {"status": {"$in": ["timeout", "abandoned"]}},
                {"$and": [
                    {"status": {"$exists": False}},
                    {"players": {"$elemMatch": {"placement": None}}}
                ]}
            ]
        }},
        {"$sort": {"endedAt": -1}}
    ]
    matches = list(db.league_matches.aggregate(pipeline))
    for m in matches:
        m["_id"] = str(m["_id"])
        if m.get("tournamentGroupId"):
            m["tournamentGroupId"] = str(m["tournamentGroupId"])
        m["endedAt"] = to_iso_str(m.get("endedAt"))
        m["startedAt"] = to_iso_str(m.get("startedAt"))
        m["matchId"] = (m.get("gameUuid") or "")[:8].upper()
        m["players"] = sorted(m.get("players", []), key=lambda p: p.get("placement") or 999)
        for p in m.get("players", []):
            p["hasPlacement"] = p.get("placement") is not None
    return matches
