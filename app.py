"""
酒馆战棋联赛网站
从 MongoDB 读取真实数据
"""

from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from datetime import datetime, timedelta
from bson import datetime as bson_datetime
import time

app = Flask(__name__)

# 对局超时：超过此时间未结束的对局视为异常断线，自动标记结束
GAME_TIMEOUT_MINUTES = 25

# ── MongoDB 连接 ────────────────────────────────────
MONGO_URL = "mongodb://YOUR_MONGO_HOST:27017"
DB_NAME = "hearthstone"

_client = None
_db = None


@app.context_processor
def inject_counts():
    """每个页面自动注入进行中对局数和选手数"""
    try:
        db = get_db()
        cutoff = (datetime.utcnow() - timedelta(minutes=GAME_TIMEOUT_MINUTES)).isoformat() + "Z"
        active_count = db.league_matches.count_documents({
            "$and": [
                {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
                {"startedAt": {"$gte": cutoff}}
            ]
        })
        player_count = len(db.league_matches.distinct("players.battleTag",
            {"endedAt": {"$nin": [None]}}))
    except Exception:
        active_count = 0
        player_count = 0
    return {"active_game_count": active_count, "total_player_count": player_count}


def get_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(MONGO_URL)
        _db = _client[DB_NAME]
    return _db


# ── 数据查询 ────────────────────────────────────────

def to_epoch(dt_val):
    """安全地把各种格式的时间值转为 epoch 秒数"""
    if dt_val is None:
        return int(time.time())
    if isinstance(dt_val, datetime):
        return int(dt_val.timestamp())
    if isinstance(dt_val, bson_datetime.datetime):
        return int(dt_val.timestamp())
    # 字符串格式
    try:
        s = str(dt_val).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp())
    except Exception:
        return int(time.time())


def to_iso_str(dt_val):
    """安全地把各种格式的时间值转为 ISO 字符串"""
    if dt_val is None:
        return ""
    if isinstance(dt_val, (datetime, bson_datetime.datetime)):
        return dt_val.strftime("%Y-%m-%dT%H:%M:%S")
    return str(dt_val)


def get_players():
    """从 league_matches 聚合 + bg_ratings 获取排行榜"""
    db = get_db()
    pipeline = [
        {"$match": {"endedAt": {"$ne": None}}},
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
        {"$lookup": {
            "from": "bg_ratings",
            "localField": "_id",
            "foreignField": "playerId",
            "as": "rating",
        }},
        {"$addFields": {
            "totalGames": {"$ifNull": [{"$first": "$rating.gameCount"}, "$leagueGames"]},
            "avgPlacement": {"$divide": ["$totalPlacement", "$leagueGames"]},
            "winRate": {"$divide": ["$wins", "$leagueGames"]},
            "chickenRate": {"$divide": ["$chickens", "$leagueGames"]},
        }},
        {"$sort": {"totalPoints": -1}},
    ]

    players = []
    for p in db.league_matches.aggregate(pipeline):
        players.append({
            "_id": str(p["_id"]),
            "battleTag": p["_id"],
            "displayName": p.get("displayName", ""),
            "accountIdLo": p.get("accountIdLo", ""),
            "verified": True,
            "totalPoints": p.get("totalPoints", 0),
            "totalGames": p.get("totalGames", 0),
            "leagueGames": p.get("leagueGames", 0),
            "wins": p.get("wins", 0),
            "chickens": p.get("chickens", 0),
            "avgPlacement": round(p.get("avgPlacement", 0), 1),
            "winRate": p.get("winRate", 0),
            "chickenRate": p.get("chickenRate", 0),
            "lastGameAt": to_iso_str(p.get("lastGameAt")),
        })
    return players


def get_completed_matches(limit=10):
    """获取已完成的对局（endedAt 非 null）"""
    db = get_db()
    # endedAt 为 null 或字段不存在 = 进行中；非 null = 已完成
    matches = list(db.league_matches.find(
        {"endedAt": {"$nin": [None]}}
    ).sort("endedAt", -1).limit(limit))
    for m in matches:
        m["_id"] = str(m["_id"])
        # 统一时间格式为字符串
        m["endedAt"] = to_iso_str(m.get("endedAt"))
        m["startedAt"] = to_iso_str(m.get("startedAt"))
    return matches


def get_active_games():
    """获取进行中的对局（endedAt 为 null 或字段不存在，且未超时）"""
    db = get_db()
    # 每次查询时清理超时对局
    cleanup_stale_games()
    cutoff = (datetime.utcnow() - timedelta(minutes=GAME_TIMEOUT_MINUTES)).isoformat() + "Z"
    query = {
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$gte": cutoff}}
        ]
    }
    games = list(db.league_matches.find(query).sort("startedAt", -1))
    for g in games:
        g["_id"] = str(g["_id"])
        g["startedAtEpoch"] = to_epoch(g.get("startedAt"))
        g["startedAt"] = to_iso_str(g.get("startedAt"))
    return games


