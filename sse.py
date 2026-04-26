"""SSE（Server-Sent Events）实时推送端点 — 事件驱动版"""

import json
import logging
import threading
import time
from flask import Blueprint, Response

from db import get_db, to_cst_str, VALID_MATCH_FILTER

log = logging.getLogger("bgtracker")
sse_bp = Blueprint("sse", __name__)

try:
    from gevent import sleep as gsleep
except ImportError:
    from time import sleep as gsleep

# ── 事件对象：写入端点 import 后调用 .set() 通知 SSE ──
evt_active_games = threading.Event()
evt_queue = threading.Event()
evt_waiting_queue = threading.Event()
evt_matches = threading.Event()
evt_problem_matches = threading.Event()
evt_bracket = threading.Event()


def _sse_generate(fetch_fn, event=None, poll_interval=10, max_lifetime=120):
    """通用 SSE 生成器：等待事件触发，有变化时推送。

    event: threading.Event，有数据变更时被 set()。
    poll_interval: 兜底轮询间隔（秒），防止极端情况下事件丢失。
    """
    last_fingerprint = None
    last_heartbeat = time.time()
    start_time = time.time()
    while True:
        try:
            if time.time() - start_time > max_lifetime:
                break
            # 等待事件触发，超时后兜底查一次
            if event is not None:
                event.wait(timeout=poll_interval)
                event.clear()
            else:
                gsleep(poll_interval)
            data = fetch_fn()
            fingerprint = json.dumps(data, sort_keys=True, default=str)
            if fingerprint != last_fingerprint:
                last_fingerprint = fingerprint
                yield f"data: {fingerprint}\n\n"
            if time.time() - last_heartbeat > 30:
                yield ": heartbeat\n\n"
                last_heartbeat = time.time()
        except GeneratorExit:
            break
        except Exception as e:
            log.error(f"[SSE] error: {e}")
            gsleep(poll_interval)


@sse_bp.route("/api/events/active-games")
def sse_active_games():
    from data import get_active_games
    def fetch():
        games = get_active_games()
        return [{"gameUuid": g.get("gameUuid", ""), "startedAtEpoch": g.get("startedAtEpoch"),
                 "players": [{"displayName": p.get("displayName", ""), "heroCardId": p.get("heroCardId", ""),
                              "heroName": p.get("heroName", ""), "placement": p.get("placement")}
                             for p in g.get("players", [])]}
                for g in games]
    return Response(_sse_generate(fetch, evt_active_games), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/queue")
def sse_queue():
    def fetch():
        db = get_db()
        queue = list(db.league_queue.find().sort("joinedAt", 1))
        return [{"name": q["name"]} for q in queue]
    return Response(_sse_generate(fetch, evt_queue), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/waiting-queue")
def sse_waiting_queue():
    def fetch():
        db = get_db()
        groups = list(db.league_waiting_queue.find().sort("createdAt", 1))
        return [{"players": g.get("players", [])} for g in groups]
    return Response(_sse_generate(fetch, evt_waiting_queue), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/matches")
def sse_matches():
    from data import get_completed_matches
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
    return Response(_sse_generate(fetch, evt_matches), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/problem-matches")
def sse_problem_matches():
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
    return Response(_sse_generate(fetch, evt_problem_matches), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/bracket")
def sse_bracket():
    def fetch():
        from routes_tournament import build_bracket_data
        return build_bracket_data()
    return Response(_sse_generate(fetch, evt_bracket), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
