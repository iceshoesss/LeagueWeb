"""数据库连接 + 时间工具 + 共享常量"""

import os
import time
import logging
from datetime import datetime, timedelta, UTC
from bson import datetime as bson_datetime
from pymongo import MongoClient

log = logging.getLogger("bgtracker")

# ── MongoDB 连接配置 ────────────────────────────────
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")

# ── 共享常量 ────────────────────────────────────────
GAME_TIMEOUT_MINUTES = 80
QUEUE_TIMEOUT_MINUTES = 10
WAITING_QUEUE_TIMEOUT_MINUTES = 20
MIN_MATCH_PLAYERS = 8

# ── 排除有问题的对局的统一过滤条件 ─────────────────
VALID_MATCH_FILTER = {"$or": [{"status": {"$exists": False}}, {"status": "completed"}]}

# ── 赛事报名 ──────────────────────────────────────
ENROLL_CAP = 1024      # 显示用上限（好看）
ENROLL_SLOTS = 896     # 实际正选名额，超过后进替补
ENROLL_DEADLINE = os.environ.get("ENROLL_DEADLINE", "")

# ── 赛事阶段 ──────────────────────────────────────
TOURNAMENT_PHASE = os.environ.get("TOURNAMENT_PHASE", "auto")

_client = None
_db = None


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
        _ensure_indexes(_db)
    return _db


def _ensure_indexes(db):
    """创建常用索引（仅首次执行，幂等）"""
    try:
        db.league_matches.create_index([("tournamentGroupId", 1), ("endedAt", 1)])
        db.league_matches.create_index([("endedAt", 1), ("startedAt", -1)])
        db.league_matches.create_index([("gameUuid", 1)], unique=True, sparse=True)
        db.tournament_groups.create_index([("tournamentName", 1), ("round", 1), ("groupIndex", 1)])
        db.tournament_groups.create_index([("status", 1)])
        db.player_records.create_index([("playerId", 1)], unique=True, sparse=True)
        db.league_players.create_index([("battleTag", 1)], unique=True, sparse=True)
        db.league_queue.create_index([("joinedAt", 1)])
        db.league_waiting_queue.create_index([("createdAt", 1)])
        db.tournament_enrollments.create_index([("battleTag", 1)], unique=True, sparse=True)
        log.info("[db] 索引创建完成")
    except Exception as e:
        log.warning(f"[db] 索引创建异常（可忽略）: {e}")


def to_epoch(dt_val):
    """安全地把各种格式的时间值转为 epoch 秒数（统一按 UTC 处理）"""
    if dt_val is None:
        return int(time.time())
    if isinstance(dt_val, (datetime, bson_datetime.datetime)):
        if dt_val.tzinfo is None:
            from datetime import timezone
            dt_val = dt_val.replace(tzinfo=timezone.utc)
        return int(dt_val.timestamp())
    try:
        s = str(dt_val)
        if s.endswith("Z"):
            s = s[:-1]
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
    s = str(dt_val).strip()
    if not s:
        return ""
    if s.endswith("Z"):
        return s
    if "+" in s or (s.count("-") > 2):
        try:
            dt = datetime.fromisoformat(s)
            from datetime import timezone as tz
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz.utc)
            return dt.astimezone(tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return ""
    s = s.replace(" ", "T")
    return s + "Z"


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
