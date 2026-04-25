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
        {"$match": {"players.accountIdLo": {"$nin": ["", None, "None"]}, "players.points": {"$ne": None}}},
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


# ── 淘汰赛相关 ──────────────────────────────────────

def _sort_key_chicken(p):
    """吃鸡规则排序：总积分↓ → 吃鸡↓ → 最后一局排名↑"""
    return (-p.get("totalPoints", 0), -p.get("chickens", 0), p.get("lastGamePlacement", 999))


def _sort_key_golden(p):
    """黄金赛规则排序：总积分↓ → 单局最高分↓ → 最后一局分数↓"""
    return (-p.get("totalPoints", 0), -p.get("maxGamePoints", 0), -p.get("lastGamePoints", 0))


SORT_KEYS = {
    "chicken": _sort_key_chicken,
    "golden": _sort_key_golden,
}


def get_group_rankings(db, tournament_name=None, advancement_rule="chicken"):
    """从 league_matches 聚合淘汰赛各组排名数据"""
    match_filter = {"tournamentGroupId": {"$ne": None}}
    if tournament_name:
        # tournamentName 只存在 tournament_groups 中，先查出对应的 _id
        tg_ids = [g["_id"] for g in db.tournament_groups.find(
            {"tournamentName": tournament_name}, {"_id": 1}
        )]
        match_filter["tournamentGroupId"] = {"$in": tg_ids}

    pipeline = [
        {"$match": match_filter},
        {"$match": {"endedAt": {"$ne": None}}},
        {"$match": {"players": {"$not": {"$elemMatch": {"placement": None}}}}},
        {"$unwind": "$players"},
        {"$match": {"players.accountIdLo": {"$nin": ["", None, "None"]}}},
        {"$sort": {"tournamentGroupId": 1, "startedAt": 1}},
        {"$group": {
            "_id": {"tg": "$tournamentGroupId", "lo": "$players.accountIdLo"},
            "totalPoints": {"$sum": {"$ifNull": ["$players.points", 0]}},
            "gamesPlayed": {"$sum": 1},
            "games": {"$push": {"$ifNull": ["$players.points", 0]}},
            "placements": {"$push": "$players.placement"},
            "chickens": {"$sum": {"$cond": [{"$eq": ["$players.placement", 1]}, 1, 0]}},
            "lastPlacement": {"$last": "$players.placement"},
            "lastPoints": {"$last": {"$ifNull": ["$players.points", 0]}},
        }},
        {"$addFields": {
            "maxGamePoints": {"$max": "$games"},
        }},
    ]

    sort_fn = SORT_KEYS.get(advancement_rule, _sort_key_chicken)

    rankings = {}
    for doc in db.league_matches.aggregate(pipeline):
        tg_str = str(doc["_id"]["tg"])
        lo = str(doc["_id"]["lo"])
        if tg_str not in rankings:
            rankings[tg_str] = []
        rankings[tg_str].append({
            "accountIdLo": lo,
            "totalPoints": doc["totalPoints"],
            "gamesPlayed": doc["gamesPlayed"],
            "games": doc["games"],
            "chickens": doc["chickens"],
            "lastGamePlacement": doc["lastPlacement"] or 999,
            "maxGamePoints": doc.get("maxGamePoints", 0),
            "lastGamePoints": doc.get("lastPoints", 0),
        })

    result = {}
    for tg_str, players_list in rankings.items():
        players_list.sort(key=sort_fn)
        players_data = {}
        for i, p in enumerate(players_list):
            players_data[p["accountIdLo"]] = {
                "totalPoints": p["totalPoints"],
                "gamesPlayed": p["gamesPlayed"],
                "games": p["games"],
                "chickens": p["chickens"],
                "lastGamePlacement": p["lastGamePlacement"],
                "maxGamePoints": p["maxGamePoints"],
                "lastGamePoints": p["lastGamePoints"],
                "placement": i + 1,
                "qualified": i < 4,
                "eliminated": i >= 4,
            }
        result[tg_str] = players_data
    return result


