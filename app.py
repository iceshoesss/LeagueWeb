"""
酒馆战棋联赛网站 — 入口文件
模块拆分：db / auth / cleanup / data / routes_* / sse
"""

import logging
import os
import secrets
import threading
from datetime import datetime, timedelta, UTC
from flask import Flask, session

# ── 日志配置 ────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bgtracker")

# ── Flask 应用 ──────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = False

# ── 网站外观 ──────────────────────────────────────
SITE_NAME = os.environ.get("SITE_NAME", "酒馆战棋联赛")
SITE_LOGO = os.environ.get("SITE_LOGO", "🍺")
WEB_VERSION = "0.5.3"

# ── 注册蓝图 ──────────────────────────────────────
from routes_pages import pages
from routes_admin import admin_bp
from routes_league import league_bp
from routes_plugin import plugin_bp
from sse import sse_bp

app.register_blueprint(pages)
app.register_blueprint(admin_bp)
app.register_blueprint(league_bp)
app.register_blueprint(plugin_bp)
app.register_blueprint(sse_bp)

# ── Jinja 过滤器 ──────────────────────────────────
from db import to_cst_str
app.jinja_env.filters['cst'] = to_cst_str

# ── 上下文处理器 ──────────────────────────────────
from db import get_db, GAME_TIMEOUT_MINUTES, to_iso_str
from auth import is_admin, is_super_admin, PLUGIN_API_KEY, MIN_PLUGIN_VERSION, _version_tuple
from cleanup import CLEANUP_INTERVAL, _background_cleanup


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

    current_user = None
    is_admin_user = False
    is_super_admin_user = False
    battle_tag = session.get("battleTag")
    if battle_tag:
        current_user = {"battleTag": battle_tag, "displayName": session.get("displayName", battle_tag)}
        is_admin_user = is_admin(battle_tag)
        is_super_admin_user = is_super_admin(battle_tag)

    return {
        "active_game_count": active_count,
        "total_player_count": player_count,
        "current_user": current_user,
        "is_admin_user": is_admin_user,
        "is_super_admin": is_super_admin_user,
        "site_name": SITE_NAME,
        "site_logo": SITE_LOGO,
        "web_version": WEB_VERSION,
    }


# ── before_request ────────────────────────────────

@app.before_request
def check_plugin_version():
    """插件端点认证 + 版本检查"""
    from flask import request, jsonify
    if not request.path.startswith("/api/plugin/"):
        return

    if PLUGIN_API_KEY:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "缺少认证 token"}), 403
        if auth_header[7:] != PLUGIN_API_KEY:
            return jsonify({"error": "认证失败"}), 403

    plugin_version = request.headers.get("X-HDT-Plugin", "")
    if not plugin_version:
        return jsonify({"error": "缺少 X-HDT-Plugin header"}), 403
    if _version_tuple(plugin_version) < _version_tuple(MIN_PLUGIN_VERSION):
        return jsonify({
            "error": f"插件版本过低（当前 {plugin_version}，最低 {MIN_PLUGIN_VERSION}），请更新插件"
        }), 403


@app.before_request
def update_last_seen():
    """每次请求刷新登录用户的 lastSeen"""
    from flask import request
    battle_tag = session.get("battleTag")
    if not battle_tag:
        return
    now_str = datetime.now(UTC).isoformat() + "Z"
    try:
        db = get_db()
        db.league_players.update_one(
            {"battleTag": battle_tag},
            {"$set": {"lastSeen": now_str}},
        )
        db.league_queue.update_one(
            {"name": battle_tag},
            {"$set": {"lastSeen": now_str}},
        )
    except Exception:
        pass


# ── 404 ──────────────────────────────────────────

@app.errorhandler(404)
def page_not_found(e):
    from flask import render_template
    return render_template("404.html"), 404


# ── 启动后台清理线程 ──────────────────────────────
_cleanup_thread = threading.Thread(target=_background_cleanup, daemon=True)
_cleanup_thread.start()
log.info(f"后台 cleanup 已启动，间隔 {CLEANUP_INTERVAL} 秒")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
