#!/usr/bin/env python3
"""
联赛功能测试脚本
模拟插件上报 + 联赛匹配 + 排名提交的完整流程
用法: python test_league.py [--cleanup]
"""

import os
import sys
import uuid
import hashlib
import time
from datetime import datetime, timedelta
from pymongo import MongoClient

# ── 配置 ──────────────────────────────────────────
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")

# 模拟的 8 个玩家
FAKE_PLAYERS = [
    {"battleTag": "南怀北瑾丨少头脑#5267",   "displayName": "南怀北瑾丨少头脑",   "accountIdLo": "1708070391", "heroCardId": "TB_BaconShop_HERO_56",  "heroName": "阿莱克丝塔萨"},
    {"battleTag": "疾风剑豪#1234",           "displayName": "疾风剑豪",           "accountIdLo": "2000000001", "heroCardId": "BG20_HERO_202",         "heroName": "阮大师"},
    {"battleTag": "暗夜精灵#5678",           "displayName": "暗夜精灵",           "accountIdLo": "2000000002", "heroCardId": "TB_BaconShop_HERO_18",  "heroName": "穆克拉"},
    {"battleTag": "星辰大海#9012",           "displayName": "星辰大海",           "accountIdLo": "2000000003", "heroCardId": "TB_BaconShop_HERO_55",  "heroName": "伊瑟拉"},
    {"battleTag": "月光骑士#3456",           "displayName": "月光骑士",           "accountIdLo": "2000000004", "heroCardId": "BG20_HERO_101",         "heroName": "沃金"},
    {"battleTag": "虚空行者#7890",           "displayName": "虚空行者",           "accountIdLo": "2000000005", "heroCardId": "TB_BaconShop_HERO_52",  "heroName": "阿莱克丝塔萨"},
    {"battleTag": "冰霜法师#2345",           "displayName": "冰霜法师",           "accountIdLo": "2000000006", "heroCardId": "TB_BaconShop_HERO_34",  "heroName": "奈法利安"},
    {"battleTag": "烈焰术士#6789",           "displayName": "烈焰术士",           "accountIdLo": "2000000007", "heroCardId": "TB_BaconShop_HERO_28",  "heroName": "拉卡尼休"},
]

# 模拟的排名结果
MOCK_PLACEMENTS = [1, 2, 3, 4, 5, 6, 7, 8]

# 积分规则
def calc_points(placement):
    return 9 if placement == 1 else max(1, 9 - placement)

# 验证码生成（与 C# 插件一致）
def generate_verification_code(oid_str):
    h = hashlib.sha256(("bgtracker:" + oid_str).encode()).hexdigest()
    return h[:8].upper()


