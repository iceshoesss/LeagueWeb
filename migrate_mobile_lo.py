#!/usr/bin/env python3
"""
迁移脚本：将所有 accountIdLo 为空的手机玩家补上 battleTag 作为伪 Lo。
涉及集合：league_players, tournament_groups, league_matches

用法：
  python migrate_mobile_lo.py              # dry-run（只打印，不写入）
  python migrate_mobile_lo.py --apply      # 实际执行写入
"""

import sys
import os
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")

DRY_RUN = "--apply" not in sys.argv

def main():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    stats = {"league_players": 0, "tournament_groups": 0, "league_matches": 0}

    # 1. league_players: accountIdLo 为空或空串 → 补 battleTag
    for p in db.league_players.find({"$or": [{"accountIdLo": ""}, {"accountIdLo": {"$exists": False}}]}):
        bt = p.get("battleTag", "")
        if not bt:
            print(f"  [SKIP] league_players {_id(p)}: battleTag 也为空，跳过")
            continue
        print(f"  league_players {_id(p)}: accountIdLo '' → '{bt}'")
        stats["league_players"] += 1
        if not DRY_RUN:
            db.league_players.update_one({"_id": p["_id"]}, {"$set": {"accountIdLo": bt}})

    # 2. tournament_groups: players 中 accountIdLo 为空的 → 补 battleTag
    for g in db.tournament_groups.find({"players.accountIdLo": {"$in": ["", None]}}):
        gid = _id(g)
        updated = False
        for i, pl in enumerate(g.get("players", [])):
            lo = pl.get("accountIdLo", "")
            if lo in ("", None):
                bt = pl.get("battleTag", "")
                if not bt:
                    print(f"  [SKIP] tournament_groups {gid} player[{i}]: battleTag 也为空")
                    continue
                print(f"  tournament_groups {gid} player[{i}]: accountIdLo '' → '{bt}'")
                stats["tournament_groups"] += 1
                updated = True
                if not DRY_RUN:
                    db.tournament_groups.update_one(
                        {"_id": g["_id"]},
                        {"$set": {f"players.{i}.accountIdLo": bt}}
                    )
        if updated:
            # 重新加载（dry-run 时不需要）
            pass

    # 3. league_matches: players 中 accountIdLo 为空的 → 补 battleTag
    for m in db.league_matches.find({"players.accountIdLo": {"$in": ["", None]}}):
        mid = _id(m)
        for i, pl in enumerate(m.get("players", [])):
            lo = pl.get("accountIdLo", "")
            if lo in ("", None):
                bt = pl.get("battleTag", "")
                if not bt:
                    print(f"  [SKIP] league_matches {mid} player[{i}]: battleTag 也为空")
                    continue
                print(f"  league_matches {mid} player[{i}]: accountIdLo '' → '{bt}'")
                stats["league_matches"] += 1
                if not DRY_RUN:
                    db.league_matches.update_one(
                        {"_id": m["_id"]},
                        {"$set": {f"players.{i}.accountIdLo": bt}}
                    )

    print()
    print("=" * 50)
    print(f"{'[DRY-RUN] ' if DRY_RUN else ''}迁移完成:")
    print(f"  league_players:     {stats['league_players']} 条")
    print(f"  tournament_groups:  {stats['tournament_groups']} 个玩家")
    print(f"  league_matches:     {stats['league_matches']} 个玩家")
    if DRY_RUN:
        print()
        print("这是 dry-run，没有实际写入。加 --apply 参数执行写入。")

def _id(doc):
    return str(doc.get("_id", "?"))

if __name__ == "__main__":
    main()
