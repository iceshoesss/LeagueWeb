#!/usr/bin/env python3
"""
联赛功能测试脚本
模拟从注册 → 排队 → 联赛对局 → 提交排名的完整流程

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
from datetime import datetime, timedelta
from pymongo import MongoClient

# ── 配置 ──────────────────────────────────────────
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")

# 模拟的 8 个玩家（已注册状态）
FAKE_PLAYERS = [
    {"battleTag": "南怀北瑾丨少头脑#5267",   "displayName": "南怀北瑾丨少头脑",   "accountIdLo": "1708070391"},
    {"battleTag": "疾风剑豪#1234",           "displayName": "疾风剑豪",           "accountIdLo": "2000000001"},
    {"battleTag": "暗夜精灵#5678",           "displayName": "暗夜精灵",           "accountIdLo": "2000000002"},
    {"battleTag": "星辰大海#9012",           "displayName": "星辰大海",           "accountIdLo": "2000000003"},
    {"battleTag": "月光骑士#3456",           "displayName": "月光骑士",           "accountIdLo": "2000000004"},
    {"battleTag": "虚空行者#7890",           "displayName": "虚空行者",           "accountIdLo": "2000000005"},
    {"battleTag": "冰霜法师#2345",           "displayName": "冰霜法师",           "accountIdLo": "2000000006"},
    {"battleTag": "烈焰术士#6789",           "displayName": "烈焰术士",           "accountIdLo": "2000000007"},
]

# 英雄数据（每个玩家选的英雄）
HEROES = [
    ("TB_BaconShop_HERO_56", "阿莱克丝塔萨"),
    ("BG20_HERO_202",        "阮大师"),
    ("TB_BaconShop_HERO_18", "穆克拉"),
    ("TB_BaconShop_HERO_55", "伊瑟拉"),
    ("BG20_HERO_101",        "沃金"),
    ("TB_BaconShop_HERO_52", "阿莱克丝塔萨"),
    ("TB_BaconShop_HERO_34", "奈法利安"),
    ("TB_BaconShop_HERO_28", "拉卡尼休"),
]

# 排名提交顺序: 第8名先传, 第1名最后传
PLACEMENT_UPLOAD_ORDER = [8, 7, 6, 5, 4, 3, 2, 1]
UPLOAD_INTERVAL = 5  # 秒

def calc_points(placement):
    return 9 if placement == 1 else max(1, 9 - placement)


def main():
    cleanup_only = "--cleanup" in sys.argv

    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]

    if cleanup_only:
        print("🧹 清理测试数据...")
        for tag in [p["battleTag"] for p in FAKE_PLAYERS]:
            db.league_players.delete_many({"battleTag": tag})
            db.bg_ratings.delete_many({"playerId": tag})
        db.league_queue.delete_many({})
        db.league_waiting_queue.delete_many({})
        db.league_matches.delete_many({"region": "TEST"})
        print("✅ 清理完成")
        return

    print("=" * 60)
    print("🧪 酒馆战棋联赛功能测试")
    print("=" * 60)
    print(f"MongoDB: {MONGO_URL}/{DB_NAME}")
    print()

    # ── 步骤 1: 所有用户注册为已验证选手 ──
    print("📋 步骤 1: 注册 8 个已验证选手")
    print("-" * 40)

    for i, p in enumerate(FAKE_PLAYERS):
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
        print(f"  ✅ {p['displayName']} 已注册")

    # 同时写入 bg_ratings（插件已上传过分数，有验证码）
    for i, p in enumerate(FAKE_PLAYERS):
        rating = 6000 + i * 200
        db.bg_ratings.update_one(
            {"playerId": p["battleTag"]},
            {"$set": {
                "playerId": p["battleTag"],
                "accountIdLo": p["accountIdLo"],
                "rating": rating,
                "region": "TEST",
            },
            "$setOnInsert": {"gameCount": 10}},
            upsert=True,
        )

    print()

    # ── 步骤 2: 模拟 8 人点击排队按钮 ──
    print("📋 步骤 2: 8 人依次点击排队按钮")
    print("-" * 40)

    db.league_queue.delete_many({})
    db.league_waiting_queue.delete_many({})

    for i, p in enumerate(FAKE_PLAYERS):
        name = p["displayName"]

        # 查是否有未满的等待组
        incomplete = None
        for g in db.league_waiting_queue.find().sort("createdAt", 1):
            if len(g.get("players", [])) < 8:
                incomplete = g
                break

        if incomplete:
            db.league_waiting_queue.update_one(
                {"_id": incomplete["_id"]},
                {"$push": {"players": {"name": name, "accountIdLo": p["accountIdLo"]}}}
            )
            current_count = len(incomplete["players"]) + 1
            print(f"  📝 {name} 排队 → 补入等待组 ({current_count}/8)")
            if current_count == 8:
                print(f"\n  🎉 等待组已满 8 人！自动进入正在进行")
        else:
            db.league_queue.update_one(
                {"name": name},
                {"$setOnInsert": {
                    "name": name,
                    "accountIdLo": p["accountIdLo"],
                    "joinedAt": datetime.utcnow().isoformat() + "Z",
                }},
                upsert=True,
            )
            count = db.league_queue.count_documents({})
            print(f"  📝 {name} 排队 ({count}/8)")

            if count >= 8:
                signup = list(db.league_queue.find().sort("joinedAt", 1).limit(8))
                players = [{"name": s["name"], "accountIdLo": s.get("accountIdLo", "")} for s in signup]
                names = [s["name"] for s in signup]
                db.league_waiting_queue.insert_one({
                    "players": players,
                    "createdAt": datetime.utcnow().isoformat() + "Z",
                })
                db.league_queue.delete_many({"name": {"$in": names}})
                print(f"\n  🎉 8人满员，自动移入等待组 → 进行中")

    waiting = list(db.league_waiting_queue.find())
    queue_count = db.league_queue.count_documents({})
    print(f"\n  等待组: {len(waiting)} 组, 报名队列剩余: {queue_count} 人")

    print()
    print("  ⏳ 等待组已就绪，8人进入游戏大厅...")
    print()

    # ── 步骤 3: 游戏开始 ──
    print("  🎮 游戏开始...")
    print("  ⏳ STEP 13 (MAIN_CLEANUP) 检测到，开始联赛匹配...")
    print()

    # ── 步骤 4: 8 个插件上传 match（随机微小延迟模拟竞争）──
    print("📋 步骤 4: 8 个插件上传 league_matches（随机延迟竞争）")
    print("-" * 40)

    import threading

    game_uuid = str(uuid.uuid4())
    started_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    # 先删除等待组（模拟匹配成功，真实流程是 CheckLeagueQueue 里做的）
    group = waiting[0]
    group_account_ids = {p.get("accountIdLo", "") for p in group["players"] if p.get("accountIdLo")}
    print(f"  等待组 accountIdLo: {sorted(group_account_ids)}")
    db.league_waiting_queue.delete_one({"_id": group["_id"]})
    print("  🗑️  等待组已删除（匹配成功）")
    print()

    upload_results = []
    upload_times = []
    lock = threading.Lock()

    def plugin_upload_match(player_idx):
        """
        模拟单个插件的 CreateLeagueMatchDirect:
        - 延迟随机 0~50ms 模拟网络微小差异
        - 从 LobbyInfo 获取全部 8 人信息
        - upsert + $setOnInsert（竞争）
        """
        delay = random.uniform(0, 0.05)
        time.sleep(delay)

        p = FAKE_PLAYERS[player_idx]

        # 每个插件都从 LobbyInfo 看到全部 8 个玩家
        players_array = []
        for j, fp in enumerate(FAKE_PLAYERS):
            h_id, h_name = HEROES[j]
            players_array.append({
                "accountIdLo": fp["accountIdLo"],
                "battleTag": fp["battleTag"],
                "displayName": fp["displayName"],
                "heroCardId": h_id,
                "heroName": h_name,
                "placement": None,
                "points": None,
            })

        t_before = time.time()
        result = db.league_matches.update_one(
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
        t_after = time.time()

        created = result.upserted_id is not None
        with lock:
            upload_results.append((p["displayName"], created, delay))
            upload_times.append((p["displayName"], t_before, t_after))

    threads = []
    for i in range(8):
        t = threading.Thread(target=plugin_upload_match, args=(i,))
        threads.append(t)

    t_start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 按实际到达时间排序显示
    upload_results.sort(key=lambda x: x[2])
    for name, created, delay in upload_results:
        status = "创建文档 ✅" if created else "文档已存在，跳过 ⏭️"
        print(f"  {'🔌':>2} {name} → {status}  (延迟 {delay*1000:.0f}ms)")

    match_doc = db.league_matches.find_one({"gameUuid": game_uuid})
    if match_doc:
        has_created = sum(1 for _, c, _ in upload_results if c)
        print(f"\n  ✅ match 文档存在，玩家数: {len(match_doc['players'])}")
        print(f"  ✅ 竞争写入: {has_created} 个创建，{8 - has_created} 个跳过")
    else:
        print(f"\n  ❌ match 文档不存在！")
        return

    print()
    print("  ⏳ 游戏进行中...")
    print()

    # ── 步骤 5: 按 8→7→6→5→4→3→2→1 顺序，每 5 秒一人提交排名 ──
    print("📋 步骤 5: 8 人按 8→1 顺序提交排名（每 5 秒一人）")
    print("-" * 40)

    for i, placement in enumerate(PLACEMENT_UPLOAD_ORDER):
        player_idx = placement - 1
        p = FAKE_PLAYERS[player_idx]
        points = calc_points(placement)
        rating_before = 6000 + player_idx * 200
        # 模拟分数变化: 前四加分，后四扣分
        rating_change = random.randint(10, 50) if placement <= 4 else -random.randint(10, 50)
        rating_after = rating_before + rating_change

        # ── 模拟插件 UpdateLeaguePlacement ──
        db.league_matches.update_one(
            {"gameUuid": game_uuid, "players.accountIdLo": p["accountIdLo"]},
            {"$set": {
                "players.$.placement": placement,
                "players.$.points": points,
            }}
        )

        # ── 模拟插件 IncrementLeagueCount + TryUploadRating（写 bg_ratings）──
        db.bg_ratings.update_one(
            {"playerId": p["battleTag"]},
            {"$set": {
                "rating": rating_after,
                "lastRating": rating_before,
                "ratingChange": rating_change,
                "mode": "solo",
                "region": "TEST",
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            },
            "$inc": {"gameCount": 1},
            "$push": {
                "ratingChanges": rating_change,
                "placements": placement,
            }},
            upsert=True,
        )

        # ── 模拟 CheckAndFinalizeMatch ──
        doc = db.league_matches.find_one({"gameUuid": game_uuid})
        all_done = all(pl.get("placement") is not None for pl in doc["players"])
        finalized = False
        if all_done:
            db.league_matches.update_one(
                {"gameUuid": game_uuid},
                {"$set": {"endedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")}}
            )
            finalized = True

        rank_label = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"][placement - 1]
        done_count = sum(1 for pl in doc["players"] if pl.get("placement") is not None)
        sign = "+" if rating_change > 0 else ""
        print(f"  {rank_label} 第{placement}名 {p['displayName']:>20} → {points}分  "
              f"分数 {rating_before} → {rating_after} ({sign}{rating_change})  "
              f"({done_count}/8)" + ("  🎉 对局结束！" if finalized else ""))

        if i < len(PLACEMENT_UPLOAD_ORDER) - 1:
            time.sleep(UPLOAD_INTERVAL)

    print()

    # ── 验证最终结果 ──
    print("=" * 60)
    print("📊 最终验证")
    print("=" * 60)

    match_doc = db.league_matches.find_one({"gameUuid": game_uuid})

    # 验证排名和积分
    print("\n  排名验证:")
    all_correct = True
    for p in sorted(match_doc["players"], key=lambda x: x["placement"]):
        expected_points = calc_points(p["placement"])
        ok = p["points"] == expected_points
        if not ok:
            all_correct = False
        mark = "✅" if ok else "❌"
        print(f"    {mark} {p['displayName']:>20}  第{p['placement']}名  {p['points']}分 (预期{expected_points})")

    # 验证 endedAt
    if match_doc.get("endedAt"):
        print(f"\n  ✅ endedAt 已写入: {match_doc['endedAt']}")
    else:
        print(f"\n  ❌ endedAt 为 null！")
        all_correct = False

    # 验证积分总和
    total = sum(calc_points(i) for i in range(1, 9))
    actual_total = sum(p["points"] for p in match_doc["players"] if p["points"])
    if actual_total == total:
        print(f"  ✅ 积分总和: {actual_total}")
    else:
        print(f"  ❌ 积分总和: {actual_total} (预期 {total})")
        all_correct = False

    # 验证 bg_ratings 更新
    print(f"\n  bg_ratings 验证:")
    for p in FAKE_PLAYERS:
        rating_doc = db.bg_ratings.find_one({"playerId": p["battleTag"]})
        if rating_doc and rating_doc.get("ratingChange") is not None:
            placements = rating_doc.get("placements", [])
            print(f"    ✅ {p['displayName']:>20}  分数={rating_doc['rating']}  "
                  f"变化={rating_doc['ratingChange']}  排名记录={placements}")
        else:
            print(f"    ❌ {p['displayName']:>20}  未找到更新")
            all_correct = False

    if all_correct:
        print("\n  🎉 所有测试通过！")
    else:
        print("\n  ⚠️  有测试未通过")

    print(f"\n  💡 清理测试数据: python3 {sys.argv[0]} --cleanup")
    print(f"  🌐 查看网站: http://localhost:5000/match/{game_uuid}")
    print()


if __name__ == "__main__":
    main()
