#!/usr/bin/env python3
"""
炉石战棋联赛系统 — 全流程测试脚本（淘汰赛版）

模拟 8 个玩家完成一整局联赛：
  upload-rating → register → login → check-league → update-placement

用法：
  python test_league.py                          # 默认参数
  python test_league.py --base https://xxx.com   # 指定 API 地址
  python test_league.py --prefix 测试玩家         # 自定义玩家名前缀
  python test_league.py --skip-placement 3       # 跳过第4个玩家的排名提交，测试自动推算
  python test_league.py --with-errors            # 正常流程 + 错误场景测试
  python test_league.py --test-errors            # 仅运行错误场景测试
  python test_league.py --demo                   # 演示模式，Step 4 每人间隔 3 秒（录视频用）
  python test_league.py --interval 5             # 自定义 Step 4 间隔秒数
"""

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass

import requests

# ─── 默认配置 ───
DEFAULT_BASE = "http://localhost:5000"
PLUGIN_KEY = "YOUR_PLUGIN_KEY_HERE"  # 替换为实际 API Key 
PLUGIN_VER = "0.1.0"    # 替换为对应的插件版本，确保符合服务器要求

# ─── 英雄数据（模拟用） ───
HEROES = [
    ("TB_BaconShop_HERO_14", "瓦托格尔女王"),
    ("TB_BaconShop_HERO_14", "瓦托格尔女王"),
    ("TB_BaconShop_HERO_14", "瓦托格尔女王"),
    ("TB_BaconShop_HERO_14", "瓦托格尔女王"),
    ("TB_BaconShop_HERO_14", "瓦托格尔女王"),
    ("TB_BaconShop_HERO_14", "瓦托格尔女王"),
    ("TB_BaconShop_HERO_14", "瓦托格尔女王"),
    ("TB_BaconShop_HERO_14", "瓦托格尔女王"),
]

# ─── 排名积分规则 ───
POINTS_TABLE = {1: 9, 2: 7, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1}


@dataclass
class Player:
    index: int
    battle_tag: str
    display_name: str
    account_id_lo: str
    hero_card_id: str
    hero_name: str


def plugin_headers():
    return {
        "Content-Type": "application/json",
        "X-HDT-Plugin": PLUGIN_VER,
        "Authorization": f"Bearer {PLUGIN_KEY}",
    }


def web_headers():
    return {"Content-Type": "application/json"}


def api(method, url, session=None, **kwargs):
    """发送请求，统一错误处理"""
    s = session or requests
    r = s.request(method, url, timeout=15, **kwargs)
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text[:200]}
    return r.status_code, data


def print_step(label, status, data):
    status_str = f"✅ {status}" if 200 <= status < 300 else f"❌ HTTP {status}"
    detail = json.dumps(data, ensure_ascii=False)
    if len(detail) > 100:
        detail = detail[:97] + "..."
    print(f"  {label:24s} {status_str:12s} {detail}")


def generate_players(prefix="衣锦夜行", start_tag=1000, start_lo=10000000):
    """生成 8 个模拟玩家"""
    players = []
    for i in range(8):
        card_id, hero_name = HEROES[i]
        players.append(Player(
            index=i,
            battle_tag=f"{prefix}#{start_tag + i}",
            display_name=prefix,
            account_id_lo=str(start_lo + i),
            hero_card_id=card_id,
            hero_name=hero_name,
        ))
    return players


# ─── 测试流程 ───

def step_upload_rating(base, players):
    """Step 1: 每个玩家上报分数，确保 player_record 存在"""
    print("\n📦 Step 1: upload-rating")
    codes = {}
    for p in players:
        s, d = api("POST", f"{base}/api/plugin/upload-rating",
                    json={
                        "playerId": p.battle_tag,
                        "accountIdLo": p.account_id_lo,
                        "rating": 6000,
                        "mode": "solo",
                        "region": "CN",
                    },
                    headers=plugin_headers())
        print_step(p.battle_tag, s, d)
        if s == 200:
            codes[p.battle_tag] = d.get("verificationCode", "123")
        time.sleep(0.2)
    return codes


