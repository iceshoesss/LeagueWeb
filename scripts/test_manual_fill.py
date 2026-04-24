#!/usr/bin/env python3
"""
手动补录测试 — BO2，7 人，3 人上传失败后停止

脚本只做到 4 人上传排名就停止，剩下 3 人由用户在网页上手动补录。
验证补录后能否正常进入 BO2 等待状态。

用法：
  python3 scripts/test_manual_fill.py
  python3 scripts/test_manual_fill.py --base http://xxx:5000
"""

import argparse
import json
import random
import time

import requests

DEFAULT_BASE = "http://127.0.0.1:5000"
PLUGIN_KEY = "YOUR_PLUGIN_KEY_HERE"
PLUGIN_VER = "1.1.0"

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
    return {"Content-Type": "application/json", "X-HDT-Plugin": PLUGIN_VER,
            "Authorization": f"Bearer {PLUGIN_KEY}"}


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


def make_players(prefix, start_tag, count=7):
    players = []
    for i in range(count):
        card_id, hero_name = HEROES[i % len(HEROES)]
        players.append({
            "battleTag": f"{prefix}#{start_tag + i}",
            "displayName": prefix,
            "accountIdLo": str(10000000 + start_tag + i),
            "heroCardId": card_id,
            "heroName": hero_name,
        })
    return players


def run(base, prefix, start_tag, admin_tag):
    base = base.rstrip("/")

    print("=" * 60)
    print("  手动补录测试 — BO2，7 人，3 人上传失败")
    print(f"  API: {base}  管理员: {admin_tag}")
    print("=" * 60)

    group = make_players(prefix, start_tag, count=7)
    print(f"\n📦 生成 {len(group)} 个测试玩家")
    for p in group:
        print(f"     {p['battleTag']}  Lo={p['accountIdLo']}")

    # ── upload-rating + register + login ──
    print("\n📦 准备: upload-rating + register + login")
    codes = {}
    sessions = {}
    all_for_upload = group.copy()
    if not any(p["battleTag"] == admin_tag for p in group):
        all_for_upload.append({
            "battleTag": admin_tag, "displayName": admin_tag.split("#")[0],
            "accountIdLo": str(10000000 + start_tag + 99),
            "heroCardId": "TB_BaconShop_HERO_14", "heroName": "瓦托格尔女王",
        })

    for p in all_for_upload:
        s, d = api("POST", f"{base}/api/plugin/upload-rating",
                    json={"playerId": p["battleTag"], "accountIdLo": p["accountIdLo"],
                          "rating": 6000, "mode": "solo", "region": "CN"},
                    headers=plugin_headers())
        if s == 200:
            codes[p["battleTag"]] = d.get("verificationCode", "123")
        time.sleep(0.05)

    for p in all_for_upload:
        s = requests.Session()
        code = codes.get(p["battleTag"], "123")
        api("POST", f"{base}/api/register", session=s,
            json={"battleTag": p["battleTag"], "verificationCode": code})
        api("POST", f"{base}/api/login", session=s,
            json={"battleTag": p["battleTag"], "verificationCode": code})
        sessions[p["battleTag"]] = s
        time.sleep(0.05)

    admin_session = sessions.get(admin_tag)
    if not admin_session:
        print(f"  ❌ 管理员 {admin_tag} 未登录")
        return

    # ── 创建赛事 BO2 ──
    print("\n🏆 创建赛事（7 人组，BO2）")
    s, d = api("POST", f"{base}/api/tournament/create", session=admin_session,
               json={"tournamentName": "补录测试", "rounds": [{
                   "round": 1, "boN": 2,
                   "groups": [{"groupIndex": 1, "players": group}],
               }]})
    step("创建赛事", s, d)
    if s != 200:
        print("  ❌ 创建失败"); return

    # ── check-league ──
    print("\n⚔️ 第 1 局: check-league")
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    game_los = [p["accountIdLo"] for p in group] + ["0"]
    game_uuid = None

    for i, p in enumerate(group):
        s, d = api("POST", f"{base}/api/plugin/check-league", json={
            "playerId": p["battleTag"], "accountIdLo": p["accountIdLo"],
            "accountIdLoList": game_los, "players": {
                pp["accountIdLo"]: {"battleTag": pp["battleTag"], "displayName": pp["displayName"],
                                    "heroCardId": pp["heroCardId"], "heroName": pp["heroName"]}
                for pp in group
            },
            "mode": "solo", "region": "CN", "startedAt": started_at,
        }, headers=plugin_headers())
        if i == 0:
            game_uuid = d.get("gameUuid")
        step(f"{p['battleTag'][-4:]} check-league", s, d)
        time.sleep(0.2)

    if not game_uuid:
        print("  ❌ 未获取到 gameUuid"); return

    # ── 前 4 人上传排名，后 3 人模拟失败 ──
    placements = list(range(1, 8))
    random.shuffle(placements)

    print(f"\n📤 前 4 人上传排名（后 3 人上传失败）")
    print(f"   gameUuid: {game_uuid}\n")
    for i in range(4):
        p = group[i]
        placement = placements[i]
        s, d = api("POST", f"{base}/api/plugin/update-placement", json={
            "playerId": p["battleTag"], "gameUuid": game_uuid,
            "accountIdLo": p["accountIdLo"], "placement": placement,
        }, headers=plugin_headers())
        step(f"第{placement}名 {p['battleTag']} (Lo={p['accountIdLo']})", s, d)
        time.sleep(0.3)

    print(f"\n{'─' * 60}")
    print(f"  ⚠️ 后 3 人上传失败，请在网页上手动补录：")
    print(f"{'─' * 60}")
    fail_players = group[4:]
    for i, p in enumerate(fail_players):
        print(f"    {p['battleTag']}  Lo={p['accountIdLo']}  (未上传)")
    print(f"\n  在管理后台找到对局 {game_uuid}")
    print(f"  为以上 3 人补录排名后，检查 BO 进度是否变为 gamesPlayed=1/2, status=waiting")
    print(f"{'─' * 60}")


def main():
    parser = argparse.ArgumentParser(description="手动补录测试")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API 地址")
    parser.add_argument("--prefix", default="补录选手", help="玩家名前缀")
    parser.add_argument("--players", type=int, default=10000, help="起始编号")
    parser.add_argument("--admin", default="衣锦夜行#1000", help="管理员 battleTag")
    args = parser.parse_args()
    run(args.base, args.prefix, args.players, args.admin)


if __name__ == "__main__":
    main()
