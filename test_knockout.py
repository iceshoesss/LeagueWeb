#!/usr/bin/env python3
"""
淘汰赛测试脚本 — 模拟 8 人对局（随机排名）

前置条件：
  1. Flask 服务运行中
  2. 管理员已在网站创建好赛事 + 分组

用法：
  python3 test_knockout.py                          # 默认 localhost:5000
  python3 test_knockout.py --base https://xxx.com   # 指定 API
  python3 test_knockout.py --players 0008           # 自定义起始编号
"""

import argparse
import json
import random
import time
import uuid

import requests

# ─── 配置 ───
DEFAULT_BASE = "http://127.0.0.1:5000"
PLUGIN_KEY = "YOUR_PLUGIN_KEY_HERE"
PLUGIN_VER = "1.1.0"

# ─── 英雄池 ───
HEROES = [
    ("TB_BaconShop_HERO_14", "瓦托格尔女王"),
    ("TB_BaconShop_HERO_56", "阿莱克丝塔萨"),
    ("TB_BaconShop_HERO_01", "米尔豪斯·法力风暴"),
    ("TB_BaconShop_HERO_34", "拉卡尼休"),
    ("TB_BaconShop_HERO_18", "巫妖王"),
    ("TB_BaconShop_HERO_22", "风暴之王托里姆"),
    ("TB_BaconShop_HERO_55", "伊瑟拉"),
    ("TB_BaconShop_HERO_20", "帕奇维克"),
]


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


def run(base, prefix, start_tag, interval):
    base = base.rstrip("/")

    print("=" * 60)
    print(f"  淘汰赛测试 — 模拟对局")
    print(f"  API: {base}  插件版本: {PLUGIN_VER}  间隔 {interval}s")
    print("=" * 60)

    # ── 生成 8 个玩家 ──
    print(f"\n📦 生成 8 个玩家 ({prefix}#{start_tag} ~ #{start_tag + 7})")
    players = []
    for i in range(8):
        card_id, hero_name = HEROES[i]
        players.append({
            "battleTag": f"{prefix}#{start_tag + i}",
            "displayName": prefix,
            "accountIdLo": str(10000000 + start_tag + i),
            "heroCardId": card_id,
            "heroName": hero_name,
        })
        print(f"  {players[-1]['battleTag']}  Lo={players[-1]['accountIdLo']}  {hero_name}")

    # ── Step 1: upload-rating ──
    print("\n📦 Step 1: upload-rating")
    codes = {}
    for p in players:
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

    # ── Step 2: register + login ──
    print("\n🔑 Step 2: register + login")
    sessions = {}
    for p in players:
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

    # ── Step 3: check-league ──
    print("\n🏁 Step 3: check-league")
    game_uuid = str(uuid.uuid4())
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    players_detail = {}
    for p in players:
        players_detail[p["accountIdLo"]] = {
            "battleTag": p["battleTag"],
            "displayName": p["displayName"],
            "heroCardId": p["heroCardId"],
            "heroName": p["heroName"],
        }
    lo_list = [p["accountIdLo"] for p in players]

    body = {
        "playerId": players[0]["battleTag"],
        "gameUuid": game_uuid,
        "accountIdLo": players[0]["accountIdLo"],
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

    # ── Step 4: update-placement（随机排名）──
    placements = list(range(1, 9))
    random.shuffle(placements)
    print(f"\n📊 Step 4: update-placement (间隔 {interval}s)")
    print(f"  随机排名: {placements}")

    for i, p in enumerate(players):
        placement = placements[i]
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

    print("\n" + "=" * 60)
    print("  测试完成!")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="淘汰赛测试 — 模拟对局")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API 地址")
    parser.add_argument("--prefix", default="衣锦夜行", help="玩家名前缀")
    parser.add_argument("--players", type=int, default=1000, help="起始编号 (默认 1000)")
    parser.add_argument("--interval", type=float, default=2.0, help="排名提交间隔秒数")
    args = parser.parse_args()
    run(args.base, args.prefix, args.players, args.interval)


if __name__ == "__main__":
    main()
