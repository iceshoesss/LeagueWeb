"""
酒馆战棋联赛网站
从 MongoDB 读取真实数据
"""

from flask import Flask, render_template, jsonify, request, session, redirect, url_for, Response
from pymongo import MongoClient
from datetime import datetime, timedelta
from bson import datetime as bson_datetime
import hashlib
import time
import json

try:
    from gevent import sleep as gsleep
except ImportError:
    from time import sleep as gsleep

app = Flask(__name__)
app.secret_key = "bgtracker-flask-secret-2026-hearthstone"


# 对局超时：超过此时间未结束的对局视为异常断线，自动标记结束
GAME_TIMEOUT_MINUTES = 80

# ── MongoDB 连接 ────────────────────────────────────
MONGO_URL = "mongodb://YOUR_MONGO_HOST:27017"
DB_NAME = "hearthstone"

_client = None
_db = None
_last_cleanup_ts = 0

# ── 排行榜缓存 ───────────────────────────────────────
_leaderboard_cache = {"data": None, "ts": 0}
LEADERBOARD_TTL = 30  # 秒


@app.context_processor
def inject_counts():
    """每个页面自动注入进行中对局数、选手数、当前登录用户"""
    try:
        db = get_db()
        cutoff_str = (datetime.utcnow() - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
        active_count = db.league_matches.count_documents({
            "$and": [
                {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
                {"startedAt": {"$gte": cutoff_str}}
            ]
        })
        player_count = db.league_players.count_documents({"verified": True})
    except Exception as e:
        print(f"[inject_counts] 数据库查询失败: {e}")
        active_count = 0
        player_count = 0

    # 当前登录用户
    current_user = None
    battle_tag = session.get("battleTag")
    if battle_tag:
        current_user = {"battleTag": battle_tag, "displayName": session.get("displayName", battle_tag)}

    return {
        "active_game_count": active_count,
        "total_player_count": player_count,
        "current_user": current_user,
    }


def get_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(
            MONGO_URL,
            maxPoolSize=50,
            minPoolSize=5,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
        )
        _db = _client[DB_NAME]
    return _db


# ── 数据查询 ────────────────────────────────────────

def to_epoch(dt_val):
    """安全地把各种格式的时间值转为 epoch 秒数（统一按 UTC 处理）"""
    if dt_val is None:
        return int(time.time())
    if isinstance(dt_val, (datetime, bson_datetime.datetime)):
        # 如果是 naive datetime，明确视为 UTC
        if dt_val.tzinfo is None:
            from datetime import timezone
            dt_val = dt_val.replace(tzinfo=timezone.utc)
        return int(dt_val.timestamp())
    # 字符串格式
    try:
        s = str(dt_val)
        if s.endswith("Z"):
            s = s[:-1]  # 去掉 Z
        from datetime import timezone
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return int(time.time())


def to_iso_str(dt_val):
    """安全地把各种格式的时间值转为 ISO 字符串（UTC）"""
    if dt_val is None:
        return ""
    if isinstance(dt_val, (datetime, bson_datetime.datetime)):
        return dt_val.strftime("%Y-%m-%dT%H:%M:%S")
    return str(dt_val)


def to_cst_str(dt_val):
    """安全地把各种格式的时间值转为中国时间 (UTC+8) 字符串"""
    from datetime import timezone as tz
    if dt_val is None:
        return ""
    if isinstance(dt_val, (datetime, bson_datetime.datetime)):
        if dt_val.tzinfo is None:
            dt_val = dt_val.replace(tzinfo=tz.utc)
        cst = dt_val + timedelta(hours=8)
        return cst.strftime("%Y-%m-%d %H:%M")
    # 字符串格式
    try:
        s = str(dt_val)
        if s.endswith("Z"):
            s = s[:-1]
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz.utc)
        cst = dt + timedelta(hours=8)
        return cst.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt_val)

app.jinja_env.filters['cst'] = to_cst_str


