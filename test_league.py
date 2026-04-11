#!/usr/bin/env python3
"""
联赛功能测试脚本 — 32人报名，自动组队，随机开赛

用法:
  python3 test_league.py           运行完整测试
  python3 test_league.py --cleanup 清理测试数据
"""

import os
import sys
import uuid
import hashlib
import time
import random
import threading
from datetime import datetime, timedelta
from pymongo import MongoClient

# ── 配置 ──────────────────────────────────────────
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")
NUM_PLAYERS = 32
PLAYERS_PER_GAME = 8
UPLOAD_INTERVAL = 0.5  # 排名提交间隔（秒），测试加速

# 生成 32 个模拟玩家
def make_players(n):
    heroes_pool = [
        ("TB_BaconShop_HERO_56", "阿莱克丝塔萨"), ("BG20_HERO_202", "阮大师"),
        ("TB_BaconShop_HERO_18", "穆克拉"), ("TB_BaconShop_HERO_55", "伊瑟拉"),
        ("BG20_HERO_101", "沃金"), ("TB_BaconShop_HERO_52", "苔丝·格雷迈恩"),
        ("TB_BaconShop_HERO_34", "奈法利安"), ("TB_BaconShop_HERO_28", "拉卡尼休"),
        ("BG31_HERO_802", "阿塔尼斯"), ("TB_BaconShop_HERO_01", "奥妮克希亚"),
        ("TB_BaconShop_HERO_11", "帕奇维克"), ("TB_BaconShop_HERO_40", "米尔豪斯"),
        ("TB_BaconShop_HERO_22", "巴罗夫领主"), ("BG20_HERO_283", "艾萨拉"),
        ("TB_BaconShop_HERO_39", "雷诺"), ("BG20_HERO_301", "希尔瓦娜斯"),
    ]
    players = []
    for i in range(n):
        tag_num = 1000 + i
        players.append({
            "battleTag": f"测试玩家{i+1:02d}#{tag_num}",
            "displayName": f"测试玩家{i+1:02d}",
            "accountIdLo": str(3000000000 + i),
            "rating": 5000 + random.randint(-500, 1500),
            "hero": heroes_pool[i % len(heroes_pool)],
        })
    return players

FAKE_PLAYERS = make_players(NUM_PLAYERS)

# 排名提交顺序: 第8名先传, 第1名最后传
PLACEMENT_UPLOAD_ORDER = list(range(PLAYERS_PER_GAME, 0, -1))


def calc_points(placement):
    return 9 if placement == 1 else max(1, 9 - placement)


def try_join_queue(db, player):
    """
    模拟一个玩家点击「参赛」按钮。
    返回 (joined, waiting_group_count)，joined=False 表示已在队列/等待组中。
    """
    name = player["displayName"]

    # 已在报名队列或等待组中
    if db.league_queue.find_one({"name": name}):
        return False, 0
    if db.league_waiting_queue.find_one({"players.name": name}):
        return False, 0

    # 优先补入未满的等待组
    incomplete = None
    for g in db.league_waiting_queue.find().sort("createdAt", 1):
        if len(g.get("players", [])) < PLAYERS_PER_GAME:
            incomplete = g
            break

    if incomplete:
        db.league_waiting_queue.update_one(
            {"_id": incomplete["_id"]},
            {"$push": {"players": {"name": name, "accountIdLo": player["accountIdLo"]}}}
        )
        count = len(incomplete["players"]) + 1
        return True, count

    # 加入报名队列
    db.league_queue.update_one(
        {"name": name},
        {"$setOnInsert": {
            "name": name,
            "accountIdLo": player["accountIdLo"],
            "joinedAt": datetime.utcnow().isoformat() + "Z",
        }},
        upsert=True,
    )

    # 检查是否满员
    count = db.league_queue.count_documents({})
    if count >= PLAYERS_PER_GAME:
        signup = list(db.league_queue.find().sort("joinedAt", 1).limit(PLAYERS_PER_GAME))
        players = [{"name": s["name"], "accountIdLo": s.get("accountIdLo", "")} for s in signup]
        names = [s["name"] for s in signup]
        db.league_waiting_queue.insert_one({
            "players": players,
            "createdAt": datetime.utcnow().isoformat() + "Z",
        })
        db.league_queue.delete_many({"name": {"$in": names}})
        return True, PLAYERS_PER_GAME

    return True, 0


