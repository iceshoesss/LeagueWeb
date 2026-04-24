#!/usr/bin/env python3
"""
拟真淘汰赛测试 — 模拟 bg_tool 完整流程

每个玩家独立调用 check-league（各自带完整 LoList），服务端匹配后返回 gameUuid。
模拟真实场景：8 个玩家各自从 HearthMirror 拿到 LoList，独立上报。

用法：
  python scripts/test_realistic.py
  python scripts/test_realistic.py --base http://xxx:5000 --bo 3 --admin "衣锦夜行#1000"
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


def sim_check_league(base, sender, all_players, game_los, started_at):
    """
    模拟单个 bg_tool 在 STEP 13 时调用 check-league。
    sender: 发起请求的玩家
    all_players: 本局所有 7 个真人玩家
    game_los: 本局所有 Lo（含 bot 的非数字 Lo，模拟 HearthMirror 返回）
    """
    # 构建 players detail（只有 sender 自己有完整信息，其他人只有 Lo）
    players_detail = {}
    for p in all_players:
        players_detail[p["accountIdLo"]] = {
            "battleTag": p["battleTag"],
            "displayName": p["displayName"],
            "heroCardId": p["heroCardId"],
            "heroName": p["heroName"],
        }

    body = {
        "playerId": sender["battleTag"],
        "accountIdLo": sender["accountIdLo"],
        "accountIdLoList": game_los,  # 完整 LoList（含 bot 空位）
        "players": players_detail,
        "mode": "solo",
        "region": "CN",
        "startedAt": started_at,
    }
    return api("POST", f"{base}/api/plugin/check-league", json=body, headers=plugin_headers())


def sim_update_placement(base, sender, game_uuid, placement):
    """模拟单个 bg_tool 在游戏结束时调用 update-placement"""
    body = {
        "playerId": sender["battleTag"],
        "gameUuid": game_uuid,
        "accountIdLo": sender["accountIdLo"],
        "placement": placement,
    }
    return api("POST", f"{base}/api/plugin/update-placement", json=body, headers=plugin_headers())


def calc_points(placement):
    return 9 if placement == 1 else max(1, 9 - placement)


def play_game(base, group, game_num, bo_n):
    """模拟一局完整对局，返回 [(battleTag, placement, points), ...]"""
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 模拟 HearthMirror 返回的 LoList：7 个真人 + 1 个 bot（Lo=0）
    game_los = [p["accountIdLo"] for p in group] + ["0"]

    print(f"  📡 LoList（含 bot）: {game_los}")

    # ── Phase 1: 每个玩家独立调 check-league ──
    print(f"\n  Phase 1: check-league（{len(group)} 个玩家各自独立调用）")
    game_uuid = None
    check_results = []

    for i, p in enumerate(group):
        s, d = sim_check_league(base, p, group, game_los, started_at)
        check_results.append((p, s, d))

        returned_uuid = d.get("gameUuid")
        is_league = d.get("isLeague")

        if i == 0:
            check("第 1 个玩家匹配到淘汰赛", is_league is True)
            check("服务端生成 gameUuid", bool(returned_uuid))
            game_uuid = returned_uuid
        else:
            check(f"第 {i+1} 个玩家匹配到同一对局", returned_uuid == game_uuid,
                  f"期望 {game_uuid}, 实际 {returned_uuid}")

        step(f"{p['battleTag'][-4:]} check-league", s, d)
        time.sleep(0.3)

    if not game_uuid:
        print("  ❌ 未获取到 gameUuid，终止")
        return []

    print(f"\n  🎮 gameUuid: {game_uuid}")

    # ── Phase 2: 随机排名 ──
    placements = list(range(1, 8))
    random.shuffle(placements)
    print(f"\n  Phase 2: update-placement（随机排名 {placements}）")

    game_results = []  # [(battleTag, placement, points)]
    finalized = False
    for i, (p, _, _) in enumerate(check_results):
        placement = placements[i]
        s, d = sim_update_placement(base, p, game_uuid, placement)
        points = calc_points(placement)
        finalized = d.get("finalized", False)
        game_results.append((p["battleTag"], placement, points))

        label = f"  第{placement}名 {p['battleTag'][-4:]} (+{points}分)"
        extra = " 🎉 对局结束!" if finalized else ""
        step(label + extra, s, d)

        if i < len(check_results) - 1:
            check(f"第 {i+1}/7 人提交后未结束", not finalized)
        else:
            check("第 7 人提交后对局结束", finalized)

        if finalized:
            break
        time.sleep(0.5)

    # ── 本局结果 ──
    print(f"\n  📊 本局结果（第 {game_num}/{bo_n} 局）")
    print(f"  {'玩家':20s} {'排名':>4s} {'积分':>4s}")
    print(f"  {'─' * 32}")
    for bt, plc, pts in sorted(game_results, key=lambda x: x[1]):
        print(f"  {bt:20s} {plc:>4d} {pts:>+4d}")

    return game_results


def run(base, prefix, start_tag, bo_n, admin_tag):
    base = base.rstrip("/")

    print("=" * 60)
    print(f"  拟真淘汰赛测试 — BO{bo_n}")
    print(f"  API: {base}  管理员: {admin_tag}")
    print("=" * 60)

    # ── 生成 7 个测试玩家 ──
    group = make_players(prefix, start_tag, count=7)
    print(f"\n📦 生成 {len(group)} 个测试玩家")
    for p in group:
        print(f"     {p['battleTag']}  Lo={p['accountIdLo']}")

    # ── Step 1: upload-rating ──
    print("\n📦 Step 1: upload-rating")
    codes = {}
    all_for_upload = group.copy()
    admin_in_group = any(p["battleTag"] == admin_tag for p in group)
    if not admin_in_group:
        all_for_upload.append({
            "battleTag": admin_tag,
            "displayName": admin_tag.split("#")[0],
            "accountIdLo": str(10000000 + start_tag + 99),
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

    # ── Step 3: 创建赛事 ──
    print(f"\n🏆 Step 3: 创建赛事（7 人组，BO{bo_n}）")
    admin_session = sessions.get(admin_tag)
    if not admin_session:
        print(f"  ❌ 管理员 {admin_tag} 未登录")
        return

    rounds_data = [{
        "round": 1,
        "boN": bo_n,
        "groups": [
            {"groupIndex": 1, "players": group},
        ],
    }]
    s, d = api("POST", f"{base}/api/tournament/create", session=admin_session,
               json={"tournamentName": "拟真测试", "rounds": rounds_data})
    step("创建赛事", s, d)
    if s != 200:
        print("  ❌ 创建失败，终止")
        return

    # ── Step 4: 打 BO ──
    # 累计积分: {battleTag: total_points}
    cumulative = {p["battleTag"]: 0 for p in group}
    all_game_results = []

    for game in range(1, bo_n + 1):
        print(f"\n{'─' * 50}")
        print(f"⚔️ 第 {game}/{bo_n} 局")
        print(f"{'─' * 50}")
        results = play_game(base, group, game, bo_n)
        if not results:
            print("  ❌ 对局未正常结束，停止")
            break
        all_game_results.append(results)
        for bt, plc, pts in results:
            cumulative[bt] += pts
        time.sleep(1)

    # ── 最终排名 ──
    print(f"\n{'=' * 60}")
    print(f"  🏆 最终排名（{bo_n} 局累计）")
    print(f"{'=' * 60}")
    print(f"  {'排名':>4s} {'玩家':20s} {'总分':>4s} {'每局得分'}")
    print(f"  {'─' * 56}")
    sorted_players = sorted(cumulative.items(), key=lambda x: -x[1])
    for rank, (bt, total) in enumerate(sorted_players, 1):
        # 收集该玩家每局得分
        per_game = []
        for game_results in all_game_results:
            for g_bt, g_plc, g_pts in game_results:
                if g_bt == bt:
                    per_game.append(f"{g_plc}th({g_pts:+d})")
        games_str = " → ".join(per_game)
        print(f"  {rank:>4d} {bt:20s} {total:>4d} {games_str}")

    print(f"\n  测试完成: {passed} 通过 / {failed} 失败")
    if failed > 0:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="拟真淘汰赛测试")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API 地址")
    parser.add_argument("--prefix", default="测试选手", help="玩家名前缀")
    parser.add_argument("--players", type=int, default=8000, help="起始编号")
    parser.add_argument("--bo", type=int, default=3, help="BO N")
    parser.add_argument("--admin", default="衣锦夜行#1000", help="管理员 battleTag")
    args = parser.parse_args()
    run(args.base, args.prefix, args.players, args.bo, args.admin)


if __name__ == "__main__":
    main()
