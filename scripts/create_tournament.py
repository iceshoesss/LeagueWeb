#!/usr/bin/env python3
"""
创建赛事脚本 — 淘汰赛（bracket）或 海选赛（grid）

用法：
  python scripts/create_tournament.py bracket              # 16 进 8，2 组 × 8 人
  python scripts/create_tournament.py bracket --players 32 # 32 进 16，4 组 × 8 人
  python scripts/create_tournament.py grid                 # 4 组 × 8 人海选
  python scripts/create_tournament.py grid --groups 2      # 2 组 × 8 人海选
  python scripts/create_tournament.py bracket --bo 3       # BO3
  python scripts/create_tournament.py grid --bo 2          # BO2
"""

import argparse
import sys
import time

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


def make_players(prefix, count, start_tag=1000):
    players = []
    for i in range(count):
        card_id, hero_name = HEROES[i % len(HEROES)]
        tag = start_tag + i
        players.append({
            "battleTag": f"{prefix}#{tag}",
            "displayName": prefix,
            "accountIdLo": str(10000000 + tag),
            "heroCardId": card_id,
            "heroName": hero_name,
        })
    return players


def split_groups(players, group_size=8):
    return [players[i:i + group_size] for i in range(0, len(players), group_size)]


def step(label, status, data):
    icon = "✅" if 200 <= status < 300 else f"❌ {status}"
    import json
    detail = json.dumps(data, ensure_ascii=False)
    if len(detail) > 120:
        detail = detail[:117] + "..."
    print(f"  {label:30s} {icon:6s} {detail}")


def setup_players(base, all_players, admin_tag):
    """upload-rating + register + login，返回 sessions dict"""
    # upload-rating
    print("\n📦 upload-rating")
    codes = {}
    upload_list = all_players.copy()
    admin_in = any(p["battleTag"] == admin_tag for p in all_players)
    if not admin_in:
        upload_list.append({
            "battleTag": admin_tag,
            "displayName": admin_tag.split("#")[0],
            "accountIdLo": str(10000000 + 99999),
            "heroCardId": HEROES[0][0],
            "heroName": HEROES[0][1],
        })

    for p in upload_list:
        s, d = api("POST", f"{base}/api/plugin/upload-rating",
                    json={"playerId": p["battleTag"], "accountIdLo": p["accountIdLo"],
                           "rating": 6000, "mode": "solo", "region": "CN"},
                    headers=plugin_headers())
        if s == 200:
            codes[p["battleTag"]] = d.get("verificationCode", "123")
        step(p["battleTag"], s, d)
        time.sleep(0.05)

    # register + login
    print("\n🔑 register + login")
    sessions = {}
    for p in upload_list:
        s = requests.Session()
        code = codes.get(p["battleTag"], "123")
        api("POST", f"{base}/api/register", session=s,
            json={"battleTag": p["battleTag"], "verificationCode": code})
        status, data = api("POST", f"{base}/api/login", session=s,
                           json={"battleTag": p["battleTag"], "verificationCode": code})
        if status == 200:
            sessions[p["battleTag"]] = s
        step(p["battleTag"], status, data)
        time.sleep(0.05)

    return sessions


def run(base, mode, total_players, bo_n, admin_tag, num_groups, name):
    base = base.rstrip("/")

    # 计算分组
    if mode == "bracket":
        all_p = make_players("选手", total_players, start_tag=1000)
        groups = split_groups(all_p, 8)
        num_groups = len(groups)
        layout = None  # 默认 bracket
    else:
        group_size = 8
        total_players = num_groups * group_size
        all_p = make_players("选手", total_players, start_tag=1000)
        groups = split_groups(all_p, group_size)
        layout = "grid"

    print("=" * 60)
    print(f"  创建{('淘汰赛' if mode == 'bracket' else '海选赛')} — {name}")
    print(f"  {num_groups} 组 × 8 人, BO{bo_n}")
    print(f"  API: {base}  管理员: {admin_tag}")
    print("=" * 60)

    print(f"\n📦 {len(all_p)} 个测试玩家")
    for i, g in enumerate(groups):
        print(f"  组 {i + 1}:")
        for p in g:
            print(f"    {p['battleTag']}  Lo={p['accountIdLo']}")

    # setup
    sessions = setup_players(base, all_p, admin_tag)
    admin_session = sessions.get(admin_tag)
    if not admin_session:
        print(f"  ❌ 管理员 {admin_tag} 未登录")
        return

    # 创建赛事
    print(f"\n🏆 创建赛事「{name}」")
    rounds_data = [{
        "round": 1,
        "boN": bo_n,
        "groups": [
            {"groupIndex": i + 1, "players": g}
            for i, g in enumerate(groups)
        ],
    }]

    body = {"tournamentName": name, "rounds": rounds_data}
    if layout:
        body["layout"] = layout

    s, d = api("POST", f"{base}/api/tournament/create", session=admin_session, json=body)
    step("创建赛事", s, d)
    if s != 200:
        print("  ❌ 创建失败")
        return

    print(f"\n✅ 赛事「{name}」创建成功！{num_groups} 组 × 8 人, BO{bo_n}")
    print(f"   访问 {base} 查看对阵图")


def main():
    parser = argparse.ArgumentParser(description="创建赛事脚本")
    parser.add_argument("mode", choices=["bracket", "grid"], help="bracket=淘汰赛, grid=海选赛")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API 地址")
    parser.add_argument("--players", type=int, default=16, help="bracket 模式总人数（8 的倍数）")
    parser.add_argument("--groups", type=int, default=4, help="grid 模式分组数")
    parser.add_argument("--bo", type=int, default=1, help="BO N")
    parser.add_argument("--admin", default="衣锦夜行#1000", help="管理员 battleTag")
    parser.add_argument("--name", default=None, help="赛事名称")
    args = parser.parse_args()

    if args.mode == "bracket":
        if args.players % 8 != 0:
            print("❌ 人数必须是 8 的倍数")
            sys.exit(1)
        name = args.name or f"淘汰赛 {args.players}强"
        run(args.base, "bracket", args.players, args.bo, args.admin, 0, name)
    else:
        name = args.name or f"海选赛 {args.groups}组"
        run(args.base, "grid", 0, args.bo, args.admin, args.groups, name)


if __name__ == "__main__":
    main()