def try_advance_group(db, tg):
    """单组完成时立即晋级：将前 4 名放入下一轮分组"""
    # grid 布局（海选赛）不自动晋级
    if tg.get("layout") == "grid":
        log.info(f"[advance] 跳过 grid 布局晋级: R{tg.get('round')}G{tg.get('groupIndex')}")
        return

    current_round = tg.get("round", 1)
    gi = tg.get("groupIndex", 1)
    tournament_name = tg.get("tournamentName", "赛事")
    tg_id = tg["_id"]
    advancement_rule = tg.get("advancementRule", "chicken")

    groups_in_round = db.tournament_groups.count_documents({
        "round": current_round, "tournamentName": tournament_name,
    })
    next_group_index = (gi + 1) // 2 if groups_in_round > 1 else 1

    group_rankings = get_group_rankings(db, tournament_name, advancement_rule)
    rankings = group_rankings.get(str(tg_id), {})

    def _rank_key(p):
        r = rankings.get(str(p.get("accountIdLo", "")), {})
        return sort_fn(r) if r else (0,)

    sort_fn = SORT_KEYS.get(advancement_rule, _sort_key_chicken)
    ranked_players = sorted(tg.get("players", []), key=_rank_key)

    quals = []
    for p in ranked_players[:4]:
        quals.append({
            "battleTag": p.get("battleTag", ""),
            "accountIdLo": p.get("accountIdLo", ""),
            "displayName": p.get("displayName", ""),
            "heroCardId": p.get("heroCardId", ""),
            "heroName": p.get("heroName", ""),
            "empty": False,
        })

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
        log.info(f"[advance] R{current_round}G{gi} → R{next_round}G{next_group_index}: 填入 {len(quals)} 人")
    else:
        all_players = quals + [{"battleTag": None, "accountIdLo": None, "displayName": "待定",
                                "heroCardId": None, "heroName": None, "empty": True}] * 4
        next_bo_n = tg.get("boN", 3)
        db.tournament_groups.insert_one({
            "tournamentName": tournament_name,
            "round": next_round,
            "groupIndex": next_group_index,
            "status": "waiting",
            "boN": next_bo_n,
            "advancementRule": advancement_rule,
            "gamesPlayed": 0,
            "players": all_players,
            "nextRoundGroupId": None,
            "startedAt": None,
            "endedAt": None,
        })
        log.info(f"[advance] R{current_round}G{gi} → 创建 R{next_round}G{next_group_index}: {len(quals)} 人晋级")


def try_advance_round(db, current_round, tournament_name, group_rankings=None):
    """检查当前轮次是否全部完成，如果是则创建下一轮分组"""
    round_groups = list(db.tournament_groups.find({
        "round": current_round,
        "tournamentName": tournament_name,
    }))
    if not round_groups:
        return
    # grid 布局（海选赛）不自动晋级
    if round_groups[0].get("layout") == "grid":
        return
    if not all(g.get("status") == "done" for g in round_groups):
        return

    next_round = current_round + 1
    existing = db.tournament_groups.count_documents({
        "round": next_round,
        "tournamentName": tournament_name,
    })
    if existing > 0:
        return

    if group_rankings is None:
        group_rankings = get_group_rankings(db, tournament_name, round_groups[0].get("advancementRule", "chicken"))
    buckets = {}
    for g in sorted(round_groups, key=lambda x: x.get("groupIndex", 0)):
        gi = g.get("groupIndex", 0)
        nrg = (gi + 1) // 2 if len(round_groups) > 1 else None
        tg_str = str(g["_id"])
        rankings = group_rankings.get(tg_str, {})
        advancement_rule = g.get("advancementRule", "chicken")
        sort_fn = SORT_KEYS.get(advancement_rule, _sort_key_chicken)

        def _rank_key(p, rk=rankings, fn=sort_fn):
            r = rk.get(str(p.get("accountIdLo", "")), {})
            return fn(r) if r else (0,)

        ranked_players = sorted(g.get("players", []), key=_rank_key)
        for p in ranked_players[:4]:
            buckets.setdefault(nrg, []).append({
                "battleTag": p.get("battleTag", ""),
                "accountIdLo": p.get("accountIdLo", ""),
                "displayName": p.get("displayName", ""),
                "heroCardId": p.get("heroCardId", ""),
                "heroName": p.get("heroName", ""),
                "empty": False,
            })

    for gid in sorted(buckets.keys()):
        players = buckets[gid]
        while len(players) < 8:
            players.append({"battleTag": None, "accountIdLo": None, "displayName": "待定",
                            "heroCardId": None, "heroName": None, "empty": True})
        next_nrg = (gid + 1) // 2 if len(buckets) > 1 else None
        src_bo_n = round_groups[0].get("boN", 1)
        src_rule = round_groups[0].get("advancementRule", "chicken")
        db.tournament_groups.insert_one({
            "tournamentName": tournament_name,
            "round": next_round,
            "groupIndex": gid,
            "status": "waiting",
            "boN": src_bo_n,
            "advancementRule": src_rule,
            "gamesPlayed": 0,
            "players": players,
            "nextRoundGroupId": next_nrg,
            "startedAt": None,
            "endedAt": None,
        })

    log.info(f"[advance] 第 {current_round} 轮全部完成，已创建第 {next_round} 轮分组 ({len(buckets)} 组)")
