"""
模拟海选赛事数据：2 组 16 人，每组 BO1，前 4 晋级
用法：python mock_qualifier.py [MONGO_URL]
默认 mongodb://localhost:27017
"""

import sys
import hashlib
import random
from datetime import datetime, UTC
from pymongo import MongoClient

MONGO_URL = sys.argv[1] if len(sys.argv) > 1 else "mongodb://localhost:27017"
DB_NAME = "hearthstone"
TOURNAMENT_NAME = "2026 春季赛海选"

# ── 16 个模拟玩家 ─────────────────────────────────
PLAYERS = [
    {"battleTag": "衣锦夜行#1001", "displayName": "衣锦夜行", "accountIdLo": "1000001", "heroCardId": "TB_BaconShop_HERO_56", "heroName": "阿莱克丝塔萨"},
    {"battleTag": "瓦莉拉#1002",   "displayName": "瓦莉拉",   "accountIdLo": "1000002", "heroCardId": "TB_BaconShop_HERO_02", "heroName": "帕奇维克"},
    {"battleTag": "雷克萨#1003",   "displayName": "雷克萨",   "accountIdLo": "1000003", "heroCardId": "TB_BaconShop_HERO_22", "heroName": "巫妖王"},
    {"battleTag": "古尔丹#1004",   "displayName": "古尔丹",   "accountIdLo": "1000004", "heroCardId": "TB_BaconShop_HERO_19", "heroName": "米尔豪斯"},
    {"battleTag": "吉安娜#1005",   "displayName": "吉安娜",   "accountIdLo": "1000005", "heroCardId": "TB_BaconShop_HERO_01", "heroName": "鼠王"},
    {"battleTag": "萨尔#1006",     "displayName": "萨尔",     "accountIdLo": "1000006", "heroCardId": "TB_BaconShop_HERO_08", "heroName": "尤格-萨隆"},
    {"battleTag": "乌瑟尔#1007",   "displayName": "乌瑟尔",   "accountIdLo": "1000007", "heroCardId": "TB_BaconShop_HERO_13", "heroName": "伊瑟拉"},
    {"battleTag": "玛法里奥#1008", "displayName": "玛法里奥", "accountIdLo": "1000008", "heroCardId": "TB_BaconShop_HERO_36", "heroName": "拉卡尼休"},
    {"battleTag": "安度因#1009",   "displayName": "安度因",   "accountIdLo": "1000009", "heroCardId": "TB_BaconShop_HERO_07", "heroName": "乔治"},
    {"battleTag": "莉亚德琳#1010", "displayName": "莉亚德琳", "accountIdLo": "1000010", "heroCardId": "TB_BaconShop_HERO_45", "heroName": "辛达苟萨"},
    {"battleTag": "泰兰德#1011",   "displayName": "泰兰德",   "accountIdLo": "1000011", "heroCardId": "TB_BaconShop_HERO_11", "heroName": "拉兹"},
    {"battleTag": "沃金#1012",     "displayName": "沃金",     "accountIdLo": "1000012", "heroCardId": "TB_BaconShop_HERO_15", "heroName": "诺兹多姆"},
    {"battleTag": "凯恩#1013",     "displayName": "凯恩",     "accountIdLo": "1000013", "heroCardId": "TB_BaconShop_HERO_20", "heroName": "馆长"},
    {"battleTag": "希尔瓦娜斯#1014","displayName": "希尔瓦娜斯","accountIdLo": "1000014", "heroCardId": "TB_BaconShop_HERO_29", "heroName": "斯尼德"},
    {"battleTag": "加尔鲁什#1015", "displayName": "加尔鲁什", "accountIdLo": "1000015", "heroCardId": "TB_BaconShop_HERO_06", "heroName": "永恒者托奇"},
    {"battleTag": "伊利丹#1016",   "displayName": "伊利丹",   "accountIdLo": "1000016", "heroCardId": "TB_BaconShop_HERO_38", "heroName": "玛维"},
]

# 每组排名（前 4 晋级）
GROUP_PLACEMENTS = [
    # 组 1：玩家 0-7，排名顺序
    [1, 3, 5, 7, 2, 4, 6, 8],  # placement 值
    # 组 2：玩家 8-15
    [2, 4, 6, 8, 1, 3, 5, 7],
]

