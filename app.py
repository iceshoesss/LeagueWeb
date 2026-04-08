"""
酒馆战棋联赛网站
从 MongoDB 读取真实数据
"""

from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from datetime import datetime
from bson import datetime as bson_datetime
import time

app = Flask(__name__)

# ── MongoDB 连接 ────────────────────────────────────
MONGO_URL = "mongodb://YOUR_MONGO_HOST:27017"
DB_NAME = "hearthstone"

_client = None
_db = None


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
    """从 league_matches 聚合生成排行榜"""
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
            "totalGames": {"$sum": 1},
            "wins": {"$sum": {"$cond": [{"$eq": ["$players.placement", 1]}, 1, 0]}},
            "totalPlacement": {"$sum": "$players.placement"},
            "lastGameAt": {"$max": "$endedAt"},
        }},
        {"$addFields": {
            "avgPlacement": {"$divide": ["$totalPlacement", "$totalGames"]},
            "winRate": {"$divide": ["$wins", "$totalGames"]},
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
            "wins": p.get("wins", 0),
            "avgPlacement": round(p.get("avgPlacement", 0), 1),
            "winRate": p.get("winRate", 0),
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
    """获取进行中的对局（endedAt 为 null 或字段不存在）"""
    db = get_db()
    games = list(db.league_matches.find(
        {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]}
    ).sort("startedAt", -1))
    for g in games:
        g["_id"] = str(g["_id"])
        g["startedAtEpoch"] = to_epoch(g.get("startedAt"))
        # 统一时间格式
        g["startedAt"] = to_iso_str(g.get("startedAt"))
    return games


def get_player(battle_tag):
    """从 league_matches 聚合获取单个选手信息"""
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
            "totalGames": {"$sum": 1},
            "wins": {"$sum": {"$cond": [{"$eq": ["$players.placement", 1]}, 1, 0]}},
            "totalPlacement": {"$sum": "$players.placement"},
            "lastGameAt": {"$max": "$endedAt"},
        }},
    ]
    result = list(db.league_matches.aggregate(pipeline))
    if result:
        p = result[0]
        total_games = max(p.get("totalGames", 1), 1)
        return {
            "_id": str(p["_id"]),
            "battleTag": p["_id"],
            "displayName": p.get("displayName", ""),
            "accountIdLo": p.get("accountIdLo", ""),
            "verified": True,
            "totalPoints": p.get("totalPoints", 0),
            "totalGames": p.get("totalGames", 0),
            "wins": p.get("wins", 0),
            "avgPlacement": round(p.get("totalPlacement", 0) / total_games, 1),
            "winRate": p.get("wins", 0) / total_games,
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
