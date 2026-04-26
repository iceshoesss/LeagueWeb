"""迁移脚本：为所有现有 tournament_groups 补写 rankings 字段

用法：python scripts/migrate_rankings.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db
from data import recalc_group_rankings

def main():
    db = get_db()
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
