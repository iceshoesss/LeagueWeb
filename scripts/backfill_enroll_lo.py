#!/usr/bin/env python3
"""补填报名表中缺失的 accountIdLo

从 league_players 和 player_records 交叉查找，回填到 tournament_enrollments。

用法:
  python backfill_enroll_lo.py            # 预览（不写入）
  python backfill_enroll_lo.py --apply    # 执行写入

环境变量:
  MONGO_URL  MongoDB 地址 (默认 mongodb://mongo:27017)
  DB_NAME    数据库名 (默认 hearthstone)
"""

import os
import sys

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")


def main():
    apply = "--apply" in sys.argv

    from pymongo import MongoClient
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    # 找所有缺 Lo 的报名记录
    enrollments = list(db.tournament_enrollments.find(
        {"$or": [
            {"accountIdLo": {"$exists": False}},
            {"accountIdLo": ""},
            {"accountIdLo": None},
        ]},
        {"battleTag": 1, "displayName": 1, "status": 1}
    ))

    if not enrollments:
        print("✅ 所有报名记录都有 accountIdLo，无需补填")
        return

    print(f"找到 {len(enrollments)} 条缺 Lo 的报名记录\n")

    filled = 0
    not_found = []

    for e in enrollments:
        tag = e.get("battleTag", "")
        lo = None
        source = None

        # 优先从 league_players 查
        lp = db.league_players.find_one({"battleTag": tag}, {"accountIdLo": 1})
        if lp and lp.get("accountIdLo"):
            lo = str(lp["accountIdLo"])
            source = "league_players"

        # 其次从 player_records 查
        if not lo:
            pr = db.player_records.find_one({"playerId": tag}, {"accountIdLo": 1})
            if pr and pr.get("accountIdLo"):
                lo = str(pr["accountIdLo"])
                source = "player_records"

        if lo:
            filled += 1
            print(f"  ✅ {tag} → Lo={lo} (来自 {source})")
            if apply:
                db.tournament_enrollments.update_one(
                    {"_id": e["_id"]},
                    {"$set": {"accountIdLo": lo}}
                )
        else:
            not_found.append(tag)
            print(f"  ❌ {tag} → 未找到 Lo")

    print(f"\n{'已写入' if apply else '待写入'}: {filled} 条")
    if not_found:
        print(f"仍未找到: {len(not_found)} 人")
        for t in not_found:
            print(f"  - {t}")
        print("\n这些人需要用 bg_tool/HDT 插件打一局才能获得 Lo")


if __name__ == "__main__":
    main()