def run_game(db, game_players, game_num):
    """
    模拟一整局联赛：STEP 13 匹配 → 8人竞争创建match → 按随机顺序提交排名。
    game_players: 8 个玩家的列表
    """
    game_uuid = str(uuid.uuid4())
    started_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    print(f"\n  🎮 第 {game_num} 局开始 (gameUuid: {game_uuid[:8]}...)")
    print(f"     玩家: {', '.join(p['displayName'] for p in game_players)}")

    # ── STEP 13: 匹配等待组（删除等待组）──
    account_ids = {p["accountIdLo"] for p in game_players}
    group = db.league_waiting_queue.find_one({
        "players.accountIdLo": {"$in": list(account_ids)}
    })
    if group:
        db.league_waiting_queue.delete_one({"_id": group["_id"]})
    print(f"     🗑️  等待组已删除（匹配成功）")

    # ── 8 个插件竞争创建 league_matches（随机延迟）──
    lock = threading.Lock()
    results = []

    def upload_match(idx):
        delay = random.uniform(0, 0.05)
        time.sleep(delay)
        p = game_players[idx]

        players_array = []
        for fp in game_players:
            h_id, h_name = fp["hero"]
            players_array.append({
                "accountIdLo": fp["accountIdLo"],
                "battleTag": fp["battleTag"],
                "displayName": fp["displayName"],
                "heroCardId": h_id,
                "heroName": h_name,
                "placement": None,
                "points": None,
            })

        r = db.league_matches.update_one(
            {"gameUuid": game_uuid},
            {"$setOnInsert": {
                "players": players_array,
                "region": "TEST",
                "mode": "solo",
                "startedAt": started_at,
                "endedAt": None,
            }},
            upsert=True,
        )
        with lock:
            results.append((p["displayName"], r.upserted_id is not None))

    threads = [threading.Thread(target=upload_match, args=(i,)) for i in range(PLAYERS_PER_GAME)]
    for t in threads: t.start()
    for t in threads: t.join()

    created = sum(1 for _, c in results if c)
    print(f"     ⚔️  竞争写入: {created} 创建, {8 - created} 跳过")

    # ── 随机排名 + 按 8→1 顺序提交 ──
    placements = list(range(1, PLAYERS_PER_GAME + 1))
    random.shuffle(placements)
    # player_idx → placement
    player_placements = {i: placements[i] for i in range(PLAYERS_PER_GAME)}
    # 按 placement 从大到小排序（8先提交）
    submit_order = sorted(range(PLAYERS_PER_GAME), key=lambda i: player_placements[i], reverse=True)

    for step, player_idx in enumerate(submit_order):
        p = game_players[player_idx]
        placement = player_placements[player_idx]
        points = calc_points(placement)

        # Update league_matches
        db.league_matches.update_one(
            {"gameUuid": game_uuid, "players.accountIdLo": p["accountIdLo"]},
            {"$set": {
                "players.$.placement": placement,
                "players.$.points": points,
            }}
        )

        # Update bg_ratings（精简版，无数组）
        old_rating = p["rating"]
        rating_change = random.randint(10, 50) if placement <= 4 else -random.randint(10, 50)
        new_rating = old_rating + rating_change
        db.bg_ratings.update_one(
            {"playerId": p["battleTag"]},
            {"$set": {
                "rating": new_rating,
                "lastRating": old_rating,
                "ratingChange": rating_change,
                "mode": "solo",
                "region": "TEST",
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            },
            "$inc": {"gameCount": 1}},
            upsert=True,
        )

        # 更新玩家 rating 供后续对局使用
        p["rating"] = new_rating

        # CheckAndFinalizeMatch
        doc = db.league_matches.find_one({"gameUuid": game_uuid})
        all_done = all(pl.get("placement") is not None for pl in doc["players"])
        if all_done:
            db.league_matches.update_one(
                {"gameUuid": game_uuid},
                {"$set": {"endedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")}}
            )

        if step < len(submit_order) - 1:
            time.sleep(UPLOAD_INTERVAL)

    # 打印本局结果
    match_doc = db.league_matches.find_one({"gameUuid": game_uuid})
    sorted_players = sorted(match_doc["players"], key=lambda x: x["placement"])
    rank_emoji = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
    for sp in sorted_players:
        print(f"     {rank_emoji[sp['placement']-1]} {sp['displayName']:>12}  第{sp['placement']}名  {sp['points']}分")

    return match_doc


