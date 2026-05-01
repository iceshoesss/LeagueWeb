#!/usr/bin/env python3
"""为已归档但缺少 bracketData 的赛事补充预计算数据。

用法: python scripts/backfill_bracket_data.py [--dry-run]
"""

import os
import sys
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")

GROUP_LABELS = "ABCD"


def compute_bracket_for_tournament(db, tname, layout):
    """计算单个赛事的 bracket 数据"""
    groups = list(db.tournament_groups.find({"tournamentName": tname}).sort([("round", 1), ("groupIndex", 1)]))
    if not groups:
        return None

    all_rankings = {}
    for g in groups:
        cached = g.get("rankings")
        if cached:
            all_rankings[str(g["_id"])] = cached

    rounds_map = {}
    for g in groups:
        r = g.get("round", 1)
        rounds_map.setdefault(r, []).append(g)

    sorted_rounds = sorted(rounds_map.keys())
    total_rounds = len(sorted_rounds)

    def _round_label(r):
        if layout == "grid":
            return f"第 {r} 轮" if total_rounds > 1 else "海选"
        if r == total_rounds:
            return "决赛"
        if r == total_rounds - 1:
            return "半决赛"
        return f"第 {r} 轮"

    def _group_label(r, gi, total):
        if layout == "grid":
            return f"{GROUP_LABELS[gi]} 组" if total <= 4 else f"{GROUP_LABELS[gi % 4]}{gi // 4 + 1} 组"
        if r == total_rounds and total == 1:
            return "决赛"
        if r == 1:
            return f"{GROUP_LABELS[gi % 4]}{gi // 4 + 1} 组" if total > 4 else f"{GROUP_LABELS[gi]} 组"
        return f"{gi + 1} 组"

    rounds_data = []
    for r in sorted_rounds:
        rgroups = sorted(rounds_map[r], key=lambda g: g.get("groupIndex", 0))
        total = len(rgroups)
        groups_data = []
        for g in rgroups:
            gi = g.get("groupIndex", 1) - 1
            rankings = all_rankings.get(str(g["_id"]), {})
            for p in g.get("players", []):
                lo = str(p.get("accountIdLo", ""))
                rd = rankings.get(lo)
                if rd:
                    p["totalPoints"] = rd["totalPoints"]
                    p["games"] = rd.get("games", [])
                    p["points"] = rd["totalPoints"]
                    p["qualified"] = rd["qualified"]
                    p["eliminated"] = rd["eliminated"]
                else:
                    p["totalPoints"] = 0
                    p["games"] = []
                    p["points"] = None
                    p["qualified"] = False
                    p["eliminated"] = False
                p["empty"] = p.get("empty", False)
            groups_data.append({
                "label": _group_label(r, gi, total),
                "status": g.get("status", "waiting"),
                "boN": g.get("boN", 1),
                "gamesPlayed": g.get("gamesPlayed", 0),
                "players": g.get("players", []),
                "nextRoundGroupId": g.get("nextRoundGroupId"),
                "advancementRule": g.get("advancementRule", "chicken"),
            })
        rounds_data.append({"label": _round_label(r), "groups": groups_data})

    return [{"name": tname, "rounds": rounds_data, "layout": layout}]


def main():
    dry_run = "--dry-run" in sys.argv
    db = MongoClient(MONGO_URL)[DB_NAME]

    # 找已归档但没有 bracketData 的赛事
    archived = list(db.tournaments.find({"status": "archived"}))
    missing = [t for t in archived if "bracketData" not in t]

    if not missing:
        print(f"检查了 {len(archived)} 个归档赛事，全部已有 bracketData，无需补算")
        return

    print(f"找到 {len(missing)} 个归档赛事缺少 bracketData:")
    for t in missing:
        print(f"  - {t['name']}")

    if dry_run:
        print("\n[dry-run] 未写入数据库")
        return

    for t in missing:
        tname = t["name"]
        layout = t.get("layout", "bracket")
        bracket = compute_bracket_for_tournament(db, tname, layout)
        if bracket:
            db.tournaments.update_one(
                {"_id": t["_id"]},
                {"$set": {"bracketData": bracket}},
            )
            print(f"  ✓ {tname}")
        else:
            print(f"  ⚠ {tname} — 无分组数据，跳过")

    print(f"\n补算完成")


if __name__ == "__main__":
    main()
