"""
酒馆战棋联赛网站 — Demo
使用内嵌 mock 数据，无需 MongoDB
"""

from flask import Flask, render_template, jsonify, request
from datetime import datetime, timedelta
import time
import random

app = Flask(__name__)

_now_ts = time.time()

# ── Mock 数据 ──────────────────────────────────────────

PLAYERS = [
    {"battleTag": "衣锦夜行#5267", "displayName": "衣锦夜行", "accountIdLo": "1708070391",
     "totalPoints": 142, "totalGames": 20, "wins": 9, "avgPlacement": 2.8, "verified": True,
     "lastGameAt": "2026-04-07T22:15:00Z"},
    {"battleTag": "瓦莉拉#1337", "displayName": "瓦莉拉", "accountIdLo": "558901234",
     "totalPoints": 128, "totalGames": 18, "wins": 7, "avgPlacement": 3.1, "verified": True,
     "lastGameAt": "2026-04-07T21:50:00Z"},
    {"battleTag": "墨衣#7788", "displayName": "墨衣", "accountIdLo": "992341567",
     "totalPoints": 115, "totalGames": 22, "wins": 7, "avgPlacement": 3.5, "verified": True,
     "lastGameAt": "2026-04-07T21:30:00Z"},
    {"battleTag": "安德罗妮#4455", "displayName": "安德罗妮", "accountIdLo": "334567890",
     "totalPoints": 108, "totalGames": 16, "wins": 6, "avgPlacement": 3.2, "verified": True,
     "lastGameAt": "2026-04-07T21:00:00Z"},
    {"battleTag": "驴鸽#9900", "displayName": "驴鸽", "accountIdLo": "778123456",
     "totalPoints": 97, "totalGames": 19, "wins": 5, "avgPlacement": 3.8, "verified": True,
     "lastGameAt": "2026-04-07T20:45:00Z"},
    {"battleTag": "异灵术#2233", "displayName": "异灵术", "accountIdLo": "112233445",
     "totalPoints": 89, "totalGames": 15, "wins": 4, "avgPlacement": 4.0, "verified": True,
     "lastGameAt": "2026-04-07T20:30:00Z"},
    {"battleTag": "岛猫#6677", "displayName": "岛猫", "accountIdLo": "667788990",
     "totalPoints": 82, "totalGames": 17, "wins": 4, "avgPlacement": 4.2, "verified": False,
     "lastGameAt": "2026-04-07T20:00:00Z"},
    {"battleTag": "赤小兔#8899", "displayName": "赤小兔", "accountIdLo": "445566778",
     "totalPoints": 75, "totalGames": 14, "wins": 3, "avgPlacement": 4.5, "verified": True,
     "lastGameAt": "2026-04-07T19:30:00Z"},
    {"battleTag": "甜水七#1100", "displayName": "甜水七", "accountIdLo": "223344556",
     "totalPoints": 68, "totalGames": 13, "wins": 3, "avgPlacement": 4.8, "verified": False,
     "lastGameAt": "2026-04-07T19:00:00Z"},
    {"battleTag": "慕容清清#3344", "displayName": "慕容清清", "accountIdLo": "889900112",
     "totalPoints": 61, "totalGames": 12, "wins": 2, "avgPlacement": 5.0, "verified": True,
     "lastGameAt": "2026-04-07T18:30:00Z"},
    {"battleTag": "小呆萝拉#5566", "displayName": "小呆萝拉", "accountIdLo": "1722879111",
     "totalPoints": 53, "totalGames": 11, "wins": 2, "avgPlacement": 5.2, "verified": True,
     "lastGameAt": "2026-04-07T18:00:00Z"},
    {"battleTag": "王师傅#7788", "displayName": "王师傅", "accountIdLo": "169143705",
     "totalPoints": 45, "totalGames": 10, "wins": 1, "avgPlacement": 5.5, "verified": False,
     "lastGameAt": "2026-04-07T17:30:00Z"},
]

