#!/usr/bin/env python3
"""
海选赛（平铺网格）测试 — 模拟 bg_tool 完整流程

创建 grid 布局的海选赛事，2 组 × (8-bots) 人（含 bot 空位），
模拟 BO 全流程：每个玩家独立 check-league + update-placement。

用法：
  python scripts/test_grid.py                           # 2 组 × 7 人，各 1 个 bot
  python scripts/test_grid.py --bots 2                  # 2 组 × 6 人，各 2 个 bot
  python scripts/test_grid.py --bots 3 --bo 2           # 2 组 × 5 人，各 3 个 bot，BO2
  python scripts/test_grid.py --groups 3 --bots 1 --bo 2
"""

import argparse
import json
import random
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

passed = 0
failed = 0


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


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {label}")
    else:
        failed += 1
        print(f"  ❌ {label}  {detail}")


def step(label, status, data):
    icon = "✅" if 200 <= status < 300 else f"❌ {status}"
    detail = json.dumps(data, ensure_ascii=False)
    if len(detail) > 120:
        detail = detail[:117] + "..."
    print(f"    {label:30s} {icon:6s} {detail}")


def calc_points(placement):
    return 9 if placement == 1 else max(1, 9 - placement)


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


def sim_check_league(base, sender, group_players, game_los, started_at):
    players_detail = {}
    for p in group_players:
        players_detail[p["accountIdLo"]] = {
            "battleTag": p["battleTag"],
            "displayName": p["displayName"],
            "heroCardId": p["heroCardId"],
            "heroName": p["heroName"],
        }

    body = {
        "playerId": sender["battleTag"],
        "accountIdLo": sender["accountIdLo"],
        "accountIdLoList": game_los,
        "players": players_detail,
        "mode": "solo",
        "region": "CN",
        "startedAt": started_at,
    }
    return api("POST", f"{base}/api/plugin/check-league", json=body, headers=plugin_headers())


def sim_update_placement(base, sender, game_uuid, placement):
    body = {
        "playerId": sender["battleTag"],
        "gameUuid": game_uuid,
        "accountIdLo": sender["accountIdLo"],
        "placement": placement,
    }
    return api("POST", f"{base}/api/plugin/update-placement", json=body, headers=plugin_headers())


def play_game(base, group, group_name, game_num, bo_n, bot_count=1):
    """模拟一组打一局，返回 [(battleTag, placement, points), ...]"""
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    game_los = [p["accountIdLo"] for p in group] + ["0"] * bot_count  # bot Lo=0

    # ── check-league ──
    game_uuid = None
    for i, p in enumerate(group):
        s, d = sim_check_league(base, p, group, game_los, started_at)
        returned_uuid = d.get("gameUuid")
        if i == 0:
            check(f"[{group_name}] 第 1 人匹配到淘汰赛", d.get("isLeague") is True)
            game_uuid = returned_uuid
        else:
            check(f"[{group_name}] 第 {i+1} 人匹配到同一对局", returned_uuid == game_uuid)
        step(f"{p['battleTag'][-4:]} check-league", s, d)
        time.sleep(0.2)

    if not game_uuid:
        print(f"  ❌ [{group_name}] 未获取到 gameUuid")
        return []

    # ── update-placement ──
    # 8 人排名：前 N 个给真实玩家，剩余给 bot
    placements = list(range(1, len(group) + 1))  # 按真实人数生成排名，如 5 人用 1-5
    random.shuffle(placements)

    results = []
    finalized = False
    for i, p in enumerate(group):
        placement = placements[i]
        s, d = sim_update_placement(base, p, game_uuid, placement)
        points = calc_points(placement)
        finalized = d.get("finalized", False)
        results.append((p["battleTag"], placement, points))

        label = f"  第{placement}名 {p['battleTag'][-4:]} (+{points}分)"
        extra = " 🎉" if finalized else ""
        step(label + extra, s, d)
        if finalized:
            break
        time.sleep(0.3)

    # 本局结果
    print(f"\n  📊 [{group_name}] 第 {game_num}/{bo_n} 局结果")
    print(f"  {'玩家':20s} {'排名':>4s} {'积分':>4s}")
    print(f"  {'─' * 32}")
    for bt, plc, pts in sorted(results, key=lambda x: x[1]):
        print(f"  {bt:20s} {plc:>4d} {pts:>+4d}")

    return results