def get_players():
    """从 league_matches 聚合 + bg_ratings 获取排行榜（带缓存）"""
    now = time.time()
    if _leaderboard_cache["data"] is not None and now - _leaderboard_cache["ts"] < LEADERBOARD_TTL:
        return _leaderboard_cache["data"]

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
        {"$addFields": {
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
            "totalPoints": p.get("totalPoints", 0),
            "leagueGames": p.get("leagueGames", 0),
            "wins": p.get("wins", 0),
            "chickens": p.get("chickens", 0),
            "avgPlacement": round(p.get("avgPlacement", 0), 1),
            "winRate": p.get("winRate", 0),
            "chickenRate": p.get("chickenRate", 0),
            "lastGameAt": to_iso_str(p.get("lastGameAt")),
        })

    _leaderboard_cache["data"] = players
    _leaderboard_cache["ts"] = now
    return players


def get_completed_matches(limit=10):
    """获取已完成的对局（endedAt 非 null，且所有玩家都有 placement）"""
    db = get_db()
    # 用聚合管道精确过滤：排除超时/掉线等不完整的对局
    # $not + $elemMatch: 确保没有 placement 为 null 的玩家
    pipeline = [
        {"$match": {
            "endedAt": {"$nin": [None]},
            "status": {"$exists": False},
            "players": {"$not": {"$elemMatch": {"placement": None}}}
        }},
        {"$sort": {"endedAt": -1}},
        {"$limit": limit}
    ]
    matches = list(db.league_matches.aggregate(pipeline))
    for m in matches:
        m["_id"] = str(m["_id"])
        m["endedAt"] = to_iso_str(m.get("endedAt"))
        m["startedAt"] = to_iso_str(m.get("startedAt"))
        # 按排名排序（1-8）
        m["players"] = sorted(m.get("players", []), key=lambda p: p.get("placement") or 999)
    return matches


def get_active_games():
    """获取进行中的对局（endedAt 为 null 或字段不存在，且未超时）"""
    global _last_cleanup_ts
    db = get_db()
    # 每 60 秒清理一次，避免每 5 秒轮询都跑 cleanup
    now = time.time()
    if now - _last_cleanup_ts > 60:
        _last_cleanup_ts = now
        cleanup_stale_games()
        cleanup_partial_matches()
    cutoff_str = (datetime.utcnow() - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    query = {
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$gte": cutoff_str}}
        ]
    }
    games = list(db.league_matches.find(query).sort("startedAt", -1))
    for g in games:
        g["_id"] = str(g["_id"])
        g["startedAtEpoch"] = to_epoch(g.get("startedAt"))
        g["startedAt"] = to_iso_str(g.get("startedAt"))
    return games


def cleanup_stale_games():
    """将超过超时时间的未结束对局标记为超时结束"""
    db = get_db()
    cutoff_str = (datetime.utcnow() - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    query = {
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$lt": cutoff_str}}
        ]
    }
    result = db.league_matches.update_many(
        query,
        {"$set": {
            "endedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "timeout"
        }}
    )
    if result.modified_count > 0:
        print(f"清理了 {result.modified_count} 个超时对局")


def cleanup_partial_matches():
    """
    处理部分掉线导致永不结束的对局：
    有人填了 placement 但超过超时时间仍未全部填完的对局，
    将未填的玩家标记为 placement=null, status="abandoned"，
    并写入 endedAt 让对局结束。
    """
    db = get_db()
    cutoff_str = (datetime.utcnow() - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    query = {
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$lt": cutoff_str}},
            {"status": {"$exists": False}},  # 不重复处理已标记的
        ]
    }
    matches = list(db.league_matches.find(query))
    if not matches:
        return

    count = 0
    for m in matches:
        players = m.get("players", [])
        has_any_placement = any(p.get("placement") is not None for p in players)
        if not has_any_placement:
            continue  # 纯超时局，由 cleanup_stale_games 处理

        # 有人填了但没全填 → 部分掉线
        db.league_matches.update_one(
            {"_id": m["_id"]},
            {"$set": {
                "endedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "abandoned"
            }}
        )
        count += 1

    if count > 0:
        print(f"标记了 {count} 个部分掉线对局")


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
        }
    return None


