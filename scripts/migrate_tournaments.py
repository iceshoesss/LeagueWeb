#!/usr/bin/env python3
"""从 tournament_groups 反向生成 tournaments 集合记录。

用法: python scripts/migrate_tournaments.py [--dry-run]

运行前确保 MONGO_URL 环境变量正确（默认 mongodb://mongo:27017）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

    # 为归档赛事预计算 bracketData
    archived = [t for t in tournaments if t["status"] == "archived"]
    if archived:
        print(f"\n预计算 {len(archived)} 个归档赛事的 bracketData...")
        from data import get_group_rankings, SORT_KEYS
        GROUP_LABELS = "ABCD"

        for t_meta in archived:
            tname = t_meta["name"]
            groups = list(db.tournament_groups.find({"tournamentName": tname}).sort([("round", 1), ("groupIndex", 1)]))
            if not groups:
                continue

            layout = t_meta.get("layout", "bracket")
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

            db.tournaments.update_one(
                {"name": tname},
                {"$set": {"bracketData": [{"name": tname, "rounds": rounds_data, "layout": layout}]}},
            )
            print(f"  ✓ {tname}")

        print(f"预计算完成")


if __name__ == "__main__":
    main()