def step_login(base, players, codes):
    """Step 2: 每个玩家登录，返回 session。先 register 再 login，确保 cookie 到手"""
    print("\n🔑 Step 2: register + login")
    sessions = {}
    for p in players:
        s = requests.Session()
        code = codes.get(p.battle_tag, "123")

        # register（可能已注册，忽略错误）
        api("POST", f"{base}/api/register", session=s,
            json={"battleTag": p.battle_tag, "verificationCode": code},
            headers=web_headers())

        # login（确保拿到 session cookie）
        status, data = api("POST", f"{base}/api/login", session=s,
                           json={"battleTag": p.battle_tag, "verificationCode": code},
                           headers=web_headers())
        print_step(p.battle_tag, status, data)

        if status == 200:
            sessions[p.battle_tag] = s
        time.sleep(0.3)
    return sessions


def step_check_league(base, players):
    """Step 3: check-league 创建联赛对局 + 竞争测试"""
    print("\n🏁 Step 3: check-league")
    p0 = players[0]
    game_uuid = str(uuid.uuid4())
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    players_detail = {}
    for p in players:
        players_detail[p.account_id_lo] = {
            "battleTag": p.battle_tag,
            "displayName": p.display_name,
            "heroCardId": p.hero_card_id,
            "heroName": p.hero_name,
        }
    account_id_list = [p.account_id_lo for p in players]

    # 第一个玩家发起
    body = {
        "playerId": p0.battle_tag,
        "gameUuid": game_uuid,
        "accountIdLo": p0.account_id_lo,
        "accountIdLoList": account_id_list,
        "players": players_detail,
        "mode": "solo",
        "region": "CN",
        "startedAt": started_at,
    }
    status, data = api("POST", f"{base}/api/plugin/check-league",
                       json=body, headers=plugin_headers())
    print_step(p0.battle_tag, status, data)

    # 竞争测试：其他玩家也调用
    print("\n  🔍 竞争测试（其他玩家 check-league）")
    for p in players[1:]:
        body_copy = {**body, "playerId": p.battle_tag, "accountIdLo": p.account_id_lo}
        s, d = api("POST", f"{base}/api/plugin/check-league",
                    json=body_copy, headers=plugin_headers())
        print_step(f"  {p.battle_tag}", s, d)
        time.sleep(0.1)

    return game_uuid


def step_duplicate_test(base, game_uuid, player):
    """测试重复提交返回 409"""
    status, data = api("POST", f"{base}/api/plugin/update-placement",
                        json={
                            "playerId": player.battle_tag,
                            "gameUuid": game_uuid,
                            "accountIdLo": player.account_id_lo,
                            "placement": 1,
                        },
                        headers=plugin_headers())
    print_step(player.battle_tag, status, data)
    if status == 409:
        print("  ✅ 重复提交正确拒绝 (HTTP 409)")


def step_submit_placements(base, players, game_uuid, placements=None, skip_player=None, interval=0.3):
    """
    Step 4: 提交排名
    placements: dict {accountIdLo: placement}，None 则自动分配 1-8
    skip_player: accountIdLo，跳过该玩家（测试自动推算）
    interval: 每个玩家提交之间的间隔秒数
    """
    print("\n📊 Step 4: update-placement")

    if placements is None:
        placements = {p.account_id_lo: i + 1 for i, p in enumerate(players)}

    for p in players:
        if skip_player and p.account_id_lo == skip_player:
            print(f"  {p.battle_tag:24s} ⏭ 跳过（测试自动推算）")
            continue
        placement = placements.get(p.account_id_lo)
        if placement is None:
            continue

        status, data = api("POST", f"{base}/api/plugin/update-placement",
                           json={
                               "playerId": p.battle_tag,
                               "gameUuid": game_uuid,
                               "accountIdLo": p.account_id_lo,
                               "placement": placement,
                           },
                           headers=plugin_headers())
        finalized = data.get("finalized", False)
        label = f"{p.battle_tag} → 第{placement}名"
        if finalized:
            data = {**data, "_note": "🎉 对局结束!"}
        print_step(label, status, data)

        if finalized:
            print("  ✅ 已 finalize，停止提交")
            break
        time.sleep(interval)


