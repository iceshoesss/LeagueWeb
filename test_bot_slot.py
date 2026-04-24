#!/usr/bin/env python3
"""
7 人淘汰赛 bot 空位测试 — 验证 bot 空位不放入 match + 自动推算正确

创建 7 人赛事（1 个 bot 空位），模拟 BO3 全流程，检查：
1. check-league 返回的 match 只有 7 个玩家（无 bot）
2. 6 人提交后自动推算第 7 人
3. 对局正常结束 + BO 进度正确

用法：
  python3 test_bot_slot.py                          # 默认 localhost:5000
  python3 test_bot_slot.py --base http://xxx:5000   # 指定 API
  python3 test_bot_slot.py --bo 5                   # BO5
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
    print(f"  {label:30s} {icon:6s} {detail}")


def make_players(prefix, start_tag, count=7):
    """生成 count 个玩家"""
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


def play_game(base, group, game_num, bo_n):
    """模拟一局：7 人 check-league + 随机排名 update-placement"""
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 构建 check-league 请求（所有 7 个真人）
    players_detail = {}
    for p in group:
        players_detail[p["accountIdLo"]] = {
            "battleTag": p["battleTag"],
            "displayName": p["displayName"],
            "heroCardId": p["heroCardId"],
            "heroName": p["heroName"],
        }
    lo_list = [p["accountIdLo"] for p in group]

    # 用第一个人发起 check-league
    body = {
        "playerId": group[0]["battleTag"],
        "accountIdLo": group[0]["accountIdLo"],
        "accountIdLoList": lo_list,
        "players": players_detail,
        "mode": "solo",
        "region": "CN",
        "startedAt": started_at,
    }
    s, d = api("POST", f"{base}/api/plugin/check-league", json=body, headers=plugin_headers())
    step("check-league", s, d)

    check("匹配到淘汰赛", d.get("isLeague") is True, f"isLeague={d.get('isLeague')}")

    # 用服务端返回的 gameUuid
    game_uuid = d.get("gameUuid")
    check("服务端返回 gameUuid", bool(game_uuid), f"gameUuid={game_uuid}")

    # 验证返回的 players 数量（应为 7，无 bot）
    resp_players = d.get("players", [])
    check("match 中有 7 个玩家", len(resp_players) == 7, f"实际 {len(resp_players)} 个")

    bot_in_match = [p for p in resp_players if not str(p.get("accountIdLo", "")).isdigit()]
    check("match 中无 bot 空位", len(bot_in_match) == 0,
          f"发现 bot: {[p.get('displayName') for p in bot_in_match]}")

    # 随机排名（1-7）
    placements = list(range(1, 8))
    random.shuffle(placements)

    print(f"\n  ⚔️  第 {game_num}/{bo_n} 局，随机排名: {placements}")

    finalized = False
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
        label = f"  第{placement}名 {p['battleTag'][-4:]}"
        extra = " 🎉 对局结束!" if finalized else ""
        step(label + extra, s, d)

        # 检查：前 6 人不应触发 finalized（第 7 人自动推算才 finalized）
        if i < len(group) - 1:
            check(f"第 {i+1} 人提交后未结束", not finalized,
                  f"第 {i+1}/7 人就 finalized 了")
        else:
            check("第 7 人提交后对局结束", finalized, f"finalized={finalized}")

        if finalized:
            break
        time.sleep(0.5)

    return finalized, game_uuid


def login_player(base, battle_tag, code):
    """登录一个玩家，返回 session"""
    s = requests.Session()
    api("POST", f"{base}/api/register", session=s,
        json={"battleTag": battle_tag, "verificationCode": code})
    status, data = api("POST", f"{base}/api/login", session=s,
                       json={"battleTag": battle_tag, "verificationCode": code})
    return s if status == 200 else None


def run(base, prefix, start_tag, bo_n, admin_tag):
    base = base.rstrip("/")

    print("=" * 60)
    print(f"  7 人淘汰赛 bot 空位测试 — BO{bo_n}")
    print(f"  API: {base}  管理员: {admin_tag}")
    print("=" * 60)

    # ── 生成 7 个测试玩家 ──
    group = make_players(prefix, start_tag, count=7)
    print(f"\n📦 生成 {len(group)} 个测试玩家")
    for p in group:
        print(f"     {p['battleTag']}  Lo={p['accountIdLo']}")

    # ── Step 1: upload-rating（测试玩家 + 管理员）──
    print("\n📦 Step 1: upload-rating（注册 player_records）")
    codes = {}
    all_for_upload = group.copy()
    # 管理员不在测试玩家中时，也注册一下
    admin_in_group = any(p["battleTag"] == admin_tag for p in group)
    if not admin_in_group:
        admin_player = {
            "battleTag": admin_tag,
            "displayName": admin_tag.split("#")[0],
            "accountIdLo": str(10000000 + start_tag + 99),
            "heroCardId": "TB_BaconShop_HERO_14",
            "heroName": "瓦托格尔女王",
        }
        all_for_upload.append(admin_player)

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
        code = codes.get(p["battleTag"], "123")
        sess = login_player(base, p["battleTag"], code)
        if sess:
            sessions[p["battleTag"]] = sess
        step(p["battleTag"], 200 if sess else 400, {})
        time.sleep(0.1)

    # ── Step 3: 创建赛事（7 人 + 1 空位）──
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
               json={"tournamentName": "7人Bot空位测试", "rounds": rounds_data})
    step("创建赛事", s, d)
    if s != 200:
        print("  ❌ 创建失败，终止")
        return

    # ── Step 4: 打 BO ──
    for game in range(1, bo_n + 1):
        print(f"\n{'─' * 40}")
        print(f"⚔️ 第 {game}/{bo_n} 局")
        print(f"{'─' * 40}")
        ok, game_uuid = play_game(base, group, game, bo_n)
        if not ok:
            print("  ❌ 对局未正常结束，停止后续")
            break
        time.sleep(1)

    # ── 结果 ──
    print(f"\n{'=' * 60}")
    print(f"  测试完成: {passed} 通过 / {failed} 失败")
    print(f"{'=' * 60}")
    if failed > 0:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="7 人淘汰赛 bot 空位测试")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API 地址")
    parser.add_argument("--prefix", default="测试选手", help="玩家名前缀")
    parser.add_argument("--players", type=int, default=9000, help="起始编号")
    parser.add_argument("--bo", type=int, default=3, help="BO N")
    parser.add_argument("--admin", default="衣锦夜行#1000", help="管理员 battleTag")
    args = parser.parse_args()
    run(args.base, args.prefix, args.players, args.bo, args.admin)


if __name__ == "__main__":
    main()