def run(base, prefix, start_tag, bo_n, admin_tag, num_groups, bot_count=1):
    base = base.rstrip("/")
    players_per_group = 8 - bot_count

    print("=" * 60)
    print(f"  海选赛（平铺网格）测试 — {num_groups} 组 × {players_per_group} 人, 每组 {bot_count} 个 bot 空位, BO{bo_n}")
    print(f"  API: {base}  管理员: {admin_tag}")
    print("=" * 60)

    # ── 生成玩家 ──
    all_groups = []
    all_players = []
    for gi in range(num_groups):
        group = make_players(f"G{gi+1}{prefix}", start_tag + gi * 100, count=players_per_group)
        all_groups.append(group)
        all_players.extend(group)

    print(f"\n📦 生成 {num_groups} 组 × {players_per_group} 人 = {len(all_players)} 个测试玩家（{bot_count} bot 空位/组）")
    for gi, group in enumerate(all_groups):
        print(f"  组 {gi+1}:")
        for p in group:
            print(f"    {p['battleTag']}  Lo={p['accountIdLo']}")

    # ── Step 1: upload-rating ──
    print("\n📦 Step 1: upload-rating")
    codes = {}
    all_for_upload = all_players.copy()
    admin_in = any(p["battleTag"] == admin_tag for p in all_players)
    if not admin_in:
        all_for_upload.append({
            "battleTag": admin_tag,
            "displayName": admin_tag.split("#")[0],
            "accountIdLo": str(10000000 + start_tag + 9999),
            "heroCardId": "TB_BaconShop_HERO_14",
            "heroName": "瓦托格尔女王",
        })

    for p in all_for_upload:
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
    for p in all_for_upload:
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

    # ── Step 3: 创建海选赛事（grid 布局）──
    print(f"\n🏆 Step 3: 创建海选赛事（{num_groups} 组 × 7 人, BO{bo_n}, layout=grid）")
    admin_session = sessions.get(admin_tag)
    if not admin_session:
        print(f"  ❌ 管理员 {admin_tag} 未登录")
        return

    rounds_data = [{
        "round": 1,
        "boN": bo_n,
        "groups": [
            {"groupIndex": gi + 1, "players": group}
            for gi, group in enumerate(all_groups)
        ],
    }]
    s, d = api("POST", f"{base}/api/tournament/create", session=admin_session,
               json={"tournamentName": "海选测试", "rounds": rounds_data, "layout": "grid"})
    step("创建赛事", s, d)
    if s != 200:
        print("  ❌ 创建失败，终止")
        return

    # ── Step 4: 各组打 BO ──
    cumulative = {p["battleTag"]: 0 for p in all_players}
    all_game_results = {gi: [] for gi in range(num_groups)}

    for game in range(1, bo_n + 1):
        print(f"\n{'─' * 50}")
        print(f"⚔️ 第 {game}/{bo_n} 局（所有组同时进行）")
        print(f"{'─' * 50}")

        for gi, group in enumerate(all_groups):
            group_name = f"组{gi+1}"
            results = play_game(base, group, group_name, game, bo_n, bot_count)
            if not results:
                print(f"  ❌ [{group_name}] 对局未正常结束")
                continue
            all_game_results[gi].append(results)
            for bt, plc, pts in results:
                cumulative[bt] += pts
        time.sleep(1)

    # ── 最终排名（按组）──
    for gi, group in enumerate(all_groups):
        print(f"\n{'=' * 50}")
        print(f"  🏆 组 {gi+1} 最终排名（{bo_n} 局累计）")
        print(f"{'=' * 50}")
        print(f"  {'排名':>4s} {'玩家':20s} {'总分':>4s} {'每局得分'}")
        print(f"  {'─' * 56}")
        group_bt = {p["battleTag"] for p in group}
        sorted_p = sorted(cumulative.items(), key=lambda x: -x[1])
        rank = 0
        for bt, total in sorted_p:
            if bt not in group_bt:
                continue
            rank += 1
            per_game = []
            for game_results in all_game_results[gi]:
                for g_bt, g_plc, g_pts in game_results:
                    if g_bt == bt:
                        per_game.append(f"{g_plc}th({g_pts:+d})")
            print(f"  {rank:>4d} {bt:20s} {total:>4d} {' → '.join(per_game)}")

    print(f"\n{'=' * 60}")
    print(f"  测试完成: {passed} 通过 / {failed} 失败")
    print(f"{'=' * 60}")
    if failed > 0:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="海选赛（平铺网格）测试")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API 地址")
    parser.add_argument("--prefix", default="选手", help="玩家名前缀")
    parser.add_argument("--players", type=int, default=7000, help="起始编号")
    parser.add_argument("--bo", type=int, default=1, help="BO N")
    parser.add_argument("--admin", default="衣锦夜行#1000", help="管理员 battleTag")
    parser.add_argument("--groups", type=int, default=2, help="分组数")
    parser.add_argument("--bots", type=int, default=1, choices=[1, 2, 3], help="每组 bot 空位数 (1/2/3)")
    args = parser.parse_args()
    run(args.base, args.prefix, args.players, args.bo, args.admin, args.groups, args.bots)


if __name__ == "__main__":
    main()