def step_verify(base, game_uuid):
    """Step 5: 验证最终结果"""
    print("\n✅ Step 5: 验证结果")

    status, data = api("GET", f"{base}/api/match/{game_uuid}")
    if status != 200:
        print(f"  ❌ 获取对局失败: {data}")
        return False

    print(f"  gameUuid: {data.get('gameUuid')}")
    print(f"  endedAt:  {data.get('endedAt')}")
    print(f"  status:   {data.get('status', '正常')}")

    players_sorted = sorted(data.get("players", []), key=lambda x: x.get("placement") or 99)
    print(f"\n  {'排名':^4} {'玩家':^22} {'英雄':^10} {'积分':^4}")
    print(f"  {'─'*4} {'─'*22} {'─'*10} {'─'*4}")
    for p in players_sorted:
        pl = p.get("placement", "?")
        pt = p.get("points", "?")
        print(f"  {pl:^4} {p['battleTag']:^22} {p.get('heroName','?'):^10} {pt:^4}")

    # 验证积分
    ok = True
    for p in players_sorted:
        pl, pt = p.get("placement"), p.get("points")
        expected = POINTS_TABLE.get(pl)
        if pt != expected:
            print(f"\n  ⚠️ 积分异常: {p['battleTag']} 第{pl}名 积分{pt}，期望{expected}")
            ok = False

    if ok:
        print(f"\n  🎉 全部积分验证通过!")

    # 排行榜
    print("\n  📈 排行榜:")
    status, board = api("GET", f"{base}/api/players")
    if status == 200:
        for rank, p in enumerate(sorted(board, key=lambda x: -x.get("totalPoints", 0)), 1):
            print(f"    {rank}. {p['battleTag']:22s} 积分:{p.get('totalPoints',0):>4}  "
                  f"场次:{p.get('leagueGames',0)}  吃鸡:{p.get('chickens',0)}")
    return ok


