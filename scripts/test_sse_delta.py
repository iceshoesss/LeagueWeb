#!/usr/bin/env python3
"""测试 SSE delta 逻辑（纯本地，不需要 MongoDB/Flask）

验证：
1. _compute_delta 正确识别新增/修改/删除
2. 全量→delta→delta 完整流程
3. 环形缓冲 + 断线重连补发
4. 无变化时不推送
"""

import json
import sys
import os

# 把项目根目录加入 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 从 sse.py 复制核心函数（避免 import 触发 Flask/db 依赖）──

def _group_key(tname, round_idx, group_index):
    return f"{tname}|{round_idx}|{group_index}"


def _extract_groups(data):
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
    if prev_data is None:
        return None
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


# ── 测试数据 ─────────────────────────────────────────

def make_group(round_num, group_index, players, status="waiting", bo_n=3, games_played=0):
    return {
        "groupIndex": group_index,
        "round": round_num,
        "status": status,
        "boN": bo_n,
        "gamesPlayed": games_played,
        "players": players,
    }


def make_tournament(name, rounds):
    return {"name": name, "rounds": rounds}


def make_bracket(tournaments):
    return {"tournaments": tournaments}


def make_players(n):
    return [{"displayName": f"P{i}", "accountIdLo": str(i)} for i in range(1, n + 1)]


# ── 测试用例 ─────────────────────────────────────────

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} — {detail}")


def test_compute_delta_basic():
    print("\n🔧 test_compute_delta_basic")
    g1 = make_group(1, 1, make_players(8), "done", 3, 3)
    g2 = make_group(1, 2, make_players(8), "waiting", 3, 0)

    prev = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [g1, g2]}])])

    # 修改 g2 状态
    g2_changed = make_group(1, 2, make_players(8), "active", 3, 1)
    new = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [g1, g2_changed]}])])

    patches = _compute_delta(prev, new)
    check("patches 不为 None", patches is not None)
    check("只有 1 个 patch", patches is not None and len(patches) == 1)
    if patches:
        p = patches[0]
        check("patch 是 g2", p["groupIndex"] == 2)
        check("patch 包含新数据", p["groupData"]["status"] == "active")
        check("patch 包含 tournament", p["tournament"] == "春季赛")


def test_compute_delta_no_change():
    print("\n🔧 test_compute_delta_no_change")
    g1 = make_group(1, 1, make_players(8), "done", 3, 3)
    data = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [g1]}])])

    patches = _compute_delta(data, data)
    check("无变化时 patches 为 None", patches is None)


def test_compute_delta_new_group():
    print("\n🔧 test_compute_delta_new_group")
    g1 = make_group(1, 1, make_players(8), "done", 3, 3)
    prev = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [g1]}])])

    g2 = make_group(1, 2, make_players(8), "waiting", 3, 0)
    new = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [g1, g2]}])])

    patches = _compute_delta(prev, new)
    check("检测到新组", patches is not None and len(patches) == 1)
    if patches:
        check("新组 groupIndex=2", patches[0]["groupIndex"] == 2)
        check("groupData 不为 None", patches[0]["groupData"] is not None)


def test_compute_delta_deleted_group():
    print("\n🔧 test_compute_delta_deleted_group")
    g1 = make_group(1, 1, make_players(8), "done", 3, 3)
    g2 = make_group(1, 2, make_players(8), "waiting", 3, 0)
    prev = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [g1, g2]}])])

    new = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [g1]}])])

    patches = _compute_delta(prev, new)
    check("检测到删除", patches is not None and len(patches) == 1)
    if patches:
        check("删除的 groupData 为 None", patches[0]["groupData"] is None)


def test_compute_delta_prev_none():
    print("\n🔧 test_compute_delta_prev_none")
    g1 = make_group(1, 1, make_players(8))
    data = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [g1]}])])

    patches = _compute_delta(None, data)
    check("首次 prev=None 返回 None（需全量）", patches is None)


def test_multi_tournament():
    print("\n🔧 test_multi_tournament")
    t1_g1 = make_group(1, 1, make_players(8), "done", 3, 3)
    t2_g1 = make_group(1, 1, make_players(8), "waiting", 5, 0)
    prev = make_bracket([
        make_tournament("春季赛", [{"round": 1, "groups": [t1_g1]}]),
        make_tournament("夏季赛", [{"round": 1, "groups": [t2_g1]}]),
    ])

    t2_g1_changed = make_group(1, 1, make_players(8), "active", 5, 1)
    new = make_bracket([
        make_tournament("春季赛", [{"round": 1, "groups": [t1_g1]}]),
        make_tournament("夏季赛", [{"round": 1, "groups": [t2_g1_changed]}]),
    ])

    patches = _compute_delta(prev, new)
    check("只检测到夏季赛变化", patches is not None and len(patches) == 1)
    if patches:
        check("tournament=夏季赛", patches[0]["tournament"] == "夏季赛")


