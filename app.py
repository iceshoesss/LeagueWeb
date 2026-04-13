"""
酒馆战棋联赛网站
从 MongoDB 读取真实数据
"""

from flask import Flask, render_template, jsonify, request, session, redirect, url_for, Response
from pymongo import MongoClient
from datetime import datetime, timedelta, UTC
from bson import datetime as bson_datetime
from functools import wraps
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import hashlib
import logging
import os
import re
import time
import json
import secrets

# ── 日志配置 ────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bgtracker")

try:
    from gevent import sleep as gsleep
except ImportError:
    from time import sleep as gsleep

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

# Session cookie 配置 — 确保跨页面导航时浏览器正确发送 cookie
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = False  # 生产环境若用 HTTPS 可改为 True


# 对局超时：超过此时间未结束的对局视为异常断线，自动标记结束
GAME_TIMEOUT_MINUTES = 80

# 队列超时
QUEUE_TIMEOUT_MINUTES = 10        # 报名队列超时踢出
WAITING_QUEUE_TIMEOUT_MINUTES = 20  # 等待队列超时解散

# ── MongoDB 连接 ────────────────────────────────────
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")

# ── 网站外观 ──────────────────────────────────────
SITE_NAME = os.environ.get("SITE_NAME", "酒馆战棋联赛")
SITE_LOGO = os.environ.get("SITE_LOGO", "🍺")  # emoji 或图片 URL

def is_admin(battle_tag):
    """从数据库查询是否为管理员"""
    if not battle_tag:
        return False
    db = get_db()
    return db.league_admins.count_documents({"battleTag": battle_tag}) > 0

_client = None
_db = None
_last_cleanup_ts = 0
_last_queue_cleanup_ts = 0

# ── 排行榜缓存 ───────────────────────────────────────
_leaderboard_cache = {"data": None, "ts": 0}
LEADERBOARD_TTL = 30  # 秒

# ── 插件认证 ───────────────────────────────────────
_token_serializer = None

# 速率限制: {playerId: [(timestamp, ...), ...]}
_rate_limit_store = {}
RATE_LIMIT_WINDOW = 60   # 秒
RATE_LIMIT_MAX = 10       # 每窗口最大请求数（每个 playerId）

# token 有效期（秒）
PLUGIN_TOKEN_MAX_AGE = 7 * 24 * 3600  # 7 天


def get_token_serializer():
    global _token_serializer
    if _token_serializer is None:
        _token_serializer = URLSafeTimedSerializer(
            app.secret_key,
            salt="hdt-bgtracker-plugin"
        )
    return _token_serializer


def generate_plugin_token(player_id):
    """为 playerId 签发 token"""
    return get_token_serializer().dumps({"pid": player_id})


def verify_plugin_token(token):
    """验证 token，返回 playerId 或 None"""
    try:
        data = get_token_serializer().loads(token, max_age=PLUGIN_TOKEN_MAX_AGE)
        return data.get("pid")
    except (BadSignature, SignatureExpired):
        return None


def check_rate_limit(player_id):
    """检查速率限制，返回 True=允许, False=拒绝"""
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    # 清理过期记录
    timestamps = _rate_limit_store.get(player_id, [])
    timestamps = [t for t in timestamps if t > window_start]
    if len(timestamps) >= RATE_LIMIT_MAX:
        _rate_limit_store[player_id] = timestamps
        return False
    timestamps.append(now)
    _rate_limit_store[player_id] = timestamps
    return True