def step_test_errors(base):
    """Step 6: 测试错误场景（认证失败、参数错误等）"""
    print("\n🛡️ Step 6: 错误场景测试")
    results = []

    def check(label, expect_status, actual_status, data):
        ok = actual_status == expect_status
        icon = "✅" if ok else "⚠️"
        results.append(ok)
        exp = f"期望{expect_status}"
        detail = json.dumps(data, ensure_ascii=False)
        if len(detail) > 80:
            detail = detail[:77] + "..."
        print(f"  {icon} {label:36s} {exp:8s} 实际{actual_status:3d}  {detail}")

    # ── 认证错误 ──
    print("\n  ── 认证错误 ──")

    # 错误 API Key
    s, d = api("POST", f"{base}/api/plugin/upload-rating",
               json={"playerId": "test#0001", "rating": 5000, "mode": "solo", "region": "CN"},
               headers={"Content-Type": "application/json",
                         "X-HDT-Plugin": PLUGIN_VER,
                         "Authorization": "Bearer wrong-key-12345"})
    check("错误 API Key → 403", 403, s, d)

    # 缺少 Bearer token
    s, d = api("POST", f"{base}/api/plugin/upload-rating",
               json={"playerId": "test#0001", "rating": 5000, "mode": "solo", "region": "CN"},
               headers={"Content-Type": "application/json",
                         "X-HDT-Plugin": PLUGIN_VER})
    check("缺少 Bearer token → 403", 403, s, d)

    # 错误插件版本号（低于最低版本）
    s, d = api("POST", f"{base}/api/plugin/upload-rating",
               json={"playerId": "test#0001", "rating": 5000, "mode": "solo", "region": "CN"},
               headers={"Content-Type": "application/json",
                         "X-HDT-Plugin": "0.0.1",
                         "Authorization": f"Bearer {PLUGIN_KEY}"})
    check("插件版本 0.0.1 → 403", 403, s, d)

    # 缺少 X-HDT-Plugin header
    s, d = api("POST", f"{base}/api/plugin/upload-rating",
               json={"playerId": "test#0001", "rating": 5000, "mode": "solo", "region": "CN"},
               headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {PLUGIN_KEY}"})
    check("缺少 X-HDT-Plugin → 403", 403, s, d)

    # ── 参数错误 ──
    print("\n  ── 参数错误 ──")

    # gameUuid 格式无效
    s, d = api("POST", f"{base}/api/plugin/check-league",
               json={"gameUuid": "not-a-uuid", "accountIdLoList": ["123"]},
               headers=plugin_headers())
    check("gameUuid 格式无效 → 400", 400, s, d)

    # check-league 缺少 accountIdLoList
    s, d = api("POST", f"{base}/api/plugin/check-league",
               json={"gameUuid": str(uuid.uuid4())},
               headers=plugin_headers())
    check("check-league 缺少 accountIdLoList → 400", 400, s, d)

    # update-placement 缺少必填字段
    s, d = api("POST", f"{base}/api/plugin/update-placement",
               json={"gameUuid": str(uuid.uuid4())},
               headers=plugin_headers())
    check("update-placement 缺少 accountIdLo → 400", 400, s, d)

    # update-placement 对局不存在
    fake_uuid = str(uuid.uuid4())
    s, d = api("POST", f"{base}/api/plugin/update-placement",
               json={"gameUuid": fake_uuid, "accountIdLo": "9999", "placement": 1},
               headers=plugin_headers())
    check("update-placement 对局不存在 → 404", 404, s, d)

    # upload-rating 缺少 playerId
    s, d = api("POST", f"{base}/api/plugin/upload-rating",
               json={"rating": 5000},
               headers=plugin_headers())
    check("upload-rating 缺少 playerId → 400", 400, s, d)

    # register 缺少 battleTag
    s, d = api("POST", f"{base}/api/register",
               json={"verificationCode": "123"},
               headers=web_headers())
    check("register 缺少 battleTag → 400", 400, s, d)

    # register 验证码错误
    s, d = api("POST", f"{base}/api/register",
               json={"battleTag": "不存在的玩家#0000", "verificationCode": "WRONG"},
               headers=web_headers())
    check("register 无记录/验证码错误 → 404/400", 404 if s == 404 else 400, s, d)

    # ── 汇总 ──
    passed = sum(results)
    total = len(results)
    print(f"\n  📊 错误场景测试: {passed}/{total} 通过")
    return all(results)


# ─── 主流程 ───

def main():
    parser = argparse.ArgumentParser(description="炉石战棋联赛全流程测试")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API 地址")
    parser.add_argument("--prefix", default="衣锦夜行", help="玩家名前缀")
    parser.add_argument("--start-tag", type=int, default=1000, help="起始 #tag 编号")
    parser.add_argument("--start-lo", type=int, default=10000000, help="起始 accountIdLo")
    parser.add_argument("--skip-placement", type=int, default=None,
                        help="跳过第 N 个玩家(0-7)的排名提交，测试自动推算")
    parser.add_argument("--test-errors", action="store_true",
                        help="仅运行错误场景测试（不需要排队数据）")
    parser.add_argument("--with-errors", action="store_true",
                        help="正常流程跑完后加测错误场景")
    parser.add_argument("--demo", action="store_true",
                        help="演示模式，Step 4 每人间隔 3 秒，适合录制视频")
    parser.add_argument("--interval", type=float, default=None,
                        help="Step 4 每人间隔秒数（覆盖默认值）")
    args = parser.parse_args()

    base = args.base.rstrip("/")

    # 纯错误测试模式
    if args.test_errors:
        print("=" * 60)
        print(f"  炉石战棋联赛 — 错误场景测试")
        print(f"  API: {base}")
        print("=" * 60)
        step_test_errors(base)
        print("\n" + "=" * 60)
        print("  测试完成!")
        print("=" * 60)
        return

    players = generate_players(args.prefix, args.start_tag, args.start_lo)

    print("=" * 60)
    print(f"  炉石战棋联赛 — 全流程测试")
    print(f"  API: {base}")
    print(f"  玩家: {args.prefix}#{args.start_tag} ~ #{args.start_tag + 7}")
    if args.skip_placement is not None:
        print(f"  跳过: {players[args.skip_placement].battle_tag}（测试自动推算）")
    if args.demo:
        print(f"  🎬 演示模式（Step 4 间隔 3 秒）")
    print("=" * 60)

    codes = step_upload_rating(base, players)
    sessions = step_login(base, players, codes)

    game_uuid = step_check_league(base, players)

    # 选第一个有 session 的玩家做重复提交测试
    dup_player = None
    for p in players:
        skip_lo = players[args.skip_placement].account_id_lo if args.skip_placement is not None else None
        if p.account_id_lo != skip_lo:
            dup_player = p
            break
    if dup_player:
        step_duplicate_test(base, game_uuid, dup_player)

    skip_lo = players[args.skip_placement].account_id_lo if args.skip_placement is not None else None
    interval = args.interval if args.interval is not None else (3.0 if args.demo else 0.3)
    step_submit_placements(base, players, game_uuid, skip_player=skip_lo, interval=interval)

    step_verify(base, game_uuid)

    if args.with_errors:
        step_test_errors(base)

    print("\n" + "=" * 60)
    print("  测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
