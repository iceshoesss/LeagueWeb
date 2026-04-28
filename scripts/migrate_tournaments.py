#!/usr/bin/env python3
"""从 tournament_groups 反向生成 tournaments 集合记录。

用法: python scripts/migrate_tournaments.py [--dry-run]

运行前确保 MONGO_URL 环境变量正确（默认 mongodb://mongo:27017）。
"""

import os
import sys
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")

def main():
    dry_run = "--dry-run" in sys.argv
    db = MongoClient(MONGO_URL)[DB_NAME]

    # 检查是否已有 tournaments 记录
    existing = db.tournaments.count_documents({})
    if existing > 0:
        print(f"tournaments 集合已有 {existing} 条记录，跳过迁移")
        print("如需重新运行，先清空 tournaments 集合: db.tournaments.drop()")
        return

    # 从 tournament_groups 提取所有不重复的赛事名
    pipeline = [
        {"$group": {
            "_id": "$tournamentName",
            "count": {"$sum": 1},
            "createdAt": {"$min": "$createdAt"},
            "layout": {"$first": "$layout"},
            "statusCounts": {"$push": "$status"},
        }},
    ]

    tournaments = []
    for t in db.tournament_groups.aggregate(pipeline):
        name = t["_id"]
        if not name:
            continue

        # 推断布局
        layout = t.get("layout") or "bracket"

        # 全部组都是 done → 已结束 → 归档
        statuses = t.get("statusCounts", [])
        all_done = all(s == "done" for s in statuses)
        status = "archived" if all_done else "active"

        tournaments.append({
            "name": name,
            "status": status,
            "layout": layout,
            "seasonName": "",  # 旧数据无赛季信息
            "createdAt": t.get("createdAt") or "",
            "archivedAt": t.get("createdAt") if all_done else None,
        })

    if not tournaments:
        print("没有找到 tournament_groups 数据")
        return

    print(f"找到 {len(tournaments)} 个赛事:")
    for t in tournaments:
        print(f"  - {t['name']} (layout={t['layout']}, status={t['status']})")

    if dry_run:
        print("\n[dry-run] 未写入数据库")
        return

    db.tournaments.insert_many(tournaments)
    print(f"\n已写入 {len(tournaments)} 条 tournaments 记录")


if __name__ == "__main__":
    main()
