"""认证、权限、速率限制"""

import os
import re
import time
import logging
from functools import wraps
from flask import request, jsonify, session
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from db import get_db

log = logging.getLogger("bgtracker")

# ── 插件认证配置 ────────────────────────────────────
PLUGIN_API_KEY = os.environ.get("PLUGIN_API_KEY", "")
MIN_PLUGIN_VERSION = os.environ.get("MIN_PLUGIN_VERSION", "0.5.5")
PLUGIN_TOKEN_MAX_AGE = 7 * 24 * 3600  # 7 天

# 速率限制
_rate_limit_store = {}
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 10

_token_serializer = None

GAME_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


def is_admin(battle_tag):
    """从数据库查询是否为管理员（含超级管理员）"""
    if not battle_tag:
        return False
    db = get_db()
    return db.league_admins.count_documents({"battleTag": battle_tag}) > 0


def is_super_admin(battle_tag):
    """从数据库查询是否为超级管理员"""
    if not battle_tag:
        return False
    db = get_db()
    return db.league_admins.count_documents({"battleTag": battle_tag, "isSuperAdmin": True}) > 0


def _admin_required():
    """检查管理员权限，返回 battleTag 或 None"""
    battle_tag = session.get("battleTag")
    if not battle_tag or not is_admin(battle_tag):
        return None
    return battle_tag


def get_token_serializer():
    global _token_serializer
    if _token_serializer is None:
        from app import app as flask_app
        _token_serializer = URLSafeTimedSerializer(
            flask_app.secret_key,
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
    timestamps = _rate_limit_store.get(player_id, [])
    timestamps = [t for t in timestamps if t > window_start]
    if len(timestamps) >= RATE_LIMIT_MAX:
        _rate_limit_store[player_id] = timestamps
        return False
    timestamps.append(now)
    _rate_limit_store[player_id] = timestamps
    return True


def require_plugin_auth(f):
    """插件端点认证装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "缺少认证 token"}), 401

        token = auth_header[7:]
        player_id = verify_plugin_token(token)
        if player_id is None:
            return jsonify({"error": "token 无效或已过期"}), 401

        data = request.get_json(silent=True) or {}
        req_player_id = data.get("playerId", "")
        if req_player_id and req_player_id != player_id:
            return jsonify({"error": "playerId 与 token 不匹配"}), 403

        if not check_rate_limit(player_id):
            return jsonify({"error": "请求过于频繁，请稍后重试"}), 429

        request._plugin_player_id = player_id
        return f(*args, **kwargs)
    return decorated


def _version_tuple(v):
    """将版本字符串转为可比较的元组"""
    try:
        return tuple(int(x) for x in v.strip().lstrip("vV").split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)
