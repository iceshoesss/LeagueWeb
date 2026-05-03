"""SSE（Server-Sent Events）实时推送端点 — 独立轮询 + 函数缓存版

每个 SSE 连接独立轮询，不共享可变状态，避免 gevent Hub 冲突。
通过函数级 TTL 缓存减少 MongoDB 查询：N 个连接在同一个缓存周期内只查一次。
"""

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


# ── 函数级 TTL 缓存 ─────────────────────────────────
def _ttl_cache(ttl_seconds):
    """装饰器：函数结果缓存 ttl_seconds 秒，线程安全。"""
    def decorator(fn):
        lock = threading.Lock()
        cached = {"data": None, "ts": 0}
        def wrapper(*args, **kwargs):
            now = time.time()
            if now - cached["ts"] < ttl_seconds:
                return cached["data"]
            with lock:
                if now - cached["ts"] < ttl_seconds:
                    return cached["data"]
                result = fn(*args, **kwargs)
                cached["data"] = result
                cached["ts"] = time.time()
                return result
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


# ── 轻量变更通知（时间戳，不参与 gevent 调度）──────────
class ChangeStamp:
    """写入端点调 .notify()，SSE 生成器用 .since() 判断是否有新数据。"""
    __slots__ = ('_ts',)
    def __init__(self):
        self._ts = 0.0
    def notify(self):
        self._ts = time.time()
    def since(self, last_check):
        return self._ts > last_check

stamp_active_games = ChangeStamp()
stamp_queue = ChangeStamp()
stamp_waiting_queue = ChangeStamp()
stamp_matches = ChangeStamp()
stamp_problem_matches = ChangeStamp()
stamp_bracket = ChangeStamp()

# 向外暴露通知对象（cleanup.py / routes_* import 用）
# 兼容旧接口：外部调用 evt_active_games.set()，映射到 notify()
class _CompatEvent:
    """兼容旧的 .set() 接口，内部转发给 ChangeStamp。"""
    __slots__ = ('_stamp',)
    def __init__(self, stamp):
        self._stamp = stamp
    def set(self):
        self._stamp.notify()
    def clear(self):
        pass  # 无需操作

evt_active_games = _CompatEvent(stamp_active_games)
evt_queue = _CompatEvent(stamp_queue)
evt_waiting_queue = _CompatEvent(stamp_waiting_queue)
evt_matches = _CompatEvent(stamp_matches)
evt_problem_matches = _CompatEvent(stamp_problem_matches)
evt_bracket = _CompatEvent(stamp_bracket)


# ── 带缓存的 fetch 函数 ──────────────────────────────

@_ttl_cache(5)
def _fetch_active_games():
    from data import get_active_games
    games = get_active_games()
    return [{"gameUuid": g.get("gameUuid", ""), "startedAtEpoch": g.get("startedAtEpoch"),
             "players": [{"displayName": p.get("displayName", ""), "heroCardId": p.get("heroCardId", ""),
                          "heroName": p.get("heroName", ""), "placement": p.get("placement")}
                         for p in g.get("players", [])]}
            for g in games]


@_ttl_cache(5)
def _fetch_queue():
    db = get_db()
    queue = list(db.league_queue.find().sort("joinedAt", 1))
    return [{"name": q["name"]} for q in queue]


@_ttl_cache(5)
def _fetch_waiting_queue():
    db = get_db()
    groups = list(db.league_waiting_queue.find().sort("createdAt", 1))
    return [{"players": g.get("players", [])} for g in groups]


@_ttl_cache(5)
def _fetch_matches():
    from data import get_completed_matches
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


@_ttl_cache(5)
def _fetch_problem_matches():
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


@_ttl_cache(5)
def _fetch_bracket():
    from routes_tournament import build_bracket_data
    return build_bracket_data()


# ── 通用 SSE 生成器（独立轮询）──────────────────────

def _sse_generate(fetch_fn, poll_interval=10, max_lifetime=120):
    """通用 SSE 生成器：每个连接独立轮询，不共享可变状态。"""
    last_fingerprint = None
    last_heartbeat = time.time()
    start_time = time.time()

    while True:
        try:
            if time.time() - start_time > max_lifetime:
                break

            data = fetch_fn()
            fingerprint = json.dumps(data, sort_keys=True, default=str)
            if fingerprint != last_fingerprint:
                last_fingerprint = fingerprint
                yield f"data: {fingerprint}\n\n"

            if time.time() - last_heartbeat > 30:
                yield ": heartbeat\n\n"
                last_heartbeat = time.time()

            gsleep(poll_interval)
        except GeneratorExit:
            break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            # 客户端断开连接，立即退出 greenlet 释放内存
            break
        except Exception as e:
            log.error(f"[SSE] error: {e}")
            gsleep(poll_interval)


