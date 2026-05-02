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
        """等待事件触发，返回当前 generation。不清除 event，所有 greenlet 都能看到。"""
        self.event.wait(timeout=timeout)
        # 不 clear！多个 greenlet 共享同一 event，clear 会导致后续 greenlet 阻塞。
        # 用 generation 计数器判断是否有新数据。
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
            # event 不 clear，用 generation 判断是否有新数据
            gen = cache.wait(timeout=poll_interval)

            if gen != last_gen:
                # 有新数据 → 直接用缓存，不清 event
                last_gen = gen
                data = cache.data
            else:
                # generation 未变 → 兜底轮询：仅一个 greenlet 查库
                # 先 clear event（如果还 set 着），让下次 wait 能真正阻塞
                cache.event.clear()
                if cache._lock.acquire(blocking=False):
                    try:
                        data = fetch_fn()
                        cache.update(data)
                        last_gen = cache.generation
                    finally:
                        cache._lock.release()
                else:
                    # 其他 greenlet 正在查，等缓存更新
                    for _ in range(20):  # 最多等 2 秒
                        gsleep(0.1)
                        if cache.generation != gen:
                            break
                    if cache.generation != gen:
                        last_gen = cache.generation
                        data = cache.data
                    else:
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
    from flask import request
    last_id = request.headers.get("Last-Event-ID", "0")
    try:
        last_seq = int(last_id)
    except (ValueError, TypeError):
        last_seq = 0
    return Response(_sse_generate_bracket(last_seq), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Bracket SSE：全量首推 + 后续 delta ──
_bracket_prev_data = None    # 上次全量数据
_bracket_seq = 0             # 递增序号
_bracket_deltas = []         # 环形缓冲 [{seq, patches}]
_BRACKET_DELTA_BUF = 50      # 保留最近 50 条 delta
_bracket_prev_fingerprint = None  # 防重复推送


def _group_key(tname, round_idx, group_index):
    """组的唯一标识"""
    return f"{tname}|{round_idx}|{group_index}"


def _extract_groups(data):
    """从 build_bracket_data() 输出提取 {key: group} 的扁平映射"""
    groups = {}
    for t in data.get("tournaments", []):
        tname = t.get("name", "")
        for r in t.get("rounds", []):
            ridx = r.get("round", 0)
            for g in r.get("groups", []):
                key = _group_key(tname, ridx, g.get("groupIndex", 0))
                groups[key] = g
    return groups


def _compute_delta(prev_data, new_data):
    """对比两次全量数据，返回变化的 group 列表（patches）"""
    if prev_data is None:
        return None  # 首次，需要全量

    prev_groups = _extract_groups(prev_data)
    new_groups = _extract_groups(new_data)

    patches = []
    # 检查新增和修改
    for key, ng in new_groups.items():
        og = prev_groups.get(key)
        if og is None or json.dumps(og, sort_keys=True, default=str) != json.dumps(ng, sort_keys=True, default=str):
            # 解析 key 获取元信息
            parts = key.split("|")
            patches.append({
                "tournament": parts[0],
                "round": int(parts[1]),
                "groupIndex": int(parts[2]),
                "groupData": ng
            })
    # 检查删除（某组消失了，比如赛事被删除）
    for key in prev_groups:
        if key not in new_groups:
            parts = key.split("|")
            patches.append({
                "tournament": parts[0],
                "round": int(parts[1]),
                "groupIndex": int(parts[2]),
                "groupData": None  # null 表示删除
            })

    return patches if patches else None


def _sse_generate_bracket(initial_seq, poll_interval=10, max_lifetime=120):
    """Bracket 专用 SSE 生成器：首推全量 + 后续 delta。

    initial_seq: 客户端 Last-Event-ID，用于判断是否需要补发 delta。
    """
    global _bracket_prev_data, _bracket_seq, _bracket_prev_fingerprint

    from routes_tournament import build_bracket_data

    last_heartbeat = time.time()
    start_time = time.time()
    last_gen = 0
    last_full_sync = 0
    first_run = True  # 首次迭代立即推数据，不等事件

    while True:
        try:
            if time.time() - start_time > max_lifetime:
                break

            if first_run:
                first_run = False
                # 首次连接：立即获取数据，不等事件
                gen = _cache_bracket.generation
            else:
                gen = _cache_bracket.wait(timeout=poll_interval)

            # ── 获取最新数据（共享缓存，仅首个 greenlet 查库）──
            if gen != last_gen and _cache_bracket.data is not None:
                last_gen = gen
                new_data = _cache_bracket.data
            else:
                _cache_bracket.event.clear()
                if _cache_bracket._lock.acquire(blocking=False):
                    try:
                        new_data = build_bracket_data()
                        _cache_bracket.update(new_data)
                        last_gen = _cache_bracket.generation
                    finally:
                        _cache_bracket._lock.release()
                else:
                    for _ in range(20):
                        gsleep(0.1)
                        if _cache_bracket.generation != gen:
                            break
                    if _cache_bracket.generation != gen:
                        last_gen = _cache_bracket.generation
                        new_data = _cache_bracket.data
                    else:
                        new_data = build_bracket_data()
                        _cache_bracket.update(new_data)
                        last_gen = _cache_bracket.generation

            # ── 计算 delta ──
            patches = _compute_delta(_bracket_prev_data, new_data)

            if patches:
                _bracket_seq += 1
                delta_entry = {"seq": _bracket_seq, "patches": patches}
                _bracket_deltas.append(delta_entry)
                # 环形缓冲
                if len(_bracket_deltas) > _BRACKET_DELTA_BUF:
                    del _bracket_deltas[:len(_bracket_deltas) - _BRACKET_DELTA_BUF]

            # ── 判断推全量还是 delta ──
            now = time.time()
            need_full = (
                initial_seq == 0                          # 首次连接
                or _bracket_prev_data is None             # 服务端还没缓存
                or (now - last_full_sync) > 30            # 每 30 秒兜底全量
                or initial_seq < _bracket_seq - _BRACKET_DELTA_BUF  # seq 过旧
            )

            if need_full:
                # ── 推全量 ──
                payload = {"type": "full", "seq": _bracket_seq, "data": new_data}
                fingerprint = json.dumps(new_data, sort_keys=True, default=str)
                if fingerprint != _bracket_prev_fingerprint:
                    _bracket_prev_fingerprint = fingerprint
                    _bracket_prev_data = new_data
                    last_full_sync = now
                    yield f"id: {_bracket_seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    initial_seq = _bracket_seq  # 已同步，后续走 delta
            elif patches:
                # ── 推 delta ──
                payload = {"type": "delta", "seq": _bracket_seq, "patches": patches}
                _bracket_prev_data = new_data
                _bracket_prev_fingerprint = json.dumps(new_data, sort_keys=True, default=str)
                last_full_sync = now
                yield f"id: {_bracket_seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            elif initial_seq < _bracket_seq:
                # ── 客户端落后但没有新 patches（比如兜底轮询触发但数据没变）──
                # 补发客户端缺失的 delta
                missed = [d for d in _bracket_deltas if d["seq"] > initial_seq]
                if missed:
                    for d in missed:
                        yield f"id: {d['seq']}\ndata: {json.dumps({'type': 'delta', 'seq': d['seq'], 'patches': d['patches']}, ensure_ascii=False)}\n\n"
                    initial_seq = _bracket_seq
                # else: 数据没变，不推

            # ── 心跳 ──
            if time.time() - last_heartbeat > 30:
                yield ": heartbeat\n\n"
                last_heartbeat = time.time()

        except GeneratorExit:
            break
        except Exception as e:
            log.error(f"[SSE-bracket] error: {e}")
            gsleep(poll_interval)