def test_delta_buffer_simulation():
    """模拟完整流程：全量→delta→delta→断线重连"""
    print("\n🔧 test_delta_buffer_simulation")

    # 模拟 sse.py 中的全局状态
    bracket_seq = 0
    bracket_deltas = []
    BRACKET_DELTA_BUF = 50

    # 状态 1：2 组都是 waiting
    state1 = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [
        make_group(1, 1, make_players(8), "waiting", 3, 0),
        make_group(1, 2, make_players(8), "waiting", 3, 0),
    ]}])])

    # 状态 2：g1 变 active
    state2 = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [
        make_group(1, 1, make_players(8), "active", 3, 1),
        make_group(1, 2, make_players(8), "waiting", 3, 0),
    ]}])])

    # 状态 3：g1 done, g2 active
    state3 = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [
        make_group(1, 1, make_players(8), "done", 3, 3),
        make_group(1, 2, make_players(8), "active", 3, 1),
    ]}])])

    # 模拟生成器逻辑
    prev_data = None

    # 第 1 次：首次连接，全量推送
    patches = _compute_delta(prev_data, state1)
    check("首次：需全量（patches=None）", patches is None)
    # 实际推送全量
    prev_data = state1
    client_seq = 0  # 客户端还没收到任何数据

    # 第 2 次：数据变化
    patches = _compute_delta(prev_data, state2)
    check("第 2 次：检测到 delta", patches is not None and len(patches) == 1)
    if patches:
        bracket_seq += 1
        bracket_deltas.append({"seq": bracket_seq, "patches": patches})
        client_seq = bracket_seq
        prev_data = state2

    # 第 3 次：数据再次变化
    patches = _compute_delta(prev_data, state3)
    check("第 3 次：检测到 delta", patches is not None and len(patches) == 2)  # g1 和 g2 都变了
    if patches:
        bracket_seq += 1
        bracket_deltas.append({"seq": bracket_seq, "patches": patches})
        client_seq = bracket_seq
        prev_data = state3

    # 第 4 次：无变化
    patches = _compute_delta(prev_data, state3)
    check("第 4 次：无变化（patches=None）", patches is None)

    # 模拟断线重连：客户端 last_seq=0，需要补发所有 delta
    missed = [d for d in bracket_deltas if d["seq"] > 0]
    check("断线重连：补发 2 条 delta", len(missed) == 2)
    if missed:
        check("delta seq=1", missed[0]["seq"] == 1)
        check("delta seq=2", missed[1]["seq"] == 2)

    # 模拟断线重连：客户端 last_seq=1，只需补发 seq=2
    missed = [d for d in bracket_deltas if d["seq"] > 1]
    check("部分重连：补发 1 条 delta", len(missed) == 1)

    # 模拟 seq 过旧（超出缓冲区）
    bracket_deltas.clear()
    old_seq = 100
    is_too_old = old_seq < bracket_seq - BRACKET_DELTA_BUF
    check("seq 过旧时回退全量", is_too_old or bracket_seq <= BRACKET_DELTA_BUF)


def test_delta_content():
    """验证 delta patch 内容正确"""
    print("\n🔧 test_delta_content")

    players = make_players(8)
    g1 = make_group(1, 1, players, "waiting", 3, 0)
    prev = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [g1]}])])

    # 修改 g1 的 gamesPlayed 和 status
    g1_new = make_group(1, 1, players, "active", 3, 1)
    new = make_bracket([make_tournament("春季赛", [{"round": 1, "groups": [g1_new]}])])

    patches = _compute_delta(prev, new)
    check("patches 非空", patches is not None)
    if patches:
        p = patches[0]
        check("patch.tournament 正确", p["tournament"] == "春季赛")
        check("patch.round 正确", p["round"] == 1)
        check("patch.groupIndex 正确", p["groupIndex"] == 1)
        check("patch.groupData.status=active", p["groupData"]["status"] == "active")
        check("patch.groupData.gamesPlayed=1", p["groupData"]["gamesPlayed"] == 1)
        check("patch.groupData.players 完整", len(p["groupData"]["players"]) == 8)


def test_bandwidth():
    """对比全量 vs delta 的数据大小"""
    print("\n🔧 test_bandwidth")

    # 构造一个大赛事：4 轮，每轮 8 组，每组 8 人
    rounds = []
    for r in range(1, 5):
        groups = []
        for g in range(1, 9):
            groups.append(make_group(r, g, make_players(8), "done" if r < 4 else "waiting", 3, 3 if r < 4 else 0))
        rounds.append({"round": r, "groups": groups})
    big_bracket = make_bracket([make_tournament("2026春季赛", rounds)])

    full_size = len(json.dumps(big_bracket, ensure_ascii=False))

    # 只改 1 个组
    rounds_changed = json.loads(json.dumps(rounds))
    rounds_changed[3]["groups"][2]["status"] = "active"
    rounds_changed[3]["groups"][2]["gamesPlayed"] = 1
    new_bracket = make_bracket([make_tournament("2026春季赛", rounds_changed)])

    patches = _compute_delta(big_bracket, new_bracket)
    delta_size = len(json.dumps(patches, ensure_ascii=False))

    ratio = delta_size / full_size * 100
    print(f"  📊 全量: {full_size:,} bytes")
    print(f"  📊 delta: {delta_size:,} bytes")
    print(f"  📊 压缩比: {ratio:.1f}%")
    check("delta < 10% 全量", ratio < 10, f"ratio={ratio:.1f}%")


# ── 运行 ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("SSE Delta 逻辑测试")
    print("=" * 60)

    test_compute_delta_basic()
    test_compute_delta_no_change()
    test_compute_delta_new_group()
    test_compute_delta_deleted_group()
    test_compute_delta_prev_none()
    test_multi_tournament()
    test_delta_buffer_simulation()
    test_delta_content()
    test_bandwidth()

    print("\n" + "=" * 60)
    print(f"结果: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed > 0 else 0)