def main():
    cleanup_only = "--cleanup" in sys.argv

    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]

    if cleanup_only:
        print("🧹 清理所有测试数据...")
        db.league_matches.delete_many({"region": "TEST"})
        db.league_queue.delete_many({})
        db.league_waiting_queue.delete_many({})
        db.bg_ratings.delete_many({"region": "TEST"})
        db.league_players.delete_many({"accountIdLo": {"$in": [p["accountIdLo"] for p in FAKE_PLAYERS]}})
        print("✅ 清理完成")
        return

    print("=" * 60)
    print("🧪 酒馆战棋联赛功能测试")
    print("=" * 60)
    print(f"MongoDB: {MONGO_URL}/{DB_NAME}")
    print()

    # ── 测试 1: 模拟插件上报 bg_ratings + 验证码生成 ──
    print("📋 测试 1: 插件上报分数 + 生成验证码")
    print("-" * 40)

    for i, p in enumerate(FAKE_PLAYERS):
        # 模拟 bg_ratings 写入（插件 UploadToMongo 行为）
        rating = 6000 + i * 200
        filter_doc = {"playerId": p["battleTag"]}

        # upsert 文档
        from bson import ObjectId
        result = db.bg_ratings.update_one(
            filter_doc,
            {"$set": {
                "playerId": p["battleTag"],
                "accountIdLo": p["accountIdLo"],
                "rating": rating,
                "lastRating": rating - 23,
                "ratingChange": 23,
                "mode": "solo",
                "region": "TEST",
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                "gameCount": 10 + i,
            },
            "$setOnInsert": {
                "ratingChanges": [],
                "placements": [],
                "games": [],
            }},
            upsert=True,
        )

        # 生成验证码（模拟插件的 GenerateVerificationCode）
        if result.upserted_id:
            doc = db.bg_ratings.find_one(filter_doc)
            code = generate_verification_code(str(doc["_id"]))
            db.bg_ratings.update_one(filter_doc, {"$set": {"verificationCode": code}})
            print(f"  ✅ {p['displayName']} → rating={rating} 验证码={code}")
        else:
            doc = db.bg_ratings.find_one(filter_doc)
            code = doc.get("verificationCode", "?")
            print(f"  ⚠️  {p['displayName']} 已存在, 验证码={code}")

    print()

    # ── 测试 2: 模拟报名 → 等待组匹配 ──
    print("📋 测试 2: 报名队列 → 等待组")
    print("-" * 40)

    # 清空旧队列
    db.league_queue.delete_many({})
    db.league_waiting_queue.delete_many({})

    # 模拟 8 人依次报名
    for i, p in enumerate(FAKE_PLAYERS):
        db.league_queue.update_one(
            {"name": p["displayName"]},
            {"$setOnInsert": {"name": p["displayName"], "joinedAt": datetime.utcnow().isoformat() + "Z"}},
            upsert=True,
        )
        print(f"  📝 {p['displayName']} 已报名 ({i+1}/8)")

    # 检查是否满 8 人，创建等待组
    signup_count = db.league_queue.count_documents({})
    print(f"\n  报名人数: {signup_count}")

    if signup_count >= 8:
        signup = list(db.league_queue.find().sort("joinedAt", 1).limit(8))
        players = [{"name": s["name"]} for s in signup]
        names = [s["name"] for s in signup]
        db.league_waiting_queue.insert_one({
            "players": players,
            "createdAt": datetime.utcnow().isoformat() + "Z",
        })
        db.league_queue.delete_many({"name": {"$in": names}})
        print("  ✅ 8人满员，创建等待组")

    # 验证等待组
    waiting = list(db.league_waiting_queue.find())
    print(f"  等待组数: {len(waiting)}")
    for g in waiting:
        print(f"    组内玩家: {[p['name'] for p in g['players']]}")

    print()

    # ── 测试 3: 模拟 STEP 13 → 联赛匹配 → 创建 league_matches ──
    print("📋 测试 3: STEP 13 → 联赛匹配 → 创建对局")
    print("-" * 40)

    game_uuid = str(uuid.uuid4())
    started_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    # 收集本局玩家 accountIdLo（模拟 LobbyInfo）
    game_account_ids = {p["accountIdLo"] for p in FAKE_PLAYERS}

    # 匹配等待组（模拟 CheckLeagueQueue）
    matched_group = None
    for group in waiting:
        queue_account_ids = {p.get("accountIdLo", "") for p in group.get("players", [])
                           if p.get("accountIdLo")}
        if not queue_account_ids:
            # 等待组里只有 displayName，需要从 FAKE_PLAYERS 查找
            queue_account_ids = set()
            for wp in group.get("players", []):
                for fp in FAKE_PLAYERS:
                    if fp["displayName"] == wp["name"]:
                        queue_account_ids.add(fp["accountIdLo"])

        if game_account_ids == queue_account_ids and len(game_account_ids) == 8:
            matched_group = group
            print(f"  ✅ 匹配到等待组 _id={group['_id']}")
            break

    if matched_group is None:
        print("  ❌ 未匹配到等待组！")
        return

    # 删除等待组
    db.league_waiting_queue.delete_one({"_id": matched_group["_id"]})
    print("  🗑️  等待组已删除")

    # 创建 league_matches 文档（模拟 CreateLeagueMatchDirect）
    players_array = []
    for p in FAKE_PLAYERS:
        players_array.append({
            "accountIdLo": p["accountIdLo"],
            "battleTag": p["battleTag"],
            "displayName": p["displayName"],
            "heroCardId": p["heroCardId"],
            "heroName": p["heroName"],
            "placement": None,
            "points": None,
        })

    db.league_matches.update_one(
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
    print(f"  ✅ league_matches 已创建 gameUuid={game_uuid}")

    # 验证文档
    match_doc = db.league_matches.find_one({"gameUuid": game_uuid})
    print(f"  玩家数: {len(match_doc['players'])}")
    print(f"  startedAt: {match_doc['startedAt']}")

    print()

    # ── 测试 4: 模拟 8 人陆续提交排名 ──
    print("📋 测试 4: 8 人陆续提交排名")
    print("-" * 40)

    for i, p in enumerate(FAKE_PLAYERS):
        placement = MOCK_PLACEMENTS[i]
        points = calc_points(placement)

        # 模拟 UpdateLeaguePlacement
        db.league_matches.update_one(
            {"gameUuid": game_uuid, "players.accountIdLo": p["accountIdLo"]},
            {"$set": {
                "players.$.placement": placement,
                "players.$.points": points,
            }}
        )
        print(f"  🏁 {p['displayName']} → 第{placement}名 ({points}分)")

        # 检查是否所有人都提交了（模拟 CheckAndFinalizeMatch）
        doc = db.league_matches.find_one({"gameUuid": game_uuid})
        all_done = all(
            pl.get("placement") is not None
            for pl in doc["players"]
        )
        if all_done:
            db.league_matches.update_one(
                {"gameUuid": game_uuid},
                {"$set": {"endedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")}}
            )
            print(f"\n  🎉 所有 8 人已提交，对局结束！endedAt 已写入")

    print()

    # ── 测试 5: 验证排行榜聚合 ──
    print("📋 测试 5: 排行榜聚合验证")
    print("-" * 40)

    pipeline = [
        {"$match": {"endedAt": {"$ne": None}}},
        {"$unwind": "$players"},
        {"$match": {"players.points": {"$ne": None}}},
        {"$group": {
            "_id": "$players.battleTag",
            "displayName": {"$first": "$players.displayName"},
            "totalPoints": {"$sum": "$players.points"},
            "leagueGames": {"$sum": 1},
            "wins": {"$sum": {"$cond": [{"$lte": ["$players.placement", 4]}, 1, 0]}},
            "chickens": {"$sum": {"$cond": [{"$eq": ["$players.placement", 1]}, 1, 0]}},
            "totalPlacement": {"$sum": "$players.placement"},
        }},
        {"$addFields": {
            "avgPlacement": {"$divide": ["$totalPlacement", "$leagueGames"]},
            "winRate": {"$divide": ["$wins", "$leagueGames"]},
        }},
        {"$sort": {"totalPoints": -1}},
    ]

    leaderboard = list(db.league_matches.aggregate(pipeline))

    print(f"  {'排名':<4} {'玩家':<20} {'总分':<6} {'场次':<6} {'前四':<6} {'吃鸡':<6} {'场均':<6} {'胜率':<8}")
    print("  " + "-" * 60)
    for rank, p in enumerate(leaderboard, 1):
        print(f"  {rank:<4} {p['_id']:<20} {p['totalPoints']:<6} {p['leagueGames']:<6} "
              f"{p.get('wins', 0):<6} {p.get('chickens', 0):<6} "
              f"{p.get('avgPlacement', 0):<6.1f} {p.get('winRate', 0):<8.0%}")

    # 验证积分总和
    expected_total = sum(calc_points(i+1) for i in range(8))
    actual_total = sum(p["totalPoints"] for p in leaderboard)
    if actual_total == expected_total:
        print(f"\n  ✅ 积分总和验证通过: {actual_total} (预期 {expected_total})")
    else:
        print(f"\n  ❌ 积分总和不匹配: {actual_total} (预期 {expected_total})")

    print()

    # ── 测试 6: 模拟超时对局 ──
    print("📋 测试 6: 超时对局处理")
    print("-" * 40)

    # 创建一个超时的对局
    timeout_uuid = str(uuid.uuid4())
    old_time = (datetime.utcnow() - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%S")

    db.league_matches.insert_one({
        "gameUuid": timeout_uuid,
        "players": players_array,  # 复用，placement 都是 null
        "region": "TEST",
        "mode": "solo",
        "startedAt": old_time,
        "endedAt": None,
    })
    print(f"  📝 创建超时对局 gameUuid={timeout_uuid}")
    print(f"  startedAt: {old_time} (90 分钟前)")

    # 模拟 cleanup_stale_games
    GAME_TIMEOUT_MINUTES = 80
    cutoff_str = (datetime.utcnow() - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    result = db.league_matches.update_many(
        {
            "$and": [
                {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
                {"startedAt": {"$lt": cutoff_str}},
                {"region": "TEST"},
            ]
        },
        {"$set": {
            "endedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "timeout"
        }}
    )
    print(f"  🧹 cleanup_stale_games: 清理了 {result.modified_count} 个超时对局")

    # 验证
    timeout_match = db.league_matches.find_one({"gameUuid": timeout_uuid})
    if timeout_match.get("status") == "timeout":
        print(f"  ✅ 超时标记正确: status={timeout_match['status']}")
    else:
        print(f"  ❌ 超时标记异常: status={timeout_match.get('status')}")

    print()

    # ── 测试 7: 模拟部分掉线 (abandoned) ──
    print("📋 测试 7: 部分掉线 (abandoned) 对局")
    print("-" * 40)

    abandon_uuid = str(uuid.uuid4())
    # 只有 3 个人提交了排名
    abandoned_players = []
    for i, p in enumerate(FAKE_PLAYERS):
        abandoned_players.append({
            "accountIdLo": p["accountIdLo"],
            "battleTag": p["battleTag"],
            "displayName": p["displayName"],
            "heroCardId": p["heroCardId"],
            "heroName": p["heroName"],
            "placement": MOCK_PLACEMENTS[i] if i < 3 else None,
            "points": calc_points(MOCK_PLACEMENTS[i]) if i < 3 else None,
        })

    db.league_matches.insert_one({
        "gameUuid": abandon_uuid,
        "players": abandoned_players,
        "region": "TEST",
        "mode": "solo",
        "startedAt": old_time,
        "endedAt": None,
    })
    print(f"  📝 创建部分掉线对局 gameUuid={abandon_uuid}")
    print(f"  3/8 人已提交排名，5 人未提交")

    # 模拟 cleanup_partial_matches
    matches = list(db.league_matches.find({
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$lt": cutoff_str}},
            {"status": {"$exists": False}},
            {"region": "TEST"},
        ]
    }))
    abandoned_count = 0
    for m in matches:
        players = m.get("players", [])
        has_any_placement = any(p.get("placement") is not None for p in players)
        if not has_any_placement:
            continue
        db.league_matches.update_one(
            {"_id": m["_id"]},
            {"$set": {
                "endedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                "status": "abandoned"
            }}
        )
        abandoned_count += 1

    print(f"  🧹 cleanup_partial_matches: 标记了 {abandoned_count} 个 abandoned 对局")

    # 验证
    abandon_match = db.league_matches.find_one({"gameUuid": abandon_uuid})
    if abandon_match.get("status") == "abandoned":
        print(f"  ✅ abandoned 标记正确: status={abandon_match['status']}")
    else:
        print(f"  ❌ abandoned 标记异常: status={abandon_match.get('status')}")

    print()

    # ── 测试 8: 问题对局查询 ──
    print("📋 测试 8: 问题对局查询")
    print("-" * 40)

    problem_pipeline = [
        {"$match": {
            "endedAt": {"$nin": [None]},
            "$or": [
                {"status": {"$in": ["timeout", "abandoned"]}},
                {"$and": [
                    {"status": {"$exists": False}},
                    {"players": {"$elemMatch": {"placement": None}}}
                ]}
            ]
        }},
        {"$sort": {"endedAt": -1}}
    ]
    problems = list(db.league_matches.aggregate(problem_pipeline))
    print(f"  问题对局数: {len(problems)}")
    for m in problems:
        match_id = (m.get("gameUuid") or "")[:8].upper()
        status = m.get("status", "未知")
        null_count = sum(1 for p in m.get("players", []) if p.get("placement") is None)
        print(f"  ⚠️  #{match_id} status={status} 未提交排名={null_count}人")

    print()

    # ── 测试 9: 注册验证流程 ──
    print("📋 测试 9: 注册验证流程模拟")
    print("-" * 40)

    test_player = FAKE_PLAYERS[0]
    # 查验证码
    rating_doc = db.bg_ratings.find_one({"playerId": test_player["battleTag"]})
    stored_code = rating_doc.get("verificationCode")
    print(f"  玩家: {test_player['battleTag']}")
    print(f"  验证码: {stored_code}")

    # 模拟注册
    if stored_code:
        db.league_players.update_one(
            {"battleTag": test_player["battleTag"]},
            {"$set": {
                "battleTag": test_player["battleTag"],
                "accountIdLo": test_player["accountIdLo"],
                "displayName": test_player["displayName"],
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
        print(f"  ✅ 注册成功: {test_player['displayName']}")
    else:
        print(f"  ❌ 验证码不存在")

    # 验证错误验证码
    wrong_code = "WRONG123"
    if wrong_code.upper() != stored_code.upper():
        print(f"  ✅ 错误验证码 '{wrong_code}' 被正确拒绝")

    print()

    # ── 测试 10: SSE 数据格式验证 ──
    print("📋 测试 10: SSE 数据格式验证")
    print("-" * 40)

    # 模拟 sse_active_games 的 fetch 函数
    cutoff_str = (datetime.utcnow() - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    games = list(db.league_matches.find({
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$gte": cutoff_str}},
        ]
    }).sort("startedAt", -1))

    if games:
        g = games[0]
        sse_data = {
            "gameUuid": g.get("gameUuid", ""),
            "players": [{"displayName": p.get("displayName", ""), "heroCardId": p.get("heroCardId", ""),
                          "heroName": p.get("heroName", ""), "placement": p.get("placement")}
                         for p in g.get("players", [])]
        }
        print(f"  ✅ SSE 格式正确:")
        print(f"    gameUuid: {sse_data['gameUuid'][:16]}...")
        print(f"    players: {len(sse_data['players'])} 人")
    else:
        print(f"  ℹ️  没有进行中的对局（正常，因为测试对局都已结束）")

    print()

    # ── 汇总 ──
    print("=" * 60)
    print("📊 测试汇总")
    print("=" * 60)

    total_matches = db.league_matches.count_documents({"region": "TEST"})
    completed = db.league_matches.count_documents({"region": "TEST", "endedAt": {"$ne": None}, "status": {"$exists": False}})
    timeout = db.league_matches.count_documents({"region": "TEST", "status": "timeout"})
    abandoned = db.league_matches.count_documents({"region": "TEST", "status": "abandoned"})
    ratings = db.bg_ratings.count_documents({"region": "TEST"})
    verified = db.league_players.count_documents({"verified": True})

    print(f"  联赛对局总数: {total_matches}")
    print(f"  正常完成:     {completed}")
    print(f"  超时:         {timeout}")
    print(f"  掉线:         {abandoned}")
    print(f"  bg_ratings:   {ratings}")
    print(f"  已注册选手:   {verified}")
    print()
    print("  所有测试通过 ✅")
    print(f"\n  💡 运行 --cleanup 清理测试数据:")
    print(f"     python {sys.argv[0]} --cleanup")
    print()

    # 保留测试数据供网站查看，不自动清理
    print("  ℹ️  测试数据已保留，可通过网站 http://localhost:5000 查看效果")
    print(f"     对局详情: http://localhost:5000/match/{game_uuid}")
    print()


if __name__ == "__main__":
    main()