def get_rival_stats(battle_tag):
    """用聚合管道计算最软的虾和最硬的鸭"""
    db = get_db()
    pipeline = [
        {"$match": {
            "players.battleTag": battle_tag,
            "endedAt": {"$ne": None},
            "status": {"$exists": False}
        }},
        {"$project": {
            "players.battleTag": 1,
            "players.placement": 1,
            "players.displayName": 1
        }},
        {"$unwind": "$players"},
        {"$group": {
            "_id": "$_id",
            "myPlacement": {"$max": {"$cond": [
                {"$eq": ["$players.battleTag", battle_tag]},
                "$players.placement",
                None
            ]}},
            "opponents": {"$push": {
                "name": "$players.displayName",
                "placement": "$players.placement",
                "isMe": {"$eq": ["$players.battleTag", battle_tag]}
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


def get_player_matches(battle_tag):
    """获取某选手的所有对局记录（含超时/中断对局，标记 status）"""
    db = get_db()
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
                    "heroCardId": p.get("heroCardId", ""),
                    "heroName": p.get("heroName", ""),
                    "placement": p.get("placement"),
                    "points": p.get("points"),
                    "status": m.get("status", "completed"),
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
        # 按排名排序（null 排最后）
        match["players"] = sorted(match.get("players", []), key=lambda p: p.get("placement") or 999)
    return match


def get_problem_matches():
    """获取所有有问题的对局（timeout / abandoned / 旧数据中 placement 为 null 的已结束对局）"""
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
        m["endedAt"] = to_iso_str(m.get("endedAt"))
        m["startedAt"] = to_iso_str(m.get("startedAt"))
        # 比赛编号：gameUuid 前 8 位
        m["matchId"] = (m.get("gameUuid") or "")[:8].upper()
        # 按排名排序（null 排最后）
        m["players"] = sorted(m.get("players", []), key=lambda p: p.get("placement") or 999)
        # 标记每个玩家是否有 placement
        for p in m.get("players", []):
            p["hasPlacement"] = p.get("placement") is not None
    return matches


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
        return render_template("404.html", title="选手不存在", emoji="🔍",
            message=f"没有找到「{battle_tag}」的记录，可能还没有注册或打过联赛"), 404
    player_matches = get_player_matches(battle_tag)
    rival_stats = get_rival_stats(battle_tag)
    return render_template("player.html", player=player, matches=player_matches, rival=rival_stats)


@app.route("/match/<game_uuid>")
def match_page(game_uuid):
    match = get_match(game_uuid)
    if not match:
        return render_template("404.html", title="对局不存在", emoji="⚔️",
            message="这局对局可能从未发生过，或者数据已被清理"), 404
    return render_template("match.html", match=match)


@app.route("/match/<game_uuid>/edit")
def match_edit_page(game_uuid):
    match = get_match(game_uuid)
    if not match:
        return render_template("404.html", title="对局不存在", emoji="⚔️",
            message="这局对局可能从未发生过，或者数据已被清理"), 404
    # 判断是否问题对局：有玩家 placement 为 null
    is_problem = any(p.get("placement") is None for p in match.get("players", []))
    if not is_problem:
        return redirect(url_for("match_page", game_uuid=game_uuid))
    return render_template("match_edit.html", match=match)


@app.route("/register")
def register_page():
    return render_template("register.html")


@app.route("/problems")
def problems_page():
    matches = get_problem_matches()
    return render_template("problems.html", matches=matches)


# ── API 路由 ──────────────────────────────────────────

@app.route("/api/players")
def api_players():
    return jsonify(get_players())


@app.route("/api/players/<path:battle_tag>")
def api_player(battle_tag):
    player = get_player(battle_tag)
    if not player:
        return jsonify({"error": "选手不存在"}), 404
    return jsonify(player)


@app.route("/api/match/<game_uuid>")
def api_match(game_uuid):
    match = get_match(game_uuid)
    if not match:
        return jsonify({"error": "对局不存在"}), 404
    return jsonify(match)


@app.route("/api/matches")
def api_matches():
    return jsonify(get_completed_matches(limit=10))


@app.route("/api/active-games")
def api_active_games():
    return jsonify(get_active_games())


@app.route("/api/match/<game_uuid>/update-placement", methods=["POST"])
def api_update_placement(game_uuid):
    """手动补录对局排名"""
    data = request.get_json() or {}
    placements = data.get("placements", {})  # {accountIdLo: placement}

    if not placements:
        return jsonify({"error": "未提供排名数据"}), 400

    db = get_db()
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if not match:
        return jsonify({"error": "对局不存在"}), 404

    # 验证：8 个玩家排名 1-8 不重复
    values = list(placements.values())
    if len(values) != 8:
        return jsonify({"error": "需要 8 个玩家的排名"}), 400
    if sorted(values) != list(range(1, 9)):
        return jsonify({"error": "排名必须是 1-8 各出现一次"}), 400

    # 逐个更新 players 数组中对应玩家的 placement 和 points
    players = match.get("players", [])
    updated = 0
    for p in players:
        lo = str(p.get("accountIdLo", ""))
        if lo in placements:
            placement = placements[lo]
            points = 9 if placement == 1 else max(1, 9 - placement)

            db.league_matches.update_one(
                {"gameUuid": game_uuid, "players.accountIdLo": lo},
                {"$set": {
                    "players.$.placement": placement,
                    "players.$.points": points
                }}
            )
            updated += 1

    if updated == 0:
        return jsonify({"error": "未匹配到任何玩家"}), 400

    # 写入 endedAt（如果还没有）并去掉 status 标记
    db.league_matches.update_one(
        {"gameUuid": game_uuid},
        {"$set": {"endedAt": match.get("endedAt") or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")},
         "$unset": {"status": ""}}
    )

    return jsonify({"ok": True, "updated": updated})


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


@app.route("/api/waiting-queue")
def api_waiting_queue():
    """获取等待队列（每满N人创建一个独立组）"""
    db = get_db()
    groups = list(db.league_waiting_queue.find().sort("createdAt", 1))
    for g in groups:
        g["_id"] = str(g["_id"])
        g["createdAt"] = to_iso_str(g.get("createdAt"))
    return jsonify(groups)


@app.route("/api/queue/join", methods=["POST"])
def api_queue_join():
    """加入报名队列，优先补入未满的等待组"""
    name = session.get("battleTag") or session.get("displayName", "")
    if not name:
        return jsonify({"error": "请先登录"}), 401

    db = get_db()

    # 不能重复报名或已在等待组中
    if db.league_queue.find_one({"name": name}):
        return jsonify({"error": "已在报名队列中"}), 400
    if db.league_waiting_queue.find_one({"players.name": name}):
        return jsonify({"error": "已在等待队列中"}), 400

    # 优先补入未满的等待组
    incomplete_group = None
    for g in db.league_waiting_queue.find().sort("createdAt", 1):
        if len(g.get("players", [])) < 8:
            incomplete_group = g
            break

    if incomplete_group:
        db.league_waiting_queue.update_one(
            {"_id": incomplete_group["_id"]},
            {"$push": {"players": {"name": name}}}
        )
        return jsonify({"ok": True, "name": name, "moved": True})

    # 没有未满的组，加入报名队列
    db.league_queue.update_one(
        {"name": name},
        {"$setOnInsert": {"name": name, "joinedAt": datetime.utcnow().isoformat() + "Z"}},
        upsert=True,
    )

    # 检查是否满N人
    signup_count = db.league_queue.count_documents({})
    if signup_count >= 8:
        signup = list(db.league_queue.find().sort("joinedAt", 1).limit(8))
        players = [{"name": p["name"]} for p in signup]
        names = [p["name"] for p in signup]
        db.league_waiting_queue.insert_one({
            "players": players,
            "createdAt": datetime.utcnow().isoformat() + "Z",
        })
        db.league_queue.delete_many({"name": {"$in": names}})
        return jsonify({"ok": True, "name": name, "moved": True})

    return jsonify({"ok": True, "name": name, "moved": False})


@app.route("/api/queue/leave", methods=["POST"])
def api_queue_leave():
    """退出报名队列或等待队列"""
    name = session.get("battleTag") or session.get("displayName", "")
    if not name:
        return jsonify({"error": "请先登录"}), 401

    db = get_db()
    # 从报名队列移除
    db.league_queue.delete_one({"name": name})
    # 从等待组中移除（如果组内没人了则删除整个组）
    group = db.league_waiting_queue.find_one({"players.name": name})
    if group:
        remaining = [p for p in group["players"] if p["name"] != name]
        if remaining:
            db.league_waiting_queue.update_one(
                {"_id": group["_id"]},
                {"$set": {"players": remaining}}
            )
        else:
            db.league_waiting_queue.delete_one({"_id": group["_id"]})
    return jsonify({"ok": True, "name": name})


# ── 注册验证 API ──────────────────────────────────────

@app.route("/api/register", methods=["POST"])
def api_register():
    """
    用户在网站注册：
    1. 输入 battleTag + 验证码（从插件日志获取）
    2. 后端从 bg_ratings 读取存储的 verificationCode
    3. 比对一致则注册成功，写入 league_players
    """
    data = request.get_json() or {}
    battle_tag = data.get("battleTag", "").strip()
    verification_code = data.get("verificationCode", "").strip().upper()

    if not battle_tag:
        return jsonify({"error": "BattleTag 不能为空"}), 400
    if not verification_code:
        return jsonify({"error": "验证码不能为空"}), 400

    db = get_db()

    # 查 bg_ratings 获取 accountIdLo 和 verificationCode
    rating = db.bg_ratings.find_one({"playerId": battle_tag})
    if not rating:
        return jsonify({"error": f"未找到 {battle_tag} 的游戏记录，请先使用插件完成一局游戏"}), 404

    stored_code = rating.get("verificationCode")
    if not stored_code:
        return jsonify({"error": "该记录尚未生成验证码，请使用最新版插件完成一局游戏后重试"}), 400

    # 校验验证码
    if verification_code != stored_code.upper():
        return jsonify({"error": "验证码不正确，请检查插件日志中的验证码"}), 400

    # accountIdLo
    raw_lo = rating.get("accountIdLo")
    account_id_lo = str(raw_lo) if raw_lo else ""

    # 提取 displayName（去掉 #tag）
    display_name = battle_tag
    hash_idx = battle_tag.find("#")
    if hash_idx > 0:
        display_name = battle_tag[:hash_idx]

    # 写入或更新 league_players
    db.league_players.update_one(
        {"battleTag": battle_tag},
        {"$set": {
            "battleTag": battle_tag,
            "accountIdLo": account_id_lo,
            "displayName": display_name,
            "verified": True,
            "verifiedAt": datetime.utcnow().isoformat() + "Z",
        },
        "$setOnInsert": {
            "totalPoints": 0,
            "totalGames": 0,
            "wins": 0,
            "chickens": 0,
            "avgPlacement": 0,
            "createdAt": datetime.utcnow().isoformat() + "Z",
        }},
        upsert=True,
    )

    # 自动登录
    session["battleTag"] = battle_tag
    session["displayName"] = display_name

    return jsonify({"ok": True, "battleTag": battle_tag, "displayName": display_name})


@app.route("/api/verify")
def api_verify():
    """检查某 BattleTag 是否已验证"""
    battle_tag = request.args.get("battleTag", "").strip()
    if not battle_tag:
        return jsonify({"error": "缺少 battleTag 参数"}), 400

    db = get_db()
    player = db.league_players.find_one({"battleTag": battle_tag})
    if player:
        return jsonify({
            "verified": player.get("verified", False),
            "displayName": player.get("displayName", ""),
        })
    return jsonify({"verified": False})


@app.route("/api/login", methods=["POST"])
def api_login():
    """
    登录：BattleTag + 验证码 → 从 bg_ratings 比对 → 发 session
    """
    data = request.get_json() or {}
    battle_tag = data.get("battleTag", "").strip()
    verification_code = data.get("verificationCode", "").strip().upper()

    if not battle_tag:
        return jsonify({"error": "BattleTag 不能为空"}), 400
    if not verification_code:
        return jsonify({"error": "验证码不能为空"}), 400

    db = get_db()

    # 查 bg_ratings 验证码
    rating = db.bg_ratings.find_one({"playerId": battle_tag})
    if not rating:
        return jsonify({"error": f"未找到 {battle_tag} 的游戏记录"}), 404

    stored_code = rating.get("verificationCode")
    if not stored_code:
        return jsonify({"error": "该记录尚未生成验证码，请使用最新版插件完成一局游戏"}), 400

    if verification_code != stored_code.upper():
        return jsonify({"error": "验证码不正确"}), 403

    # 提取 displayName
    display_name = battle_tag
    hash_idx = battle_tag.find("#")
    if hash_idx > 0:
        display_name = battle_tag[:hash_idx]

    # 写 session
    session["battleTag"] = battle_tag
    session["displayName"] = display_name

    return jsonify({"ok": True, "battleTag": battle_tag, "displayName": display_name})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


# ── SSE 端点（Server-Sent Events）──────────────────────

def _sse_generate(fetch_fn, poll_interval=2, max_lifetime=120):
    """
    通用 SSE 生成器：内部轮询数据，有变化时推送，无变化时保持连接空闲。
    每 30 秒发一次心跳注释行，确保连接活跃 + 让客户端/代理检测断连。
    max_lifetime 秒后主动断开，由客户端 EventSource 自动重连（防连接堆积）。
    """
    last_fingerprint = None
    last_heartbeat = time.time()
    start_time = time.time()
    while True:
        try:
            # 主动断开超时连接，让客户端重连（清理可能的僵尸连接）
            if time.time() - start_time > max_lifetime:
                break
            data = fetch_fn()
            fingerprint = json.dumps(data, sort_keys=True, default=str)
            if fingerprint != last_fingerprint:
                last_fingerprint = fingerprint
                yield f"data: {fingerprint}\n\n"
            # 心跳：每 30s 发一个注释行，保持连接活跃
            if time.time() - last_heartbeat > 30:
                yield ": heartbeat\n\n"
                last_heartbeat = time.time()
            gsleep(poll_interval)
        except GeneratorExit:
            break
        except Exception as e:
            print(f"[SSE] error: {e}")
            gsleep(poll_interval)


@app.route("/api/events/active-games")
def sse_active_games():
    """SSE: 进行中对局变化推送"""
    def fetch():
        games = get_active_games()
        return [{"gameUuid": g.get("gameUuid", ""), "startedAtEpoch": g.get("startedAtEpoch"),
                 "players": [{"displayName": p.get("displayName", ""), "heroCardId": p.get("heroCardId", ""),
                              "heroName": p.get("heroName", ""), "placement": p.get("placement")}
                             for p in g.get("players", [])]}
                for g in games]
    return Response(_sse_generate(fetch), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/events/queue")
def sse_queue():
    """SSE: 报名队列变化推送"""
    def fetch():
        db = get_db()
        queue = list(db.league_queue.find().sort("joinedAt", 1))
        return [{"name": q["name"]} for q in queue]
    return Response(_sse_generate(fetch), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/events/waiting-queue")
def sse_waiting_queue():
    """SSE: 等待队列变化推送"""
    def fetch():
        db = get_db()
        groups = list(db.league_waiting_queue.find().sort("createdAt", 1))
        return [{"players": g.get("players", [])} for g in groups]
    return Response(_sse_generate(fetch), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── 全局错误处理 ─────────────────────────────────────

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
