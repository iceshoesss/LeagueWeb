#!/usr/bin/env python3
"""
淘汰赛测试脚本 — 自动创建赛事 + 模拟对局（随机排名）

用法：
  python test_knockout.py                          # 默认
  python test_knockout.py --base http://xxx:5000   # 指定 API
  python test_knockout.py --bo 5                   # BO5
  python test_knockout.py --group both             # AB 两组都打
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


def make_players(prefix, start_tag):
    """生成 16 个玩家（2 组 × 8 人）"""
    groups = [[], []]
    for gi in range(2):
        for i in range(8):
            idx = gi * 8 + i
            card_id, hero_name = HEROES[idx % len(HEROES)]
            groups[gi].append({
                "battleTag": f"{prefix}#{start_tag + idx}",
                "displayName": prefix,
                "accountIdLo": str(10000000 + start_tag + idx),
                "heroCardId": card_id,
                "heroName": hero_name,
            })
    return groups


def play_game(base, group, game_num, interval):
    """模拟一组打一局"""
    game_uuid = str(uuid.uuid4())
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # check-league
    players_detail = {}
    for p in group:
        players_detail[p["accountIdLo"]] = {
            "battleTag": p["battleTag"],
            "displayName": p["displayName"],
            "heroCardId": p["heroCardId"],
            "heroName": p["heroName"],
        }
    lo_list = [p["accountIdLo"] for p in group]

    body = {
        "playerId": group[0]["battleTag"],
        "gameUuid": game_uuid,
        "accountIdLo": group[0]["accountIdLo"],
        "accountIdLoList": lo_list,
        "players": players_detail,
        "mode": "solo",
        "region": "CN",
        "startedAt": started_at,
    }
    s, d = api("POST", f"{base}/api/plugin/check-league", json=body, headers=plugin_headers())
    step("check-league", s, d)
    if not d.get("isLeague"):
        print("  ❌ 未匹配到淘汰赛")
        return False

    # 随机排名
    placements = list(range(1, 9))
    random.shuffle(placements)

    for i, p in enumerate(group):
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
            break
        time.sleep(interval)

    return True


def run(base, prefix, start_tag, bo_n, interval, group_target):
    base = base.rstrip("/")
    admin_tag = f"{prefix}#{start_tag}"

    print("=" * 60)
    print(f"  淘汰赛测试 — BO{bo_n}")
    print(f"  API: {base}  插件版本: {PLUGIN_VER}  间隔 {interval}s")
    print("=" * 60)

    # ── 生成玩家 ──
    group_a, group_b = make_players(prefix, start_tag)
    all_players = group_a + group_b
    print(f"\n📦 生成 16 个玩家 (A 组 {len(group_a)} 人 + B 组 {len(group_b)} 人)")

    # ── Step 1: upload-rating ──
    print("\n📦 Step 1: upload-rating")
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
        time.sleep(0.1)

    # ── Step 2: register + login ──
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

    # ── Step 3: 创建赛事 ──
    print(f"\n🏆 Step 3: 创建赛事 (BO{bo_n})")
    admin_session = sessions.get(admin_tag)
    if not admin_session:
        print(f"  ❌ 管理员 {admin_tag} 未登录")
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
        print("  ❌ 创建失败，终止")
        return

    # ── Step 4: 打对局 ──
    targets = []
    if group_target in ("a", "both"):
        targets.append(("A", group_a))
    if group_target in ("b", "both"):
        targets.append(("B", group_b))

    for name, group in targets:
        for game in range(1, bo_n + 1):
            print(f"\n⚔️ {name} 组第 {game}/{bo_n} 局")
            if not play_game(base, group, game, interval):
                break
            time.sleep(1)

    print("\n" + "=" * 60)
    print("  测试完成!")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="淘汰赛测试")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API 地址")
    parser.add_argument("--prefix", default="衣锦夜行", help="玩家名前缀")
    parser.add_argument("--players", type=int, default=1000, help="起始编号")
    parser.add_argument("--bo", type=int, default=3, help="BO N")
    parser.add_argument("--interval", type=float, default=2.0, help="排名提交间隔秒数")
    parser.add_argument("--group", choices=["a", "b", "both"], default="a", help="打哪组 (默认 a)")
    args = parser.parse_args()
    run(args.base, args.prefix, args.players, args.bo, args.interval, args.group)


if __name__ == "__main__":
    main()
