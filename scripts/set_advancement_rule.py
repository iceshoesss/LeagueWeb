#!/usr/bin/env python3
"""修改赛事晋级规则

用法:
  python set_advancement_rule.py <赛事名称> <规则>

规则:
  chicken  吃鸡规则（总积分 → 吃鸡次数 → 最后一局排名）
  golden   黄金赛规则（总积分 → 单局最高分 → 最后一局分数）

示例:
  python set_advancement_rule.py "2026 春季赛" golden
  python set_advancement_rule.py "2026 春季赛" chicken
"""

import os
import sys

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")


def main():
    if len(sys.argv) < 3:
        print("用法: python set_advancement_rule.py <赛事名称> <规则>")
        print("规则: chicken | golden")
        sys.exit(1)

    tournament_name = sys.argv[1]
    rule = sys.argv[2]

    if rule not in ("chicken", "golden"):
        print(f"无效规则: {rule}，只支持 chicken 或 golden")
        sys.exit(1)

    from pymongo import MongoClient
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    # 查看当前状态
    groups = list(db.tournament_groups.find(
        {"tournamentName": tournament_name},
        {"round": 1, "groupIndex": 1, "status": 1, "advancementRule": 1}
    ))

    if not groups:
        print(f"未找到赛事: {tournament_name}")
        sys.exit(1)

    rule_label = "黄金赛规则" if rule == "golden" else "吃鸡规则"
    print(f"\n赛事: {tournament_name}")
    print(f"目标规则: {rule_label} ({rule})")
    print(f"共 {len(groups)} 个分组:\n")

    for g in sorted(groups, key=lambda x: (x.get("round", 0), x.get("groupIndex", 0))):
        current = g.get("advancementRule", "未设置")
        status = g.get("status", "waiting")
        print(f"  R{g.get('round', '?')}G{g.get('groupIndex', '?')}  状态={status}  当前规则={current}")

    confirm = input(f"\n确认将所有分组规则改为 {rule_label}？(y/N): ").strip().lower()
    if confirm != "y":
        print("已取消")
        return

    result = db.tournament_groups.update_many(
        {"tournamentName": tournament_name},
        {"$set": {"advancementRule": rule}}
    )

    print(f"\n已更新 {result.modified_count} 个分组 → {rule_label}")


if __name__ == "__main__":
    main()
