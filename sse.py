"""SSE（Server-Sent Events）实时推送端点 — 事件驱动 + 共享缓存版

优化点：event 触发时仅 1 个 greenlet 查 MongoDB，其余从缓存读取。
N 个客户端 → N 次查询降为 1 次。
"""

import json
import logging
import time
from flask import Blueprint, Response

from db import get_db, to_cst_str, VALID_MATCH_FILTER

log = logging.getLogger("bgtracker")
sse_bp = Blueprint("sse", __name__)

try:
    from gevent import sleep as gsleep
    from gevent.event import Event as GEvent
    from gevent.lock import Semaphore as GSemaphore
except ImportError:
    from time import sleep as gsleep
    from threading import Event as GEvent
    from threading import Lock as GSemaphore


# ── CacheEntry：共享缓存，event 触发时仅首个 greenlet 查库 ──
class CacheEntry:
    """多个 SSE greenlet 共享同一份数据。

    event.set() 后所有 greenlet 醒来，第一个拿到锁的查库并缓存结果，
    后续 greenlet 检查 generation 发现已更新，直接用缓存。
    """
    __slots__ = ('data', 'generation', 'event', '_lock', '_cond')

    def __init__(self, event):
        self.data = None
        self.generation = 0
        self.event = event
        self._lock = GSemaphore(1)
        self._cond = GEvent()

    def update(self, data):
        self.data = data
        self.generation += 1
        self._cond.set()
        self._cond.clear()

    def wait(self, timeout):
        """等待新数据。返回 generation（可用 _lock 查库）。"""
        self.event.wait(timeout=timeout)
        self.event.clear()
        return self.generation

    def wait_for_update(self, last_gen, timeout):
        """等待 generation 变化（有新数据）。"""
        if self.generation != last_gen:
            return self.generation
        self._cond.wait(timeout=timeout)
        return self.generation


# ── 事件对象：写入端点 import 后调用 .set() 通知 SSE ──
_evt_active_games = GEvent()
_evt_queue = GEvent()
_evt_waiting_queue = GEvent()
_evt_matches = GEvent()
_evt_problem_matches = GEvent()
_evt_bracket = GEvent()

# 每个端点的共享缓存
_cache_active_games = CacheEntry(_evt_active_games)
_cache_queue = CacheEntry(_evt_queue)
_cache_waiting_queue = CacheEntry(_evt_waiting_queue)
_cache_matches = CacheEntry(_evt_matches)
_cache_problem_matches = CacheEntry(_evt_problem_matches)
_cache_bracket = CacheEntry(_evt_bracket)

# 向外暴露事件对象（cleanup.py 等模块 import 用）
evt_active_games = _evt_active_games
evt_queue = _evt_queue
evt_waiting_queue = _evt_waiting_queue
evt_matches = _evt_matches
evt_problem_matches = _evt_problem_matches
evt_bracket = _evt_bracket


def _sse_generate(fetch_fn, cache, poll_interval=10, max_lifetime=120):
    """通用 SSE 生成器：共享缓存版。

    cache: CacheEntry，event 触发时仅首个 greenlet 调用 fetch_fn()。
    """
    last_fingerprint = None
    last_heartbeat = time.time()
    start_time = time.time()
    last_gen = 0

    while True:
        try:
            if time.time() - start_time > max_lifetime:
                break

            # 等待事件触发（超时后兜底查一次）
            gen = cache.wait(timeout=poll_interval)

            # 有新数据（generation 变了）→ 直接用缓存
            if gen != last_gen:
                last_gen = gen
                data = cache.data
            else:
                # 兜底轮询：只有一个 greenlet 查库，其余等缓存
                if cache._lock.acquire(blocking=False):
                    try:
                        data = fetch_fn()
                        cache.update(data)
                        last_gen = cache.generation
                    finally:
                        cache._lock.release()
                else:
                    # 有其他 greenlet 正在查，等缓存更新
                    fresh_gen = cache.wait_for_update(gen, timeout=2)
                    if fresh_gen != gen:
                        last_gen = fresh_gen
                        data = cache.data
                    else:
                        # 超时，自己查
                        data = fetch_fn()
                        cache.update(data)
                        last_gen = cache.generation

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
    return Response(_sse_generate(fetch, _cache_active_games), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/queue")
def sse_queue():
    def fetch():
        db = get_db()
        queue = list(db.league_queue.find().sort("joinedAt", 1))
        return [{"name": q["name"]} for q in queue]
    return Response(_sse_generate(fetch, _cache_queue), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/waiting-queue")
def sse_waiting_queue():
    def fetch():
        db = get_db()
        groups = list(db.league_waiting_queue.find().sort("createdAt", 1))
        return [{"players": g.get("players", [])} for g in groups]
    return Response(_sse_generate(fetch, _cache_waiting_queue), mimetype="text/event-stream",
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
    return Response(_sse_generate(fetch, _cache_matches), mimetype="text/event-stream",
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
    return Response(_sse_generate(fetch, _cache_problem_matches), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/bracket")
def sse_bracket():
    def fetch():
        from routes_tournament import build_bracket_data
        return build_bracket_data()
    return Response(_sse_generate(fetch, _cache_bracket), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