def cleanup_stale_games():
    """将超过超时时间的未结束对局标记为结束（endedAt 写入超时标记）"""
    db = get_db()
    cutoff = (datetime.utcnow() - timedelta(minutes=GAME_TIMEOUT_MINUTES)).isoformat() + "Z"
    query = {
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$lt": cutoff}}
        ]
    }
    result = db.league_matches.update_many(
        query,
        {"$set": {"endedAt": "TIMEOUT_" + datetime.utcnow().isoformat() + "Z"}}
    )
    if result.modified_count > 0:
        print(f"清理了 {result.modified_count} 个超时对局")


def get_player(battle_tag):
    """从 league_matches + bg_ratings 聚合获取单个选手信息"""
    db = get_db()
    pipeline = [
        {"$match": {"endedAt": {"$ne": None}, "players.battleTag": battle_tag}},
        {"$unwind": "$players"},
        {"$match": {"players.battleTag": battle_tag, "players.points": {"$ne": None}}},
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
        {"$lookup": {
            "from": "bg_ratings",
            "localField": "_id",
            "foreignField": "playerId",
            "as": "rating",
        }},
        {"$addFields": {
            "totalGames": {"$ifNull": [{"$first": "$rating.gameCount"}, "$leagueGames"]},
        }},
    ]
    result = list(db.league_matches.aggregate(pipeline))
    if result:
        p = result[0]
        league_games = max(p.get("leagueGames", 1), 1)
        wins = p.get("wins", 0)
        chickens = p.get("chickens", 0)
        return {
            "_id": str(p["_id"]),
            "battleTag": p["_id"],
            "displayName": p.get("displayName", ""),
            "accountIdLo": p.get("accountIdLo", ""),
            "verified": True,
            "totalPoints": p.get("totalPoints", 0),
            "totalGames": p.get("totalGames", 0),
            "leagueGames": p.get("leagueGames", 0),
            "wins": wins,
            "chickens": chickens,
            "avgPlacement": round(p.get("totalPlacement", 0) / league_games, 1),
            "winRate": wins / league_games,
            "chickenRate": chickens / league_games,
            "lastGameAt": to_iso_str(p.get("lastGameAt")),
        }
    return None


def get_player_matches(battle_tag):
    """获取某选手的所有对局记录"""
    db = get_db()
    # 查找 players 数组中包含该 battleTag 的已完成对局
    matches = list(db.league_matches.find(
        {
            "players.battleTag": battle_tag,
            "endedAt": {"$nin": [None]}
        }
    ).sort("endedAt", -1))

    result = []
    for m in matches:
        for p in m.get("players", []):
            if p.get("battleTag") == battle_tag:
                result.append({
                    "gameUuid": m["gameUuid"],
                    "endedAt": to_iso_str(m.get("endedAt")),
                    "heroName": p.get("heroName", ""),
                    "placement": p.get("placement"),
                    "points": p.get("points"),
                })
    return result


def get_match(game_uuid):
    """获取单场对局详情"""
    db = get_db()
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if match:
        match["_id"] = str(match["_id"])
        match["endedAt"] = to_iso_str(match.get("endedAt"))
        match["startedAt"] = to_iso_str(match.get("startedAt"))
    return match


# ── 页面路由 ──────────────────────────────────────────

@app.route("/")
def index():
    players = get_players()
    matches = get_completed_matches(limit=5)
    active_games = get_active_games()
    return render_template("index.html", players=players, matches=matches, active_games=active_games)


@app.route("/player/<path:battle_tag>")
def player_page(battle_tag):
    player = get_player(battle_tag)
    if not player:
        return "选手不存在", 404
    player_matches = get_player_matches(battle_tag)
    return render_template("player.html", player=player, matches=player_matches)


@app.route("/match/<game_uuid>")
def match_page(game_uuid):
    match = get_match(game_uuid)
    if not match:
        return "对局不存在", 404
    return render_template("match.html", match=match)


# ── API 路由 ──────────────────────────────────────────

@app.route("/api/players")
def api_players():
    return jsonify(get_players())


@app.route("/api/matches")
def api_matches():
    return jsonify(get_completed_matches(limit=10))


@app.route("/api/active-games")
def api_active_games():
    return jsonify(get_active_games())


# ── 报名队列 API ──────────────────────────────────────

@app.route("/api/queue")
def api_queue():
    """获取报名队列"""
    db = get_db()
    queue = list(db.league_queue.find().sort("joinedAt", 1))
    for q in queue:
        q["_id"] = str(q["_id"])
        q["joinedAt"] = to_iso_str(q.get("joinedAt"))
    return jsonify(queue)


@app.route("/api/queue/join", methods=["POST"])
def api_queue_join():
    """加入报名队列"""
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "名字不能为空"}), 400

    db = get_db()
    db.league_queue.update_one(
        {"name": name},
        {"$setOnInsert": {"name": name, "joinedAt": datetime.utcnow().isoformat() + "Z"}},
        upsert=True,
    )
    return jsonify({"ok": True, "name": name})


@app.route("/api/queue/leave", methods=["POST"])
def api_queue_leave():
    """退出报名队列"""
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "名字不能为空"}), 400

    db = get_db()
    db.league_queue.delete_one({"name": name})
    return jsonify({"ok": True, "name": name})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