def require_plugin_auth(f):
    """
    插件端点认证装饰器:
    1. 从 Header 提取 Bearer token → 验证 → 获取 playerId
    2. 确保 token 中的 playerId 与请求体中的一致
    3. 速率限制
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "缺少认证 token"}), 401

        token = auth_header[7:]
        player_id = verify_plugin_token(token)
        if player_id is None:
            return jsonify({"error": "token 无效或已过期"}), 401

        # 校验 token 中的 playerId 与请求体一致（防篡改他人数据）
        data = request.get_json(silent=True) or {}
        req_player_id = data.get("playerId", "")
        if req_player_id and req_player_id != player_id:
            return jsonify({"error": "playerId 与 token 不匹配"}), 403

        # 速率限制
        if not check_rate_limit(player_id):
            return jsonify({"error": "请求过于频繁，请稍后重试"}), 429

        # 注入已验证的 playerId 到 request 上，方便端点使用
        request._plugin_player_id = player_id
        return f(*args, **kwargs)
    return decorated


GAME_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


# ── 排除有问题的对局（timeout / abandoned）的统一过滤条件 ─────────
# 只统计正常完成的对局：status 不存在 或 status == "completed"
VALID_MATCH_FILTER = {"$or": [{"status": {"$exists": False}}, {"status": "completed"}]}


@app.context_processor
def inject_counts():
    """每个页面自动注入进行中对局数、选手数、当前登录用户"""
    try:
        db = get_db()
        cutoff_str = (datetime.now(UTC) - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
        active_count = db.league_matches.count_documents({
            "$and": [
                {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
                {"startedAt": {"$gte": cutoff_str}}
            ]
        })
        player_count = db.league_players.count_documents({"verified": True})
    except Exception as e:
        log.error(f"[inject_counts] 数据库查询失败: {e}")
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
        "site_name": SITE_NAME,
        "site_logo": SITE_LOGO,
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
    """安全地把各种格式的时间值转为 ISO 字符串（UTC，带 Z 后缀）"""
    if dt_val is None:
        return ""
    if isinstance(dt_val, (datetime, bson_datetime.datetime)):
        return dt_val.strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(dt_val)
    # 补齐时区标记，方便前端 new Date() 正确解析为 UTC
    if s and not s.endswith("Z") and "+" not in s and s.count("-") <= 2:
        s += "Z"
    return s


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
        cleanup_stale_queues()
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
        g["startedAtEpoch"] = to_epoch(g.get("startedAt"))
        g["startedAt"] = to_iso_str(g.get("startedAt"))
    return games


def cleanup_stale_games():
    """将超过超时时间的未结束对局标记为超时结束"""
    db = get_db()
    cutoff_str = (datetime.now(UTC) - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = {
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$lt": cutoff_str}}
        ]
    }
    result = db.league_matches.update_many(
        query,
        {"$set": {
            "endedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "timeout"
        }}
    )
    if result.modified_count > 0:
        log.info(f"清理了 {result.modified_count} 个超时对局")


def cleanup_partial_matches():
    """
    处理部分掉线导致永不结束的对局：
    有人填了 placement 但超过超时时间仍未全部填完的对局，
    将未填的玩家标记为 placement=null, status="abandoned"，
    并写入 endedAt 让对局结束。
    """
    db = get_db()
    cutoff_str = (datetime.now(UTC) - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
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
                "endedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "abandoned"
            }}
        )
        count += 1

    if count > 0:
        log.info(f"标记了 {count} 个部分掉线对局")


def cleanup_stale_queues():
    """清理过期的队列条目：
    1. lastSeen 超过 QUEUE_TIMEOUT_MINUTES 的报名队列玩家 → 移除
    2. lastSeen 超过 WAITING_QUEUE_TIMEOUT_MINUTES 的等待组 → 解散（组内活跃玩家回报名队列）
    """
    global _last_queue_cleanup_ts
    now = time.time()
    if now - _last_queue_cleanup_ts < 30:
        return  # 30 秒内不重复清理
    _last_queue_cleanup_ts = now

    db = get_db()
    now_dt = datetime.now(UTC)

    # 1. 清理报名队列中超时的玩家
    queue_cutoff = (now_dt - timedelta(minutes=QUEUE_TIMEOUT_MINUTES)).isoformat() + "Z"
    expired_queue = list(db.league_queue.find({
        "lastSeen": {"$lt": queue_cutoff}
    }))
    if expired_queue:
        names = [p["name"] for p in expired_queue]
        db.league_queue.delete_many({"name": {"$in": names}})
        log.info(f"报名队列踢出超时玩家: {names}")

    # 2. 清理等待队列中超时的组（直接解散，不回队列）
    waiting_cutoff = (now_dt - timedelta(minutes=WAITING_QUEUE_TIMEOUT_MINUTES)).isoformat() + "Z"
    expired_groups = list(db.league_waiting_queue.find({
        "createdAt": {"$lt": waiting_cutoff}
    }))
    for group in expired_groups:
        db.league_waiting_queue.delete_one({"_id": group["_id"]})
        expired_names = [p.get("name", "") for p in group.get("players", [])]
        log.info(f"等待组解散: {expired_names}")


@app.before_request
def update_last_seen():
    """每次请求刷新登录用户的 lastSeen（选手记录 + 队列条目）"""
    battle_tag = session.get("battleTag")
    if not battle_tag:
        return
    now_str = datetime.now(UTC).isoformat() + "Z"
    try:
        db = get_db()
        # 刷新选手记录
        db.league_players.update_one(
            {"battleTag": battle_tag},
            {"$set": {"lastSeen": now_str}},
        )
        # 刷新报名队列中的 lastSeen
        db.league_queue.update_one(
            {"name": battle_tag},
            {"$set": {"lastSeen": now_str}},
        )
    except Exception:
        pass  # 数据库不可用时不阻塞请求

def get_player(battle_tag):
    """从 league_matches + bg_ratings 聚合获取单个选手信息"""
    db = get_db()
    pipeline = [
        {"$match": {"$and": [{"endedAt": {"$ne": None}}, VALID_MATCH_FILTER, {"players.battleTag": battle_tag}]}},
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
            "$and": [
                {"players.battleTag": battle_tag},
                {"endedAt": {"$ne": None}},
                VALID_MATCH_FILTER,
            ]
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
    """获取某选手的所有对局记录（排除超时/中断等有问题的对局）"""
    db = get_db()
    pipeline = [
        {"$match": {
            "$and": [
                {"players.battleTag": battle_tag},
                {"endedAt": {"$nin": [None]}},
                VALID_MATCH_FILTER,
            ]
        }},
        {"$sort": {"endedAt": -1}},
        {"$unwind": "$players"},
        {"$match": {"players.battleTag": battle_tag}},
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
    return render_template("player.html", player=player, matches=player_matches, matches_json=player_matches, rival=rival_stats)


@app.route("/match/<game_uuid>")
def match_page(game_uuid):
    match = get_match(game_uuid)
    if not match:
        return render_template("404.html", title="对局不存在", emoji="⚔️",
            message="这局对局可能从未发生过，或者数据已被清理"), 404
    return render_template("match.html", match=match)


@app.route("/match/<game_uuid>/edit")
def match_edit_page(game_uuid):
    # 必须登录
    battle_tag = session.get("battleTag")
    if not battle_tag:
        return redirect(url_for("register_page"))

    match = get_match(game_uuid)
    if not match:
        return render_template("404.html", title="对局不存在", emoji="⚔️",
            message="这局对局可能从未发生过，或者数据已被清理"), 404
    # 判断是否问题对局：有玩家 placement 为 null
    is_problem = any(p.get("placement") is None for p in match.get("players", []))
    if not is_problem:
        return redirect(url_for("match_page", game_uuid=game_uuid))

    admin = is_admin(battle_tag)
    # 非管理员：检查自己是否在这局对局中
    if not admin:
        in_match = any(p.get("battleTag") == battle_tag for p in match.get("players", []))
        if not in_match:
            return redirect(url_for("match_page", game_uuid=game_uuid))

    return render_template("match_edit.html", match=match, is_admin=admin, my_battle_tag=battle_tag)


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
    # 必须登录
    battle_tag = session.get("battleTag")
    if not battle_tag:
        return jsonify({"error": "请先登录"}), 401

    data = request.get_json() or {}
    placements = data.get("placements", {})  # {accountIdLo: placement}

    if not placements:
        return jsonify({"error": "未提供排名数据"}), 400

    db = get_db()
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if not match:
        return jsonify({"error": "对局不存在"}), 404

    admin = is_admin(battle_tag)

    # 非管理员：只能改自己的，且必须是这局对局的参与者
    if not admin:
        my_account_ids = set()
        in_match = False
        for p in match.get("players", []):
            if p.get("battleTag") == battle_tag:
                in_match = True
                my_account_ids.add(str(p.get("accountIdLo", "")))
        if not in_match:
            return jsonify({"error": "你不是这局对局的参与者"}), 403
        # 检查提交中是否有非自己的玩家
        for lo in placements:
            if lo not in my_account_ids:
                return jsonify({"error": "你只能补录自己的排名"}), 403

    # 验证：提交的排名不重复，且值在 1-8 范围内
    values = list(placements.values())
    if not values:
        return jsonify({"error": "未提供排名数据"}), 400
    if any(v < 1 or v > 8 for v in values):
        return jsonify({"error": "排名必须在 1-8 之间"}), 400
    if len(values) != len(set(values)):
        return jsonify({"error": "提交的排名中存在重复"}), 400

    # 逐个更新 players 数组中对应玩家的 placement 和 points
    players = match.get("players", [])
    updated = 0
    skipped_locked = 0
    for p in players:
        lo = str(p.get("accountIdLo", ""))
        if lo in placements:
            # 已有排名的玩家禁止修改，防止篡改
            if p.get("placement") is not None:
                skipped_locked += 1
                continue
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
        if skipped_locked > 0:
            return jsonify({"error": f"所有提交的玩家已有排名（已锁定 {skipped_locked} 人），无法修改"}), 400
        return jsonify({"error": "未匹配到任何玩家"}), 400

    # 写入 endedAt（如果还没有）并去掉 status 标记
    db.league_matches.update_one(
        {"gameUuid": game_uuid},
        {"$set": {"endedAt": match.get("endedAt") or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
         "$unset": {"status": ""}}
    )

    return jsonify({"ok": True, "updated": updated, "skipped_locked": skipped_locked})


# ── 报名队列 API ──────────────────────────────────────

@app.route("/api/queue")
def api_queue():
    """获取报名队列"""
    db = get_db()
    queue = list(db.league_queue.find().sort("joinedAt", 1))
    for q in queue:
        q["_id"] = str(q["_id"])
        q["joinedAt"] = to_iso_str(q.get("joinedAt"))
        q["lastSeen"] = to_iso_str(q.get("lastSeen"))
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
    now_str = datetime.now(UTC).isoformat() + "Z"

    # 先清理过期队列
    cleanup_stale_queues()

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

    # 查找 accountIdLo
    player_info = db.league_players.find_one({"battleTag": name})
    account_id_lo = str(player_info.get("accountIdLo", "")) if player_info else ""

    player_entry = {"name": name, "accountIdLo": account_id_lo}

    if incomplete_group:
        db.league_waiting_queue.update_one(
            {"_id": incomplete_group["_id"]},
            {"$push": {"players": player_entry}}
        )
        return jsonify({"ok": True, "name": name, "moved": True})

    # 没有未满的组，加入报名队列
    db.league_queue.update_one(
        {"name": name},
        {"$setOnInsert": {"name": name, "joinedAt": now_str},
         "$set": {"lastSeen": now_str}},
        upsert=True,
    )

    # 检查是否满N人
    signup_count = db.league_queue.count_documents({})
    if signup_count >= 8:
        signup = list(db.league_queue.find().sort("joinedAt", 1).limit(8))
        players = []
        names = []
        for p in signup:
            p_name = p["name"]
            names.append(p_name)
            p_info = db.league_players.find_one({"battleTag": p_name})
            p_lo = str(p_info.get("accountIdLo", "")) if p_info else ""
            players.append({"name": p_name, "accountIdLo": p_lo})
        db.league_waiting_queue.insert_one({
            "players": players,
            "createdAt": datetime.now(UTC).isoformat() + "Z",
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
    # 先清理过期队列
    cleanup_stale_queues()
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
            "verifiedAt": datetime.now(UTC).isoformat() + "Z",
        },
        "$setOnInsert": {
            "createdAt": datetime.now(UTC).isoformat() + "Z",
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

    # 提取 accountIdLo
    raw_lo = rating.get("accountIdLo")
    account_id_lo = str(raw_lo) if raw_lo else ""

    # 写入 league_players（登录即注册）
    db.league_players.update_one(
        {"battleTag": battle_tag},
        {"$set": {
            "battleTag": battle_tag,
            "accountIdLo": account_id_lo,
            "displayName": display_name,
            "verified": True,
            "verifiedAt": datetime.now(UTC).isoformat() + "Z",
        },
        "$setOnInsert": {
            "createdAt": datetime.now(UTC).isoformat() + "Z",
        }},
        upsert=True,
    )

    # 写 session
    session["battleTag"] = battle_tag
    session["displayName"] = display_name

    return jsonify({"ok": True, "battleTag": battle_tag, "displayName": display_name})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    """登出并自动退出所有队列"""
    battle_tag = session.get("battleTag")
    if battle_tag:
        db = get_db()
        # 从报名队列移除
        db.league_queue.delete_one({"name": battle_tag})
        # 从等待组中移除
        group = db.league_waiting_queue.find_one({"players.name": battle_tag})
        if group:
            remaining = [p for p in group["players"] if p["name"] != battle_tag]
            if remaining:
                db.league_waiting_queue.update_one(
                    {"_id": group["_id"]},
                    {"$set": {"players": remaining}}
                )
            else:
                db.league_waiting_queue.delete_one({"_id": group["_id"]})
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
            log.error(f"[SSE] error: {e}")
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


@app.route("/api/events/matches")
def sse_matches():
    """SSE: 最近对局变化推送（有新对局结束时触发）"""
    def fetch():
        matches = get_completed_matches(limit=5)
        return [{
            "gameUuid": m.get("gameUuid", ""),
            "endedAt": to_cst_str(m.get("endedAt")),
            "players": [{
                "displayName": p.get("displayName", ""),
                "heroCardId": p.get("heroCardId", ""),
                "heroName": p.get("heroName", ""),
                "placement": p.get("placement"),
                "points": p.get("points"),
            } for p in m.get("players", [])]
        } for m in matches]
    return Response(_sse_generate(fetch), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/events/problem-matches")
def sse_problem_matches():
    """SSE: 问题对局数量变化推送"""
    def fetch():
        db = get_db()
        count = db.league_matches.count_documents({
            "endedAt": {"$nin": [None]},
            "$or": [
                {"status": {"$in": ["timeout", "abandoned"]}},
                {"$and": [
                    {"status": {"$exists": False}},
                    {"players": {"$elemMatch": {"placement": None}}}
                ]}
            ]
        })
        return {"count": count}
    return Response(_sse_generate(fetch), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── 插件专用 API（C# 插件通过 HTTP 调用，替代直连 MongoDB）──────────

def _generate_verification_code(oid):
    """基于 MongoDB ObjectId 生成确定性验证码（SHA256 前 8 位大写）"""
    raw = f"bgtracker:{oid}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8].upper()


def _ensure_verification_code(db, player_id, account_id_lo="", mode="solo", region="CN", timestamp=None):
    """
    确保玩家在 bg_ratings 中有记录并返回验证码。
    已有记录 → 返回现有验证码（可选更新 accountIdLo）。
    无记录 → 创建记录并生成新验证码。
    返回: verification_code (str) 或 None（player_id 无效时）。
    """
    if not player_id or player_id == "unknown":
        return None

    existing = db.bg_ratings.find_one({"playerId": player_id})
    if existing:
        vc = existing.get("verificationCode")
        if account_id_lo and not existing.get("accountIdLo"):
            db.bg_ratings.update_one(
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
    result = db.bg_ratings.insert_one(doc)
    vc = _generate_verification_code(result.inserted_id)
    db.bg_ratings.update_one({"_id": result.inserted_id}, {"$set": {"verificationCode": vc}})
    return vc


@app.route("/api/plugin/upload-rating", methods=["POST"])
def api_plugin_upload_rating():
    """
    插件上报分数

    无需认证。

    请求体: { playerId, accountIdLo, rating, mode, gameUuid, region }
    返回:   { ok, verificationCode? }
    """
    data = request.get_json() or {}
    player_id = data.get("playerId", "").strip()
    account_id_lo = data.get("accountIdLo", "").strip()
    rating = data.get("rating")
    mode = data.get("mode", "solo")
    region = data.get("region", "CN")

    # ── 基础校验 ──
    if not player_id or player_id == "unknown":
        return jsonify({"error": "playerId 无效"}), 400
    if not isinstance(rating, (int, float)):
        return jsonify({"error": "rating 必须是数字"}), 400
    if mode not in ("solo", "duo"):
        return jsonify({"error": "mode 必须是 solo 或 duo"}), 400

    # ── 速率限制 ──
    if not check_rate_limit(player_id):
        return jsonify({"error": "请求过于频繁，请稍后重试"}), 429

    db = get_db()
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 查找现有文档
    existing = db.bg_ratings.find_one({"playerId": player_id})

    if existing:
        set_doc = {
            "lastRating": existing.get("rating", rating),
            "rating": rating,
            "ratingChange": rating - existing.get("rating", rating),
            "mode": mode,
            "region": region,
            "timestamp": now_str,
            "gameCount": existing.get("gameCount", 0) + 1,
        }
        if account_id_lo:
            set_doc["accountIdLo"] = account_id_lo
        db.bg_ratings.update_one({"_id": existing["_id"]}, {"$set": set_doc})
        verification_code = existing.get("verificationCode")
    else:
        # 首次上传
        doc = {
            "playerId": player_id,
            "accountIdLo": account_id_lo,
            "rating": rating,
            "lastRating": rating,
            "ratingChange": 0,
            "mode": mode,
            "region": region,
            "timestamp": now_str,
            "gameCount": 1,
        }
        result = db.bg_ratings.insert_one(doc)
        verification_code = _generate_verification_code(result.inserted_id)
        db.bg_ratings.update_one(
            {"_id": result.inserted_id},
            {"$set": {"verificationCode": verification_code}}
        )

    resp = {"ok": True}
    if verification_code:
        resp["verificationCode"] = verification_code
    return jsonify(resp)


@app.route("/api/plugin/check-league", methods=["POST"])
def api_plugin_check_league():
    """
    检查是否为联赛对局（替代 C# CheckLeagueQueue）
    需要 Header: Authorization: Bearer <token>

    请求体: { playerId, gameUuid, accountIdLoList: [...], players?: {...}, mode?, region?, startedAt? }
    返回:   { isLeague: true/false }
    """
    data = request.get_json() or {}
    game_uuid = data.get("gameUuid", "").strip()
    account_ids = set(str(a) for a in data.get("accountIdLoList", []))

    if not game_uuid or not account_ids:
        log.warning(f"[check-league] 400: 参数不完整 gameUuid={game_uuid!r} account_ids={len(account_ids)}")
        return jsonify({"error": "参数不完整"}), 400
    if not GAME_UUID_RE.match(game_uuid):
        log.warning(f"[check-league] 400: gameUuid 格式无效: {game_uuid!r}")
        return jsonify({"error": "gameUuid 格式无效"}), 400
    db = get_db()

    # 先清理过期等待组
    cleanup_stale_queues()

    # 遍历等待组，找完全匹配
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

        # 只匹配有完整 accountIdLo 的组（新数据）
        if has_all_ids and len(account_ids) == len(queue_ids) and account_ids == queue_ids:
            matched_group = group
            break

    # >>> BEGIN TEST_MODE
    if matched_group is None:
        # fallback：等待组已被队友匹配删除，但联赛对局已创建
        is_league = db.league_matches.find_one({"gameUuid": game_uuid}) is not None
        resp = {"isLeague": is_league}
        vc = _ensure_verification_code(
            db,
            player_id=data.get("playerId", "").strip(),
            account_id_lo=data.get("accountIdLo", "").strip(),
            mode=data.get("mode", "solo"),
            region=data.get("region", "CN"),
        )
        if vc:
            resp["verificationCode"] = vc
        return jsonify(resp)
    # <<< END TEST_MODE

    # 删除等待组
    db.league_waiting_queue.delete_one({"_id": matched_group["_id"]})

    # 构建 players 数组（优先用请求体中的详细信息，fallback 到等待组数据）
    detailed_players = data.get("players", {})  # {accountIdLo: {heroCardId, heroName, battleTag, displayName}}

    players = []
    for p in matched_group.get("players", []):
        lo = str(p.get("accountIdLo", ""))
        detail = detailed_players.get(lo, {})
        players.append({
            "accountIdLo": lo,
            "battleTag": detail.get("battleTag", p.get("name", "")),
            "displayName": detail.get("displayName", p.get("name", "")),
            "heroCardId": detail.get("heroCardId", ""),
            "heroName": detail.get("heroName", ""),
            "placement": None,
            "points": None,
        })

    # 创建 league_matches 文档（upsert 防重复）
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
        }},
        upsert=True,
    )

    # ── 验证码处理（插件纯联赛模式下，check-league 承担验证码职责）──
    resp = {"isLeague": True}
    vc = _ensure_verification_code(
        db,
        player_id=data.get("playerId", "").strip(),
        account_id_lo=data.get("accountIdLo", "").strip(),
        mode=mode, region=region, timestamp=started_at,
    )
    if vc:
        resp["verificationCode"] = vc

    return jsonify(resp)


@app.route("/api/plugin/update-placement", methods=["POST"])
def api_plugin_update_placement():
    """
    更新联赛对局排名
    无需认证。

    请求体: { playerId, gameUuid, accountIdLo, placement }
    返回:   { ok, finalized }
    """
    data = request.get_json() or {}
    game_uuid = data.get("gameUuid", "").strip()
    account_id_lo = str(data.get("accountIdLo", ""))
    player_id = data.get("playerId", "")
    placement = data.get("placement")

    # ── 请求日志 ──
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

    # 速率限制
    if player_id and not check_rate_limit(player_id):
        return jsonify({"error": "请求过于频繁，请稍后重试"}), 429

    db = get_db()

    # 查找对局文档
    match = db.league_matches.find_one({"gameUuid": game_uuid})
    if match is None:
        log.error(f"[update-placement] 未找到对局: gameUuid={game_uuid}")
        return jsonify({"error": "未找到对局"}), 404

    # 定位目标玩家在数组中的索引
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

    # 通过索引直接更新，避免 $ 位置操作符匹配错误元素
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

    # 检查是否所有 8 人都填完了（用刚更新后的内存数据）
    players[target_index]["placement"] = placement
    all_done = all(p.get("placement") is not None for p in players)
    finalized = False
    if all_done:
        db.league_matches.update_one(
            {"gameUuid": game_uuid},
            {"$set": {"endedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}}
        )
        finalized = True
        log.info(f"[update-placement] 对局已结束: gameUuid={game_uuid}")

    return jsonify({"ok": True, "finalized": finalized})


# ── 全局错误处理 ─────────────────────────────────────

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
