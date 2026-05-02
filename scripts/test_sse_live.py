#!/usr/bin/env python3
"""在线测试 SSE delta 端点（不需要 gevent，纯 requests）

用法：
  python test_sse_live.py http://localhost:5000
  python test_sse_live.py https://your-domain.com
"""

import json
import sys
import time

# 强制 stdout 立即刷新（SSE 测试需要实时看到输出）
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    HAS_REQUESTS = False

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5000"
BASE = BASE.rstrip("/")


def fetch_json(path):
    url = f"{BASE}{path}"
    if HAS_REQUESTS:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    else:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())


def read_sse(path, max_events=5, timeout=30):
    """读取 SSE 端点，返回 [(event_id, data), ...]"""
    url = f"{BASE}{path}"
    events = []

    if HAS_REQUESTS:
        with requests.get(url, stream=True, timeout=timeout,
                          headers={"Accept": "text/event-stream"}) as resp:
            current_id = None
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("id:"):
                    current_id = line[3:].strip()
                elif line.startswith("data:"):
                    data_str = line[5:].strip()
                    try:
                        data = json.loads(data_str)
                        events.append((current_id, data))
                        print(f"  📦 收到: type={data.get('type', '?') if isinstance(data, dict) else 'list'}")
                        sys.stdout.flush()
                    except json.JSONDecodeError:
                        pass
                if len(events) >= max_events:
                    break
    else:
        req = urllib.request.Request(url)
        req.add_header("Accept", "text/event-stream")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                current_id = None
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    line = line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line.startswith("id:"):
                        current_id = line[3:].strip()
                    elif line.startswith("data:"):
                        data_str = line[5:].strip()
                        try:
                            data = json.loads(data_str)
                            events.append((current_id, data))
                            print(f"  📦 收到: type={data.get('type', '?') if isinstance(data, dict) else 'list'}")
                            sys.stdout.flush()
                        except json.JSONDecodeError:
                            pass
                    if len(events) >= max_events:
                        break
        except Exception as e:
            print(f"  ⚠️ SSE 连接异常: {e}")
            sys.stdout.flush()

    return events


# ── 测试 ─────────────────────────────────────────────

print(f"🎯 目标: {BASE}")
print("=" * 60)
sys.stdout.flush()

# 0. 检查服务是否在线
try:
    info = fetch_json("/api/players")
    print(f"✅ 服务在线，排行榜 {len(info)} 人")
except Exception as e:
    print(f"❌ 无法连接: {e}")
    sys.exit(1)

# 1. 测试 bracket SSE：首次连接应收到 full
print("\n📡 测试 /api/events/bracket（首次连接）")
events = read_sse("/api/events/bracket", max_events=1, timeout=15)
if not events:
    print("  ⚠️ 未收到任何事件（可能没有赛事数据）")
else:
    event_id, data = events[0]
    print(f"  收到事件: id={event_id}")
    if isinstance(data, dict):
        msg_type = data.get("type")
        seq = data.get("seq")
        print(f"  type={msg_type}, seq={seq}")
        if msg_type == "full":
            bracket_data = data.get("data", {})
            tournaments = bracket_data.get("tournaments", [])
            total_groups = sum(
                len(r.get("groups", []))
                for t in tournaments
                for r in t.get("rounds", [])
            )
            print(f"  ✅ 全量推送: {len(tournaments)} 个赛事, {total_groups} 个组")
            size = len(json.dumps(data, ensure_ascii=False))
            print(f"  📊 数据大小: {size:,} bytes")
        elif msg_type == "delta":
            patches = data.get("patches", [])
            print(f"  ✅ Delta 推送: {len(patches)} 个 patch")
            size = len(json.dumps(data, ensure_ascii=False))
            print(f"  📊 数据大小: {size:,} bytes")
        else:
            print(f"  ❓ 未知 type: {msg_type}")
    else:
        print(f"  数据: {str(data)[:200]}")

# 2. 测试 bracket SSE：带 Last-Event-ID 重连
if events:
    last_id = events[0][0]
    if last_id and last_id != "0":
        print(f"\n📡 测试 /api/events/bracket（Last-Event-ID={last_id}）")
        events2 = read_sse(f"/api/events/bracket", max_events=1, timeout=15)
        if events2:
            eid2, data2 = events2[0]
            print(f"  收到事件: id={eid2}")
            if isinstance(data2, dict):
                print(f"  type={data2.get('type')}, seq={data2.get('seq')}")

# 3. 测试其他 SSE 端点
for endpoint in ["/api/events/active-games", "/api/events/matches"]:
    print(f"\n📡 测试 {endpoint}")
    evts = read_sse(endpoint, max_events=1, timeout=10)
    if evts:
        _, d = evts[0]
        if isinstance(d, list):
            print(f"  ✅ 收到 {len(d)} 条数据")
        elif isinstance(d, dict):
            print(f"  ✅ 收到: {list(d.keys())}")
    else:
        print(f"  ⚠️ 未收到数据")

# 4. 压力测试：快速开 3 个连接模拟多客户端
print("\n📡 压力测试：3 个并发 bracket SSE 连接")
import threading

results = []
def connect_sse(idx):
    try:
        evts = read_sse("/api/events/bracket", max_events=1, timeout=10)
        results.append((idx, len(evts), evts[0][1] if evts else None))
    except Exception as e:
        results.append((idx, 0, str(e)))

threads = [threading.Thread(target=connect_sse, args=(i,)) for i in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join(timeout=20)

for idx, count, data in results:
    if count > 0 and isinstance(data, dict):
        print(f"  连接 {idx}: ✅ type={data.get('type')}, seq={data.get('seq')}")
    else:
        print(f"  连接 {idx}: ⚠️ {data}")

# 5. 检查日志（提示用户）
print("\n" + "=" * 60)
print("📋 请检查服务端日志:")
print("   docker compose logs web | grep -i 'SSE\\|error\\|Hub'")
print("   如果没有 Hub 相关 error，说明修复生效 ✅")
print("=" * 60)
