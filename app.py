"""
酒馆战棋联赛网站
从 MongoDB 读取真实数据
"""

from flask import Flask, render_template, jsonify, request
from pymongo import MongoClient
from datetime import datetime
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

def get_players():
    """从 league_players 获取排行榜数据"""
    db = get_db()
    players = list(db.league_players.find().sort("totalPoints", -1))
    for p in players:
        p["_id"] = str(p["_id"])
        if p.get("totalGames", 0) > 0:
            p["winRate"] = p.get("wins", 0) / p["totalGames"]
        else:
            p["winRate"] = 0
    return players


def get_completed_matches(limit=10):
    """获取已完成的对局（endedAt 非 null）"""
    db = get_db()
    matches = list(db.league_matches.find(
        {"endedAt": {"$ne": None}}
    ).sort("endedAt", -1).limit(limit))
    for m in matches:
        m["_id"] = str(m["_id"])
    return matches


def get_active_games():
    """获取进行中的对局（endedAt 为 null）"""
    db = get_db()
    games = list(db.league_matches.find(
        {"endedAt": None}
    ).sort("startedAt", -1))
    for g in games:
        g["_id"] = str(g["_id"])
        # 转换 startedAt 为 epoch 秒数，供前端 JS 计时器使用
        try:
            dt = datetime.fromisoformat(g["startedAt"].replace("Z", "+00:00"))
            g["startedAtEpoch"] = int(dt.timestamp())
        except Exception:
            g["startedAtEpoch"] = int(time.time())
    return games


def get_player(battle_tag):
    """获取单个选手信息"""
    db = get_db()
    return db.league_players.find_one({"battleTag": battle_tag})


def get_player_matches(battle_tag):
    """获取某选手的所有对局记录"""
    db = get_db()
    # 查找 players 数组中包含该 battleTag 的已完成对局
    matches = list(db.league_matches.find(
        {
            "players.battleTag": battle_tag,
            "endedAt": {"$ne": None}
        }
    ).sort("endedAt", -1))

    result = []
    for m in matches:
        for p in m.get("players", []):
            if p.get("battleTag") == battle_tag:
                result.append({
                    "gameUuid": m["gameUuid"],
                    "endedAt": m.get("endedAt", ""),
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
