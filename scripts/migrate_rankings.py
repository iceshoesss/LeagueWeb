#!/usr/bin/env python3
"""迁移脚本：为所有现有 tournament_groups 重算 rankings 字段

用法:
  python migrate_rankings.py [--force]

参数:
  --force  强制重算所有组（包括已有 rankings 的组）

环境变量:
  MONGO_URL  MongoDB 地址 (默认 mongodb://mongo:27017)
  DB_NAME    数据库名 (默认 hearthstone)
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient
from data import recalc_group_rankings

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")


def main():
    force = "--force" in sys.argv

    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    groups = list(db.tournament_groups.find({}, {"_id": 1, "tournamentName": 1, "round": 1, "groupIndex": 1, "rankings": 1}))
    total = len(groups)
    skipped = 0
    updated = 0

    for g in groups:
        if not force and g.get("rankings"):
            skipped += 1
            continue
        gid = g["_id"]
        label = f"R{g.get('round')}G{g.get('groupIndex')} ({g.get('tournamentName', '?')})"
        print(f"  {label} ...", end=" ", flush=True)
        recalc_group_rankings(db, gid)
        updated += 1
        print("OK")

    mode = "强制重算" if force else "补写"
    print(f"\n完成：共 {total} 组，{mode} {updated} 组，跳过 {skipped} 组")


if __name__ == "__main__":
    main()
