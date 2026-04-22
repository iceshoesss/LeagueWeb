#!/usr/bin/env python3
"""
淘汰赛测试脚本 — 创建赛事 + 模拟对局

模拟 16 个玩家（2 组 × 8 人，BO3），跑完 A 组第一局。

前置条件：
  1. Flask 服务运行中
  2. MongoDB 可达
  3. 第一个玩家（衣锦夜行#1000）是管理员：
     python manage_admins.py add "衣锦夜行#1000" --super

用法：
  python3 test_knockout.py                          # 默认 localhost:5000
  python3 test_knockout.py --base https://xxx.com   # 指定 API
  python3 test_knockout.py --bo 5                   # BO5
"""

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field

import requests

# ─── 配置 ───
DEFAULT_BASE = "http://localhost:5000"
PLUGIN_KEY = "YOUR_PLUGIN_KEY_HERE"
PLUGIN_VER = "1.1.0"

# ─── 英雄池 ───
HERO_POOL = [
    ("TB_BaconShop_HERO_14", "瓦托格尔女王"),
    ("TB_BaconShop_HERO_56", "阿莱克丝塔萨"),
    ("TB_BaconShop_HERO_01", "米尔豪斯·法力风暴"),
    ("TB_BaconShop_HERO_34", "拉卡尼休"),
    ("TB_BaconShop_HERO_18", "巫妖王"),
    ("TB_BaconShop_HERO_22", "风暴之王托里姆"),
    ("TB_BaconShop_HERO_55", "伊瑟拉"),
    ("TB_BaconShop_HERO_20", "帕奇维克"),
]


# ─── 工具函数 ───

def plugin_headers():
    return {
        "Content-Type": "application/json",
        "X-HDT-Plugin": PLUGIN_VER,
        "Authorization": f"Bearer {PLUGIN_KEY}",
    }


def api(method, url, session=None, **kwargs):
    s = session or requests
    r = s.request(method, url, timeout=15, **kwargs)
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text[:200]}
    return r.status_code, data


def step(label, status, data):
    icon = "✅" if 200 <= status < 300 else f"❌ {status}"
    detail = json.dumps(data, ensure_ascii=False)
    if len(detail) > 120:
        detail = detail[:117] + "..."
    print(f"  {label:30s} {icon:6s} {detail}")


def pick_hero(i):
    card_id, name = HERO_POOL[i % len(HERO_POOL)]
    return card_id, name


# ─── 主流程 ───