# ── SSE 端点 ─────────────────────────────────────────

@sse_bp.route("/api/events/active-games")
def sse_active_games():
    return Response(_sse_generate(_fetch_active_games), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/queue")
def sse_queue():
    return Response(_sse_generate(_fetch_queue), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/waiting-queue")
def sse_waiting_queue():
    return Response(_sse_generate(_fetch_waiting_queue), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/matches")
def sse_matches():
    return Response(_sse_generate(_fetch_matches), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@sse_bp.route("/api/events/problem-matches")
def sse_problem_matches():
    return Response(_sse_generate(_fetch_problem_matches), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Bracket SSE：全量首推 + 后续 delta ──────────────

_bracket_seq = 0             # 全局递增序号
_bracket_deltas = []         # 环形缓冲 [{seq, patches}]
_BRACKET_DELTA_BUF = 50      # 保留最近 50 条 delta


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


def _sse_generate_bracket(initial_seq, poll_interval=5, max_lifetime=120):
    """Bracket 专用 SSE 生成器：首推全量 + 后续 delta（独立轮询版）。

    每个连接独立调用 _fetch_bracket()（函数级 TTL 缓存），
    对比本地 prev_data 计算 delta，不依赖共享 CacheEntry。
    """
    global _bracket_seq

    prev_data = None           # 本连接上次推送的全量数据
    prev_fingerprint = None    # 本连接上次推送的指纹
    last_heartbeat = time.time()
    start_time = time.time()
    last_full_sync = 0

    while True:
        try:
            if time.time() - start_time > max_lifetime:
                break

            # ── 获取最新数据（_fetch_bracket 内部有 5 秒缓存）──
            new_data = _fetch_bracket()
            fingerprint = json.dumps(new_data, sort_keys=True, default=str)

            # ── 计算 delta ──
            patches = _compute_delta(prev_data, new_data)

            if patches:
                _bracket_seq += 1
                delta_entry = {"seq": _bracket_seq, "patches": patches}
                _bracket_deltas.append(delta_entry)
                if len(_bracket_deltas) > _BRACKET_DELTA_BUF:
                    del _bracket_deltas[:len(_bracket_deltas) - _BRACKET_DELTA_BUF]

            # ── 判断推全量还是 delta ──
            now = time.time()
            need_full = (
                prev_data is None                          # 本连接首次推送
                or (now - last_full_sync) > 30             # 每 30 秒兜底全量
                or initial_seq < _bracket_seq - _BRACKET_DELTA_BUF  # seq 过旧
            )

            if need_full:
                if fingerprint != prev_fingerprint:
                    payload = {"type": "full", "seq": _bracket_seq, "data": new_data}
                    yield f"id: {_bracket_seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    prev_data = new_data
                    prev_fingerprint = fingerprint
                    last_full_sync = now
                    initial_seq = _bracket_seq
            elif patches:
                payload = {"type": "delta", "seq": _bracket_seq, "patches": patches}
                yield f"id: {_bracket_seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                prev_data = new_data
                prev_fingerprint = fingerprint
                last_full_sync = now
            elif initial_seq < _bracket_seq:
                # 补发缺失的 delta
                missed = [d for d in _bracket_deltas if d["seq"] > initial_seq]
                if missed:
                    for d in missed:
                        yield f"id: {d['seq']}\ndata: {json.dumps({'type': 'delta', 'seq': d['seq'], 'patches': d['patches']}, ensure_ascii=False)}\n\n"
                    initial_seq = _bracket_seq

            # ── 心跳 ──
            if time.time() - last_heartbeat > 30:
                yield ": heartbeat\n\n"
                last_heartbeat = time.time()

            # ── 等待下一轮 ──
            gsleep(poll_interval)

        except GeneratorExit:
            break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            break
        except Exception as e:
            log.error(f"[SSE-bracket] error: {e}")
            gsleep(poll_interval)


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
