"""SSE（Server-Sent Events）实时推送端点 — 独立轮询 + 函数缓存版

每个 SSE 连接独立轮询，不共享可变状态，避免 gevent Hub 冲突。
通过函数级 TTL 缓存减少 MongoDB 查询：N 个连接在同一个缓存周期内只查一次。
"""

import copy
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


# ── Bracket SSE：base 快照 + delta 环形缓冲 ──────────

_bracket_seq = 0             # 全局递增序号
_bracket_base = {"seq": 0, "data": None}   # 基础快照（深拷贝，不可变）
_bracket_deltas = []         # 环形缓冲 [{seq, patches}]
_BRACKET_DELTA_BUF = 50      # 保留最近 50 条 delta


def _group_key(tname, round_idx, group_index):
    """组的唯一标识"""
    return f"{tname}|{round_idx}|{group_index}"


def _extract_groups(data):
    """从 build_bracket_data() 输出提取 {key: group} 的扁平映射（深拷贝，不修改原数据）"""
    groups = {}
    for t in data.get("tournaments", []):
        tname = t.get("name", "")
        for r in t.get("rounds", []):
            ridx = r.get("round", 0)
            for g in r.get("groups", []):
                key = _group_key(tname, ridx, g.get("groupIndex", 0))
                groups[key] = copy.deepcopy(g)
    return groups


def _compute_delta(prev_data, new_data):
    """对比两次全量数据，返回变化的 group 列表（patches）。
    prev_data 和 new_data 不会被修改。"""
    prev_groups = _extract_groups(prev_data)
    new_groups = _extract_groups(new_data)

    patches = []
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
    for key in prev_groups:
        if key not in new_groups:
            parts = key.split("|")
            patches.append({
                "tournament": parts[0],
                "round": int(parts[1]),
                "groupIndex": int(parts[2]),
                "groupData": None
            })

    return patches if patches else None


def _apply_patches(base_data, patches):
    """将 patches 应用到 base_data，返回新数据（不修改 base_data）"""
    result = copy.deepcopy(base_data)
    groups = {}
    for t in result.get("tournaments", []):
        tname = t.get("name", "")
        for r in t.get("rounds", []):
            ridx = r.get("round", 0)
            for g in r.get("groups", []):
                key = _group_key(tname, ridx, g.get("groupIndex", 0))
                groups[key] = g

    for p in patches:
        key = _group_key(p["tournament"], p["round"], p["groupIndex"])
        if p["groupData"] is None:
            groups.pop(key, None)
        else:
            groups[key] = p["groupData"]

    # 从 groups 映射重建 tournaments 结构
    tournaments_map = {}
    for key, g in groups.items():
        parts = key.split("|")
        tname = parts[0]
        tournaments_map.setdefault(tname, []).append(g)

    result["tournaments"] = []
    for tname, tgroups in tournaments_map.items():
        rounds_map = {}
        for g in tgroups:
            rounds_map.setdefault(g.get("round", 1), []).append(g)
        rounds_data = []
        for r in sorted(rounds_map.keys()):
            rounds_data.append({"groups": sorted(rounds_map[r], key=lambda x: x.get("groupIndex", 0))})
        result["tournaments"].append({"name": tname, "rounds": rounds_data})

    return result


def _reconstruct_state(target_seq):
    """从 base 快照 replay deltas 到 target_seq，返回该时刻的全量数据。
    如果 target_seq 超出缓冲范围，返回 None。"""
    if target_seq < _bracket_base["seq"]:
        return None  # 比 base 还旧，无法恢复
    if target_seq == _bracket_base["seq"]:
        return copy.deepcopy(_bracket_base["data"])
    if target_seq > _bracket_seq:
        return None  # 超过当前最新

    data = copy.deepcopy(_bracket_base["data"])
    for d in _bracket_deltas:
        if d["seq"] <= target_seq:
            data = _apply_patches(data, d["patches"])
        else:
            break
    return data