def main():
    cleanup_only = "--cleanup" in sys.argv

    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]

    if cleanup_only:
        print("🧹 清理测试数据...")
        for p in FAKE_PLAYERS:
            db.league_players.delete_many({"battleTag": p["battleTag"]})
            db.bg_ratings.delete_many({"playerId": p["battleTag"]})
        db.league_queue.delete_many({})
        db.league_waiting_queue.delete_many({})
        db.league_matches.delete_many({"region": "TEST"})
        print("✅ 清理完成")
        return

    print("=" * 60)
    print(f"🧪 酒馆战棋联赛功能测试 — {NUM_PLAYERS}人报名")
    print("=" * 60)
    print(f"MongoDB: {MONGO_URL}/{DB_NAME}")
    print()

    # ── 清理旧测试数据 ──
    for p in FAKE_PLAYERS:
        db.league_players.delete_many({"battleTag": p["battleTag"]})
        db.bg_ratings.delete_many({"playerId": p["battleTag"]})
    db.league_queue.delete_many({})
    db.league_waiting_queue.delete_many({})
    db.league_matches.delete_many({"region": "TEST"})

    # ── 注册所有玩家为已验证选手 ──
    print(f"📋 注册 {NUM_PLAYERS} 个已验证选手")
    for p in FAKE_PLAYERS:
        db.league_players.update_one(
            {"battleTag": p["battleTag"]},
            {"$set": {
                "battleTag": p["battleTag"],
                "accountIdLo": p["accountIdLo"],
                "displayName": p["displayName"],
                "verified": True,
                "verifiedAt": datetime.utcnow().isoformat() + "Z",
            },
            "$setOnInsert": {
                "totalPoints": 0,
                "totalGames": 0,
                "wins": 0,
                "chickens": 0,
                "avgPlacement": 0,
                "createdAt": datetime.utcnow().isoformat() + "Z",
            }},
            upsert=True,
        )
        # bg_ratings 初始数据
        db.bg_ratings.update_one(
            {"playerId": p["battleTag"]},
            {"$set": {
                "playerId": p["battleTag"],
                "accountIdLo": p["accountIdLo"],
                "rating": p["rating"],
                "region": "TEST",
            },
            "$setOnInsert": {"gameCount": 0}},
            upsert=True,
        )
    print(f"  ✅ 全部注册完成")
    print()

    # ── 随机顺序报名 ──
    print(f"📋 {NUM_PLAYERS} 人随机顺序点击「参赛」按钮")
    print("-" * 50)

    order = list(range(NUM_PLAYERS))
    random.shuffle(order)

    games_launched = []
    current_game_players = []

    for idx in order:
        p = FAKE_PLAYERS[idx]
        joined, group_count = try_join_queue(db, p)

        if group_count == PLAYERS_PER_GAME:
            # 等待组满 8 人，获取该组玩家并开赛
            waiting_groups = list(db.league_waiting_queue.find().sort("createdAt", -1).limit(1))
            if waiting_groups:
                group = waiting_groups[0]
                group_names = [gp["name"] for gp in group.get("players", [])]
                game_players = [fp for fp in FAKE_PLAYERS if fp["displayName"] in group_names]
                # 补齐顺序
                game_players.sort(key=lambda x: group_names.index(x["displayName"]))
                games_launched.append(game_players)
                print(f"  🎉 {p['displayName']:>12} 排队 → 等待组满 {PLAYERS_PER_GAME} 人！开赛！")
                # 开赛在报名完成后统一处理
            else:
                print(f"  📝 {p['displayName']:>12} 排队 → 等待组满 {PLAYERS_PER_GAME} 人")
        elif joined:
            if group_count > 0:
                print(f"  📝 {p['displayName']:>12} 排队 → 补入等待组 ({group_count}/{PLAYERS_PER_GAME})")
            else:
                q_count = db.league_queue.count_documents({})
                print(f"  📝 {p['displayName']:>12} 排队 → 报名队列 ({q_count}人)")
        else:
            print(f"  ⚠️  {p['displayName']:>12} 已在队列中，跳过")

    # 检查是否还有剩余的人在等待组（不足8人的）
    remaining_waiting = list(db.league_waiting_queue.find())
    remaining_queue = db.league_queue.count_documents({})

    print(f"\n  📊 报名结束:")
    print(f"     开赛场次: {len(games_launched)}")
    print(f"     等待组中: {sum(len(g.get('players', [])) for g in remaining_waiting)} 人")
    print(f"     报名队列: {remaining_queue} 人")

    print()

    # ── 依次开赛 ──
    print(f"🎮 {len(games_launched)} 场比赛依次进行")
    print("=" * 50)

    match_docs = []
    for i, gp in enumerate(games_launched, 1):
        md = run_game(db, gp, i)
        match_docs.append(md)
        print()

    # ── 全部验证 ──
    print("=" * 60)
    print("📊 全局验证")
    print("=" * 60)

    all_correct = True
    rank_emoji = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]

    for gi, md in enumerate(match_docs, 1):
        print(f"\n  第 {gi} 局 (gameUuid: {md['gameUuid'][:8]}...):")

        # endedAt
        if md.get("endedAt"):
            print(f"    ✅ endedAt: {md['endedAt']}")
        else:
            print(f"    ❌ endedAt 为 null！")
            all_correct = False

        # 排名和积分
        total_points = 0
        for sp in sorted(md["players"], key=lambda x: x["placement"]):
            expected = calc_points(sp["placement"])
            ok = sp["points"] == expected
            if not ok:
                all_correct = False
            mark = "✅" if ok else "❌"
            total_points += sp["points"]
            print(f"    {mark} {rank_emoji[sp['placement']-1]} {sp['displayName']:>12}  "
                  f"第{sp['placement']}名  {sp['points']}分")

        expected_total = sum(calc_points(i) for i in range(1, 9))
        if total_points == expected_total:
            print(f"    ✅ 积分总和: {total_points}")
        else:
            print(f"    ❌ 积分总和: {total_points} (预期 {expected_total})")
            all_correct = False

    # bg_ratings 验证
    print(f"\n  bg_ratings 验证 (抽查):")
    sample = random.sample(FAKE_PLAYERS, min(8, len(FAKE_PLAYERS)))
    for p in sample:
        doc = db.bg_ratings.find_one({"playerId": p["battleTag"]})
        if doc and doc.get("rating") is not None:
            print(f"    ✅ {p['displayName']:>12}  分数={doc['rating']}  变化={doc.get('ratingChange', '?')}  "
                  f"局数={doc.get('gameCount', '?')}")
        else:
            print(f"    ❌ {p['displayName']:>12}  未找到记录")
            all_correct = False

    # 等待组/队列应为空
    wq = db.league_waiting_queue.count_documents({})
    q = db.league_queue.count_documents({})
    if wq == 0 and q == 0:
        print(f"\n  ✅ 等待组和报名队列已清空")
    else:
        print(f"\n  ⚠️  等待组: {wq}, 报名队列: {q}（可能有不足{PLAYERS_PER_GAME}人的剩余）")

    if all_correct:
        print("\n  🎉 所有测试通过！")
    else:
        print("\n  ⚠️  有测试未通过")

    print(f"\n  💡 清理: python3 {sys.argv[0]} --cleanup")
    for gi, md in enumerate(match_docs, 1):
        print(f"  🌐 第{gi}局: http://localhost:5000/match/{md['gameUuid']}")


if __name__ == "__main__":
    main()
