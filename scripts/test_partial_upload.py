#!/usr/bin/env python3
"""
部分上传失败 + 管理员补录 → 正常进入 BO2 测试

模拟场景：BO2 淘汰赛，7 人组。
第 1 局：4 个玩家正常上传，3 个玩家上传失败，管理员手动补录。
验证：对局结束 → 进入 BO2 → 第 2 局正常进行。

用法：
  python3 scripts/test_partial_upload.py
  python3 scripts/test_partial_upload.py --base http://xxx:5000
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


def sim_check_league(base, sender, all_players, game_los, started_at):
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


def run(base, prefix, start_tag, bo_n, admin_tag):
    base = base.rstrip("/")

    print("=" * 60)
    print(f"  部分上传失败 + 补录测试 — BO{bo_n}")
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
                    json={"playerId": p["battleTag"], "accountIdLo": p["accountIdLo"],
                          "rating": 6000, "mode": "solo", "region": "CN"},
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

    # ── Step 3: 创建赛事 BO2 ──
    print(f"\n🏆 Step 3: 创建赛事（7 人组，BO{bo_n}）")
    admin_session = sessions.get(admin_tag)
    if not admin_session:
        print(f"  ❌ 管理员 {admin_tag} 未登录")
        return

    rounds_data = [{
        "round": 1,
        "boN": bo_n,
        "groups": [{"groupIndex": 1, "players": group}],
    }]
    s, d = api("POST", f"{base}/api/tournament/create", session=admin_session,
               json={"tournamentName": "补录测试", "rounds": rounds_data})
    step("创建赛事", s, d)
    if s != 200:
        print("  ❌ 创建失败，终止")
        return

    # ══════════════════════════════════════════════════════
    # 第 1 局：4 人上传 + 3 人失败 + 管理员补录
    # ══════════════════════════════════════════════════════
    print(f"\n{'─' * 50}")
    print(f"⚔️ 第 1/{bo_n} 局（模拟 3 人上传失败）")
    print(f"{'─' * 50}")

    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    game_los = [p["accountIdLo"] for p in group] + ["0"]

    # check-league（7 人全部调用）
    game_uuid = None
    for i, p in enumerate(group):
        s, d = sim_check_league(base, p, group, game_los, started_at)
        if i == 0:
            check("第 1 人匹配到淘汰赛", d.get("isLeague") is True)
            game_uuid = d.get("gameUuid")
        else:
            check(f"第 {i+1} 人匹配到同一对局", d.get("gameUuid") == game_uuid)
        step(f"{p['battleTag'][-4:]} check-league", s, d)
        time.sleep(0.2)

    if not game_uuid:
        print("  ❌ 未获取到 gameUuid，终止")
        return

    print(f"\n  🎮 gameUuid: {game_uuid}")

    # 前 4 人正常上传排名
    placements_all = list(range(1, 8))
    random.shuffle(placements_all)
    upload_players = group[:4]
    fail_players = group[4:]  # 这 3 人模拟"上传失败"

    print(f"\n  📤 前 4 人上传排名")
    for i, p in enumerate(upload_players):
        placement = placements_all[i]
        s, d = sim_update_placement(base, p, game_uuid, placement)
        step(f"第{placement}名 {p['battleTag'][-4:]}", s, d)
        time.sleep(0.3)

    # 检查：对局不应该结束
    match = requests.get(f"{base}/api/match/{game_uuid}").json()
    null_count = sum(1 for p in match.get("players", []) if p.get("placement") is None)
    check(f"4 人提交后还有 {null_count} 个空位", null_count == 3)

    # 管理员补录剩下 3 人
    print(f"\n  🔧 管理员补录剩余 3 人")
    admin_placements = {}
    for i, p in enumerate(fail_players):
        placement = placements_all[4 + i]
        admin_placements[p["accountIdLo"]] = placement
        print(f"    补录: {p['battleTag']} → 第{placement}名")

    s, d = api("POST", f"{base}/api/match/{game_uuid}/update-placement",
               session=admin_session, json={"placements": admin_placements})
    step("管理员补录", s, d)
    check("补录成功", d.get("ok") is True, json.dumps(d, ensure_ascii=False))

    # 验证：对局应该结束
    match = requests.get(f"{base}/api/match/{game_uuid}").json()
    ended = match.get("endedAt") is not None
    check("补录后对局已结束", ended)

    null_count = sum(1 for p in match.get("players", []) if p.get("placement") is None)
    check("所有玩家都有排名", null_count == 0, f"剩余 {null_count} 个空位")

    # 验证：BO 进度
    # 查询 tournament_groups
    bracket = requests.get(f"{base}/api/bracket").json()
    grid_group = None
    for t in bracket.get("tournaments", []):
        for rd in t.get("rounds", []):
            for g in rd.get("groups", []):
                if g.get("boN") == bo_n and g.get("gamesPlayed") == 1:
                    grid_group = g
    if grid_group:
        check(f"BO 进度: gamesPlayed=1/{bo_n}", grid_group["gamesPlayed"] == 1)
        check(f"状态: waiting（等第 2 局）", grid_group["status"] == "waiting")
    else:
        check("BO 进度更新", False, "未找到 gamesPlayed=1 的组")

    # ══════════════════════════════════════════════════════
    # 第 2 局：正常进行
    # ══════════════════════════════════════════════════════
    print(f"\n{'─' * 50}")
    print(f"⚔️ 第 2/{bo_n} 局（正常进行）")
    print(f"{'─' * 50}")

    started_at2 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    game_uuid2 = None

    for i, p in enumerate(group):
        s, d = sim_check_league(base, p, group, game_los, started_at2)
        if i == 0:
            check("BO2 第 2 局匹配成功", d.get("isLeague") is True)
            game_uuid2 = d.get("gameUuid")
        else:
            check(f"第 {i+1} 人匹配到第 2 局", d.get("gameUuid") == game_uuid2)
        step(f"{p['battleTag'][-4:]} check-league", s, d)
        time.sleep(0.2)

    if not game_uuid2:
        print("  ❌ 第 2 局未获取到 gameUuid，终止")
        return

    print(f"\n  🎮 gameUuid: {game_uuid2}")

    # 7 人全部正常上传
    placements2 = list(range(1, 8))
    random.shuffle(placements2)
    finalized = False
    for i, p in enumerate(group):
        placement = placements2[i]
        s, d = sim_update_placement(base, p, game_uuid2, placement)
        finalized = d.get("finalized", False)
        label = f"第{placement}名 {p['battleTag'][-4:]}"
        extra = " 🎉 对局结束!" if finalized else ""
        step(label + extra, s, d)
        if finalized:
            break
        time.sleep(0.3)

    check("第 2 局对局结束", finalized)

    # 验证 BO 完成
    bracket = requests.get(f"{base}/api/bracket").json()
    bo_done = False
    for t in bracket.get("tournaments", []):
        for rd in t.get("rounds", []):
            for g in rd.get("groups", []):
                if g.get("boN") == bo_n and g.get("gamesPlayed") == bo_n and g.get("status") == "done":
                    bo_done = True
    check(f"BO{bo_n} 全部完成, status=done", bo_done)

    # ── 总结 ──
    print(f"\n{'=' * 60}")
    print(f"  测试完成: {passed} 通过 / {failed} 失败")
    print(f"{'=' * 60}")
    if failed > 0:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="部分上传失败 + 补录测试")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API 地址")
    parser.add_argument("--prefix", default="补录选手", help="玩家名前缀")
    parser.add_argument("--players", type=int, default=9000, help="起始编号")
    parser.add_argument("--bo", type=int, default=2, help="BO N")
    parser.add_argument("--admin", default="衣锦夜行#1000", help="管理员 battleTag")
    args = parser.parse_args()
    run(args.base, args.prefix, args.players, args.bo, args.admin)


if __name__ == "__main__":
    main()