def _advance_base():
    """缓冲区满时，把最旧的 delta 合并进 base"""
    global _bracket_base
    cut = len(_bracket_deltas) - _BRACKET_DELTA_BUF
    if cut <= 0:
        return
    for d in _bracket_deltas[:cut]:
        _bracket_base["data"] = _apply_patches(_bracket_base["data"], d["patches"])
        _bracket_base["seq"] = d["seq"]
    del _bracket_deltas[:cut]


def _sse_generate_bracket(initial_seq, poll_interval=5, max_lifetime=120):
    """Bracket SSE 生成器：base + delta 环形缓冲。

    客户端带 last_seq=X：
      - X 在缓冲内 → 从 base replay 到 X → 算 delta → 发 patches
      - X 不在缓冲内（或 X=0）→ 全量
    """
    global _bracket_seq

    prev_data = None           # 本连接上次推送的数据（用于本连接内算 delta）
    prev_fingerprint = None
    last_heartbeat = time.time()
    start_time = time.time()
    first_push = True

    while True:
        try:
            if time.time() - start_time > max_lifetime:
                break

            new_data = _fetch_bracket()
            new_data_copy = copy.deepcopy(new_data)  # 深拷贝，防止后续 mutation 影响快照
            fingerprint = json.dumps(new_data, sort_keys=True, default=str)

            # ── 计算 delta（基于本连接的 prev_data）──
            if prev_data is not None:
                patches = _compute_delta(prev_data, new_data_copy)
            else:
                patches = None

            if patches:
                _bracket_seq += 1
                _bracket_deltas.append({"seq": _bracket_seq, "patches": patches})
                if len(_bracket_deltas) > _BRACKET_DELTA_BUF:
                    _advance_base()

            # ── 首次推送：尝试 delta（last_seq 在缓冲内）──
            if first_push:
                first_push = False
                if initial_seq > 0 and initial_seq >= _bracket_base["seq"] and initial_seq < _bracket_seq:
                    # 从 base replay 到 initial_seq，得到客户端的旧状态
                    old_state = _reconstruct_state(initial_seq)
                    if old_state is not None:
                        delta_patches = _compute_delta(old_state, new_data_copy)
                        if delta_patches:
                            payload = {"type": "delta", "seq": _bracket_seq, "patches": delta_patches}
                            yield f"id: {_bracket_seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        # 即使 patches 为空（数据没变），也推全量以同步客户端状态
                        # 但避免重复：如果 delta 有内容就不需要全量
                        if delta_patches:
                            prev_data = new_data_copy
                            prev_fingerprint = fingerprint
                            continue
                        # patches 为空但客户端可能有旧数据 → 跳过，等下一轮变化

                # fallback：全量
                if fingerprint != prev_fingerprint:
                    payload = {"type": "full", "seq": _bracket_seq, "data": new_data}
                    yield f"id: {_bracket_seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    prev_data = new_data_copy
                    prev_fingerprint = fingerprint
                continue

            # ── 后续推送：delta 或无变化 ──
            if patches:
                payload = {"type": "delta", "seq": _bracket_seq, "patches": patches}
                yield f"id: {_bracket_seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                prev_data = new_data_copy
                prev_fingerprint = fingerprint
            elif initial_seq < _bracket_seq:
                # 补发缺失的 delta（本连接断过，但服务端有新数据）
                missed = [d for d in _bracket_deltas if d["seq"] > initial_seq]
                if missed:
                    for d in missed:
                        yield f"id: {d['seq']}\ndata: {json.dumps({'type': 'delta', 'seq': d['seq'], 'patches': d['patches']}, ensure_ascii=False)}\n\n"
                    initial_seq = _bracket_seq
                    prev_data = new_data_copy
                    prev_fingerprint = fingerprint

            # ── 心跳 ──
            if time.time() - last_heartbeat > 30:
                yield ": heartbeat\n\n"
                last_heartbeat = time.time()

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
    # 优先从 URL 参数读（客户端刷新时带 last_seq），fallback 到 Last-Event-ID（EventSource 自动重连）
    raw = request.args.get("last_seq") or request.headers.get("Last-Event-ID", "0")
    try:
        last_seq = int(raw)
    except (ValueError, TypeError):
        last_seq = 0
    return Response(_sse_generate_bracket(last_seq), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
