#!/usr/bin/env python3
"""手动替换晋级玩家

把已完成组中错误晋级的玩家替换为本应晋级的玩家。
用于修正因晋级规则变更时机导致的晋级错误。

用法:
  python fix_swap_player.py <赛事名> <轮次> <组号>

示例:
  MONGO_URL=mongodb://localhost:27017 python fix_swap_player.py "2026 春季赛" 1 3

脚本会：
1. 展示指定已完成组的玩家排名
2. 展示下一轮对应分组的玩家
3. 让你选择替换哪两个玩家
"""

import os
import sys

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")


def main():
    if len(sys.argv) < 4:
        print("用法: python fix_swap_player.py <赛事名> <轮次> <组号>")
        sys.exit(1)

    tournament_name = sys.argv[1]
    current_round = int(sys.argv[2])
    group_index = int(sys.argv[3])

    from pymongo import MongoClient
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    # 1. 找当前组
    done_group = db.tournament_groups.find_one({
        "tournamentName": tournament_name,
        "round": current_round,
        "groupIndex": group_index,
        "status": "done",
    })
    if not done_group:
        print(f"未找到 R{current_round}G{group_index} (status=done)")
        sys.exit(1)

    players = done_group.get("players", [])
    rule = done_group.get("advancementRule", "chicken")
    print(f"\n{'='*50}")
    print(f"  {tournament_name}  R{current_round}G{group_index}  规则={rule}  BO{done_group.get('boN', 1)}")
    print(f"{'='*50}")
    print("排名（按当前规则）:")
    for i, p in enumerate(players):
        if p.get("empty"):
            continue
        name = p.get("displayName") or p.get("battleTag") or "?"
        pts = p.get("totalPoints", 0)
        games = p.get("games", [])
        qualified = "✓ 晋级" if p.get("qualified") else "✗"
        print(f"  {i+1}. {name}  总分={pts}  单局={games}  {qualified}")

    # 2. 找下一轮分组
    groups_in_round = db.tournament_groups.count_documents({
        "round": current_round, "tournamentName": tournament_name,
    })
    next_gi = (group_index + 1) // 2 if groups_in_round > 1 else 1

    next_group = db.tournament_groups.find_one({
        "tournamentName": tournament_name,
        "round": current_round + 1,
        "groupIndex": next_gi,
    })
    if not next_group:
        print(f"\n未找到下一轮分组 R{current_round + 1}G{next_gi}")
        sys.exit(1)

    next_players = next_group.get("players", [])
    print(f"\n{'='*50}")
    print(f"  下一轮  R{current_round + 1}G{next_gi}  状态={next_group.get('status', 'waiting')}")
    print(f"{'='*50}")
    for i, p in enumerate(next_players):
        if p.get("empty"):
            print(f"  [{i}] (待定)")
            continue
        name = p.get("displayName") or p.get("battleTag") or "?"
        print(f"  [{i}] {name}  lo={p.get('accountIdLo')}")

    # 3. 选择替换
    print()
    wrong_lo = input("错误晋级的 accountIdLo（下一轮中的）: ").strip()
    correct_lo = input("本应晋级的 accountIdLo（当前组中的）: ").strip()

    if not wrong_lo or not correct_lo:
        print("accountIdLo 不能为空")
        sys.exit(1)

    # 从当前组取正确的玩家数据
    correct_player = None
    for p in players:
        if str(p.get("accountIdLo", "")) == correct_lo:
            correct_player = {
                "battleTag": p.get("battleTag", ""),
                "accountIdLo": p.get("accountIdLo", ""),
                "displayName": p.get("displayName", ""),
                "heroCardId": p.get("heroCardId", ""),
                "heroName": p.get("heroName", ""),
                "empty": False,
            }
            break

    if not correct_player:
        print(f"当前组中未找到 lo={correct_lo}")
        sys.exit(1)

    # 在下一轮中找错误玩家
    wrong_idx = None
    for i, p in enumerate(next_players):
        if str(p.get("accountIdLo", "")) == wrong_lo:
            wrong_idx = i
            break

    if wrong_idx is None:
        print(f"下一轮中未找到 lo={wrong_lo}")
        sys.exit(1)

    old_name = next_players[wrong_idx].get("displayName") or next_players[wrong_idx].get("battleTag")
    new_name = correct_player["displayName"] or correct_player["battleTag"]
    print(f"\n替换: R{current_round + 1}G{next_gi} 位置[{wrong_idx}]")
    print(f"  旧: {old_name}")
    print(f"  新: {new_name}")

    confirm = input("\n确认？(y/N): ").strip().lower()
    if confirm != "y":
        print("已取消")
        return

    result = db.tournament_groups.update_one(
        {"_id": next_group["_id"]},
        {"$set": {f"players.{wrong_idx}": correct_player}}
    )
    print(f"完成: modified={result.modified_count}")


if __name__ == "__main__":
    main()
