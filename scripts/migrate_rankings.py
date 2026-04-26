"""迁移脚本：为所有现有 tournament_groups 补写 rankings 字段

用法：
  python scripts/migrate_rankings.py <mongo_url> <db_name>
  python scripts/migrate_rankings.py mongodb://localhost:27017 hearthstone
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient
from data import recalc_group_rankings

def main():
    if len(sys.argv) < 3:
        print("用法: python scripts/migrate_rankings.py <mongo_url> <db_name>")
        print("示例: python scripts/migrate_rankings.py mongodb://localhost:27017 hearthstone")
        sys.exit(1)

    mongo_url = sys.argv[1]
    db_name = sys.argv[2]

    client = MongoClient(mongo_url)
    db = client[db_name]
    groups = list(db.tournament_groups.find({}, {"_id": 1, "tournamentName": 1, "round": 1, "groupIndex": 1, "rankings": 1}))
    total = len(groups)
    skipped = 0
    updated = 0

    for g in groups:
        if g.get("rankings"):
            skipped += 1
            continue
        gid = g["_id"]
        print(f"  R{g.get('round')}G{g.get('groupIndex')} ({g.get('tournamentName', '?')}) ...", end=" ", flush=True)
        recalc_group_rankings(db, gid)
        updated += 1
        print("OK")

    print(f"\n完成：共 {total} 组，更新 {updated} 组，跳过 {skipped} 组（已有 rankings）")

if __name__ == "__main__":
    main()