# 积分规则
def calc_points(placement):
    return 9 if placement == 1 else max(1, 9 - placement)


def main():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── 1. 插入 league_players ─────────────────────
    print("插入 league_players...")
    for p in PLAYERS:
        db.league_players.update_one(
            {"battleTag": p["battleTag"]},
            {"$set": {
                "battleTag": p["battleTag"],
                "displayName": p["displayName"],
                "accountIdLo": p["accountIdLo"],
                "verified": True,
                "verifiedAt": now_str,
                "createdAt": now_str,
            }},
            upsert=True,
        )
    print(f"  ✅ {len(PLAYERS)} 个玩家")

    # ── 2. 插入 tournament_groups（2 组，done）─────
    print("插入 tournament_groups...")
    db.tournament_groups.delete_many({"tournamentName": TOURNAMENT_NAME})

    groups = []
    for gi in range(2):
        start = gi * 8
        group_players = []
        for pi in range(8):
            p = PLAYERS[start + pi]
            placement = GROUP_PLACEMENTS[gi][pi]
            group_players.append({
                "battleTag": p["battleTag"],
                "accountIdLo": p["accountIdLo"],
                "displayName": p["displayName"],
                "heroCardId": p["heroCardId"],
                "heroName": p["heroName"],
                "empty": False,
            })

        group_doc = {
            "tournamentName": TOURNAMENT_NAME,
            "round": 1,
            "groupIndex": gi + 1,
            "status": "done",
            "boN": 1,
            "gamesPlayed": 1,
            "players": group_players,
            "nextRoundGroupId": (gi + 2) // 2,
            "startedAt": now_str,
            "endedAt": now_str,
        }
        result = db.tournament_groups.insert_one(group_doc)
        groups.append(result.inserted_id)
        print(f"  ✅ 组 {gi+1}: {len(group_players)} 人, status=done")

    # ── 3. 插入 league_matches（每组 1 局 BO1）─────
    print("插入 league_matches...")
    for gi, group_id in enumerate(groups):
        start = gi * 8
        match_players = []
        for pi in range(8):
            p = PLAYERS[start + pi]
            placement = GROUP_PLACEMENTS[gi][pi]
            match_players.append({
                "accountIdLo": p["accountIdLo"],
                "battleTag": p["battleTag"],
                "displayName": p["displayName"],
                "heroCardId": p["heroCardId"],
                "heroName": p["heroName"],
                "placement": placement,
                "points": calc_points(placement),
            })

        game_uuid = hashlib.sha256(
            f"{TOURNAMENT_NAME}-R1G{gi+1}-BO1".encode()
        ).hexdigest()[:32]
        game_uuid = f"{game_uuid[:8]}-{game_uuid[8:12]}-{game_uuid[12:16]}-{game_uuid[16:20]}-{game_uuid[20:32]}"

        match_doc = {
            "gameUuid": game_uuid,
            "region": "CN",
            "mode": "solo",
            "startedAt": now_str,
            "endedAt": now_str,
            "tournamentName": TOURNAMENT_NAME,
            "tournamentGroupId": group_id,
            "tournamentRound": 1,
            "players": sorted(match_players, key=lambda x: x["placement"]),
        }
        db.league_matches.insert_one(match_doc)
        print(f"  ✅ 组 {gi+1} 对局: gameUuid={game_uuid[:12]}...")

    # ── 总结 ──────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"模拟完成！赛事：{TOURNAMENT_NAME}")
    print(f"  - {len(PLAYERS)} 个 league_players")
    print(f"  - 2 个 tournament_groups（status=done）")
    print(f"  - 2 个 league_matches（BO1）")
    print(f"\n晋级者（每组前4）：")
    for gi in range(2):
        start = gi * 8
        quals = [PLAYERS[start + pi]["displayName"] for pi in range(8) if GROUP_PLACEMENTS[gi][pi] <= 4]
        print(f"  组{gi+1}: {', '.join(quals)}")
    print(f"\n下一步：去选手管理页标记 8 个种子选手，然后测试「海选晋级洗牌」")

    client.close()


if __name__ == "__main__":
    main()
