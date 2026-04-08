"""
酒馆战棋联赛网站
从 MongoDB 读取真实数据
"""

from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from pymongo import MongoClient
from datetime import datetime, timedelta
from bson import datetime as bson_datetime
import hashlib
import secrets
import time

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# 对局超时：超过此时间未结束的对局视为异常断线，自动标记结束
GAME_TIMEOUT_MINUTES = 80

# ── MongoDB 连接 ────────────────────────────────────
MONGO_URL = "mongodb://YOUR_MONGO_HOST:27017"
DB_NAME = "hearthstone"

_client = None
_db = None


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
        player_count = len(db.league_matches.distinct("players.battleTag",
            {"endedAt": {"$nin": [None]}}))
    except Exception:
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
    """将超过超时时间的未结束对局标记为结束"""
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
        {"$set": {"endedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}}
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


def get_rival_stats(battle_tag):
    """统计最软的虾和最硬的鸭"""
    db = get_db()
    matches = list(db.league_matches.find(
        {
            "players.battleTag": battle_tag,
            "endedAt": {"$nin": [None]}
        }
    ))

    below_counts = {}  # 排名比我低的人（名次数字比我大）
    above_counts = {}  # 排名比我高的人（名次数字比我小）

    for m in matches:
        my_placement = None
        others = []
        for p in m.get("players", []):
            if p.get("battleTag") == battle_tag:
                my_placement = p.get("placement")
            else:
                others.append(p)

        if my_placement is None:
            continue

        for p in others:
            opp_placement = p.get("placement")
            if opp_placement is None:
                continue
            opp_name = p.get("displayName", p.get("battleTag", "未知"))
            if opp_placement > my_placement:
                below_counts[opp_name] = below_counts.get(opp_name, 0) + 1
            elif opp_placement < my_placement:
                above_counts[opp_name] = above_counts.get(opp_name, 0) + 1

    softest_shrimp = max(below_counts, key=below_counts.get) if below_counts else None
    hardest_duck = max(above_counts, key=above_counts.get) if above_counts else None

    return {
        "softestShrimp": {"name": softest_shrimp, "count": below_counts.get(softest_shrimp, 0)} if softest_shrimp else None,
        "hardestDuck": {"name": hardest_duck, "count": above_counts.get(hardest_duck, 0)} if hardest_duck else None,
    }


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
                    "heroCardId": p.get("heroCardId", ""),
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
    rival_stats = get_rival_stats(battle_tag)
    return render_template("player.html", player=player, matches=player_matches, rival=rival_stats)


@app.route("/match/<game_uuid>")
def match_page(game_uuid):
    match = get_match(game_uuid)
    if not match:
        return "对局不存在", 404
    return render_template("match.html", match=match)


@app.route("/register")
def register_page():
    return render_template("register.html")


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
    """加入报名队列"""
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "名字不能为空"}), 400

    db = get_db()

    # 不能重复报名或已在等待组中
    if db.league_waiting_queue.find_one({"players.name": name}):
        return jsonify({"error": "已在等待队列中"}), 400

    db.league_queue.update_one(
        {"name": name},
        {"$setOnInsert": {"name": name, "joinedAt": datetime.utcnow().isoformat() + "Z"}},
        upsert=True,
    )

    # 检查是否满N人
    signup_count = db.league_queue.count_documents({})
    if signup_count >= 2:
        signup = list(db.league_queue.find().sort("joinedAt", 1).limit(2))
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
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "名字不能为空"}), 400

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