def run(base, prefix, bo_n, interval):
    base = base.rstrip("/")
    group_a = []
    group_b = []
    admin_tag = f"{prefix}#1000"
    admin_lo = "10000000"

    print("=" * 60)
    print(f"  淘汰赛测试 — {prefix}")
    print(f"  API: {base}  BO{bo_n}  间隔 {interval}s")
    print("=" * 60)

    # ── Step 0: 准备玩家数据 ──
    print("\n📦 Step 0: 生成玩家")
    for gi in range(2):
        players = []
        for i in range(8):
            idx = gi * 8 + i
            tag_num = 1000 + idx
            lo_num = 10000000 + idx
            card_id, hero_name = pick_hero(idx)
            players.append({
                "battleTag": f"{prefix}#{tag_num}",
                "displayName": prefix,
                "accountIdLo": str(lo_num),
                "heroCardId": card_id,
                "heroName": hero_name,
            })
        group_a if gi == 0 else group_b
        if gi == 0:
            group_a = players
        else:
            group_b = players

    all_players = group_a + group_b
    print(f"  A 组: {len(group_a)} 人, B 组: {len(group_b)} 人")

    # ── Step 1: upload-rating（所有 16 人）──
    print("\n📦 Step 1: upload-rating (16 人)")
    codes = {}
    for p in all_players:
        s, d = api("POST", f"{base}/api/plugin/upload-rating",
                    json={
                        "playerId": p["battleTag"],
                        "accountIdLo": p["accountIdLo"],
                        "rating": 6000,
                        "mode": "solo",
                        "region": "CN",
                    },
                    headers=plugin_headers())
        if s == 200:
            codes[p["battleTag"]] = d.get("verificationCode", "123")
        step(p["battleTag"], s, d)
        time.sleep(0.15)

    # ── Step 2: register + login（所有 16 人）──
    print("\n🔑 Step 2: register + login")
    sessions = {}
    for p in all_players:
        s = requests.Session()
        code = codes.get(p["battleTag"], "123")
        api("POST", f"{base}/api/register", session=s,
            json={"battleTag": p["battleTag"], "verificationCode": code})
        status, data = api("POST", f"{base}/api/login", session=s,
                           json={"battleTag": p["battleTag"], "verificationCode": code})
        if status == 200:
            sessions[p["battleTag"]] = s
        step(p["battleTag"], status, data)
        time.sleep(0.1)

    # ── Step 3: 创建赛事（管理员操作）──
    print(f"\n🏆 Step 3: 创建赛事 (BO{bo_n})")
    admin_session = sessions.get(admin_tag)
    if not admin_session:
        print(f"  ❌ 管理员 {admin_session} 未登录，跳过创建赛事")
        print(f"  💡 请先运行: python manage_admins.py add \"{admin_tag}\" --super")
        return

    rounds_data = [{
        "round": 1,
        "boN": bo_n,
        "groups": [
            {"groupIndex": 1, "players": group_a},
            {"groupIndex": 2, "players": group_b},
        ],
    }]

    s, d = api("POST", f"{base}/api/tournament/create", session=admin_session,
               json={"tournamentName": "测试锦标赛", "rounds": rounds_data})
    step("创建赛事", s, d)
    if s != 200:
        print("  ❌ 创建赛事失败，终止")
        return

    # ── Step 4: A 组第一局 ──
    print(f"\n⚔️ Step 4: A 组第 1 局 (BO{bo_n})")
    game_uuid = str(uuid.uuid4())
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    players_detail = {}
    for p in group_a:
        players_detail[p["accountIdLo"]] = {
            "battleTag": p["battleTag"],
            "displayName": p["displayName"],
            "heroCardId": p["heroCardId"],
            "heroName": p["heroName"],
        }
    lo_list = [p["accountIdLo"] for p in group_a]

    # 4a: check-league
    body = {
        "playerId": group_a[0]["battleTag"],
        "gameUuid": game_uuid,
        "accountIdLo": group_a[0]["accountIdLo"],
        "accountIdLoList": lo_list,
        "players": players_detail,
        "mode": "solo",
        "region": "CN",
        "startedAt": started_at,
    }
    s, d = api("POST", f"{base}/api/plugin/check-league", json=body, headers=plugin_headers())
    step("check-league", s, d)
    is_league = d.get("isLeague", False)
    print(f"  isLeague: {is_league}")
    if not is_league:
        print("  ❌ 未匹配到淘汰赛，终止")
        return

    # 4b: update-placement（每个玩家间隔 2 秒）
    print(f"\n📊 update-placement (间隔 {interval}s)")
    for i, p in enumerate(group_a):
        placement = i + 1
        s, d = api("POST", f"{base}/api/plugin/update-placement",
                    json={
                        "playerId": p["battleTag"],
                        "gameUuid": game_uuid,
                        "accountIdLo": p["accountIdLo"],
                        "placement": placement,
                    },
                    headers=plugin_headers())
        finalized = d.get("finalized", False)
        label = f"第{placement}名 {p['battleTag']}"
        if finalized:
            d["_note"] = "🎉 对局结束!"
        step(label, s, d)
        if finalized:
            print("  ✅ finalize，停止")
            break
        time.sleep(interval)

    # ── Step 5: 查看结果 ──
    print("\n📋 Step 5: 查看赛事状态")
    s, d = api("GET", f"{base}/api/tournaments")
    if s == 200:
        for t in d if isinstance(d, list) else [d]:
            print(f"\n  赛事: {t.get('tournamentName', '?')}")
            for rnd in t.get("rounds", []):
                print(f"  第 {rnd.get('round')} 轮:")
                for g in rnd.get("groups", []):
                    status = g.get("status", "?")
                    gp = g.get("gamesPlayed", 0)
                    bo = g.get("boN", 1)
                    print(f"    组 {g.get('groupIndex')}: {status} ({gp}/{bo})")
                    for p in g.get("players", []):
                        name = p.get("displayName", p.get("battleTag", "?"))
                        pts = p.get("totalPoints", p.get("points", "-"))
                        empty = " [空]" if p.get("empty") else ""
                        print(f"      {name}{empty}  积分:{pts}")

    # 查看 bracket
    s2, d2 = api("GET", f"{base}/api/bracket")
    if s2 == 200:
        print("\n  🖼️ 对战图数据已更新（访问 /bracket 查看）")

    print("\n" + "=" * 60)
    print("  测试完成!")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="淘汰赛测试")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API 地址")
    parser.add_argument("--prefix", default="衣锦夜行", help="玩家名前缀")
    parser.add_argument("--bo", type=int, default=3, help="BO N (默认 3)")
    parser.add_argument("--interval", type=float, default=2.0, help="排名提交间隔秒数")
    args = parser.parse_args()
    run(args.base, args.prefix, args.bo, args.interval)


if __name__ == "__main__":
    main()