HEROES = ["伊瑟拉", "穆克拉", "苔丝·格雷迈恩", "雷诺·杰克逊", "帕奇维克",
          "拉卡尼休", "永恒者托奇", "阿莱克丝塔萨", "馆长", "尤格萨隆",
          "钩牙船长", "瓦丝琪女士", "凯瑞尔·罗姆", "阮大师", "死亡之翼"]

def _make_match(idx, ended_at):
    """生成一场对局"""
    sampled = random.sample(PLAYERS, min(8, len(PLAYERS)))
    players = []
    for rank, p in enumerate(sampled, 1):
        points = max(0, 9 - rank)
        players.append({
            "battleTag": p["battleTag"],
            "displayName": p["displayName"],
            "accountIdLo": p["accountIdLo"],
            "heroName": random.choice(HEROES),
            "placement": rank,
            "points": points,
        })
    return {
        "gameUuid": f"match-{idx:04d}-{random.randint(1000,9999)}",
        "players": players,
        "region": "CN",
        "mode": "solo",
        "endedAt": ended_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

now = datetime(2026, 4, 7, 22, 30)
MATCHES = []
for i in range(10):
    t = now - timedelta(hours=i * 0.5 + random.uniform(0, 0.3))
    MATCHES.append(_make_match(i + 1, t))

ACTIVE_GAMES = [
    {
        "gameUuid": "active-001",
        "players": [
            {"displayName": "衣锦夜行"},
            {"displayName": "墨衣"},
            {"displayName": "驴鸽"},
            {"displayName": "赤小兔"},
            {"displayName": "异灵术"},
            {"displayName": "岛猫"},
            {"displayName": "甜水七"},
            {"displayName": "慕容清清"},
        ],
        "startedAt": _now_ts - 522,  # 8分42秒前
    },
    {
        "gameUuid": "active-002",
        "players": [
            {"displayName": "瓦莉拉"},
            {"displayName": "安德罗妮"},
            {"displayName": "王师傅"},
            {"displayName": "小呆萝拉"},
            {"displayName": "衣锦夜行"},
            {"displayName": "墨衣"},
            {"displayName": "驴鸽"},
            {"displayName": "赤小兔"},
        ],
        "startedAt": _now_ts - 133,  # 2分13秒前
    },
]


# ── 页面路由 ──────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", players=PLAYERS, matches=MATCHES, active_games=ACTIVE_GAMES)


@app.route("/player/<battle_tag>")
def player_page(battle_tag):
    player = next((p for p in PLAYERS if p["battleTag"] == battle_tag), None)
    if not player:
        return "选手不存在", 404
    player_matches = []
    for m in MATCHES:
        for mp in m["players"]:
            if mp["battleTag"] == battle_tag:
                player_matches.append({
                    "gameUuid": m["gameUuid"],
                    "endedAt": m["endedAt"],
                    "heroName": mp["heroName"],
                    "placement": mp["placement"],
                    "points": mp["points"],
                })
    return render_template("player.html", player=player, matches=player_matches)


@app.route("/match/<game_uuid>")
def match_page(game_uuid):
    match = next((m for m in MATCHES if m["gameUuid"] == game_uuid), None)
    if not match:
        return "对局不存在", 404
    return render_template("match.html", match=match)


# ── API 路由 ──────────────────────────────────────────

@app.route("/api/players")
def api_players():
    sort = request.args.get("sort", "totalPoints")
    order = request.args.get("order", "desc")
    reverse = order == "desc"
    sorted_players = sorted(PLAYERS, key=lambda p: p.get(sort, 0), reverse=reverse)
    return jsonify(sorted_players)


@app.route("/api/matches")
def api_matches():
    return jsonify(MATCHES[:5])


@app.route("/api/active-games")
def api_active_games():
    return jsonify(ACTIVE_GAMES)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
