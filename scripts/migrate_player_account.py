#!/usr/bin/env python3
"""
迁移脚本：将旧账号的比赛记录关联到新账号。
使新账号的 player 页面能展示旧账号的历史数据。

仅修改 league_matches 和 tournament_groups 中的玩家标识，
不改动 player_records / league_players / 队列等。

用法：
  python scripts/migrate_player_account.py              # dry-run（只打印，不写入）
  python scripts/migrate_player_account.py --apply      # 实际执行写入

  python scripts/migrate_player_account.py \
    --old-lo "11111111" --new-lo "99999999" \
    --new-tag "新名字#5678" --new-display "新名字"

参数说明：
  --old-lo       旧账号的 accountIdLo（必填）
  --new-lo       新账号的 accountIdLo（必填）
  --new-tag      新账号的 BattleTag（可选，不传则只改 accountIdLo）
  --new-display  新账号的显示名（可选，默认取 new-tag # 前部分）
  --apply        实际执行写入（默认 dry-run）
"""

import os
import sys

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")

DRY_RUN = "--apply" not in sys.argv


def parse_args():
    args = {"old_lo": None, "new_lo": None, "new_tag": None, "new_display": None}
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i] == "--apply":
            i += 1
            continue
        if argv[i] == "--old-lo" and i + 1 < len(argv):
            args["old_lo"] = argv[i + 1].strip()
            i += 2
        elif argv[i] == "--new-lo" and i + 1 < len(argv):
            args["new_lo"] = argv[i + 1].strip()
            i += 2
        elif argv[i] == "--new-tag" and i + 1 < len(argv):
            args["new_tag"] = argv[i + 1].strip()
            i += 2
        elif argv[i] == "--new-display" and i + 1 < len(argv):
            args["new_display"] = argv[i + 1].strip()
            i += 2
        else:
            print(f"未知参数: {argv[i]}")
            sys.exit(1)
    return args


def _id(doc):
    return str(doc.get("_id", "?"))


def main():
    args = parse_args()
    old_lo = args["old_lo"]
    new_lo = args["new_lo"]
    new_tag = args["new_tag"]
    new_display = args["new_display"]

    if not old_lo or not new_lo:
        print("用法: python scripts/migrate_player_account.py --old-lo <旧Lo> --new-lo <新Lo> [--new-tag <新Tag>] [--new-display <显示名>]")
        sys.exit(1)

    if new_tag and not new_display:
        new_display = new_tag.split("#")[0] if "#" in new_tag else new_tag

    from pymongo import MongoClient
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    # accountIdLo 可能存储为 int 或 string
    old_lo_types = [old_lo]
    if old_lo.isdigit():
        old_lo_types.append(int(old_lo))

    stats = {"league_matches": 0, "tournament_groups": 0}

    print(f"{'='*50}")
    print(f"  选手账号迁移 {'[DRY-RUN]' if DRY_RUN else '[APPLY]'}")
    print(f"{'='*50}")
    print(f"  旧 accountIdLo: {old_lo}")
    print(f"  新 accountIdLo: {new_lo}")
    if new_tag:
        print(f"  新 BattleTag:   {new_tag}")
    if new_display:
        print(f"  新显示名:       {new_display}")
    print()

    # 1. league_matches: players 中匹配旧 Lo 的条目
    set_fields = {"players.$.accountIdLo": new_lo}
    if new_tag:
        set_fields["players.$.battleTag"] = new_tag
    if new_display:
        set_fields["players.$.displayName"] = new_display

    for m in db.league_matches.find({"players.accountIdLo": {"$in": old_lo_types}}):
        mid = _id(m)
        for i, p in enumerate(m.get("players", [])):
            if str(p.get("accountIdLo", "")) == old_lo or p.get("accountIdLo") == old_lo:
                old_name = p.get("displayName") or p.get("battleTag") or "?"
                new_name = new_display or new_tag or new_lo
                print(f"  league_matches {mid} player[{i}]: {old_name} → {new_name}")
                stats["league_matches"] += 1
                if not DRY_RUN:
                    update = {"$set": {}}
                    update["$set"][f"players.{i}.accountIdLo"] = new_lo
                    if new_tag:
                        update["$set"][f"players.{i}.battleTag"] = new_tag
                    if new_display:
                        update["$set"][f"players.{i}.displayName"] = new_display
                    db.league_matches.update_one({"_id": m["_id"]}, update)

    # 2. tournament_groups: players 中匹配旧 Lo 的条目
    for g in db.tournament_groups.find({"players.accountIdLo": {"$in": old_lo_types}}):
        gid = _id(g)
        for i, p in enumerate(g.get("players", [])):
            if str(p.get("accountIdLo", "")) == old_lo or p.get("accountIdLo") == old_lo:
                old_name = p.get("displayName") or p.get("battleTag") or "?"
                new_name = new_display or new_tag or new_lo
                print(f"  tournament_groups {gid} player[{i}]: {old_name} → {new_name}")
                stats["tournament_groups"] += 1
                if not DRY_RUN:
                    update = {"$set": {}}
                    update["$set"][f"players.{i}.accountIdLo"] = new_lo
                    if new_tag:
                        update["$set"][f"players.{i}.battleTag"] = new_tag
                    if new_display:
                        update["$set"][f"players.{i}.displayName"] = new_display
                    db.tournament_groups.update_one({"_id": g["_id"]}, update)

    print()
    print("=" * 50)
    print(f"{'[DRY-RUN] ' if DRY_RUN else ''}迁移完成:")
    print(f"  league_matches:     {stats['league_matches']} 个玩家条目")
    print(f"  tournament_groups:  {stats['tournament_groups']} 个玩家条目")
    if DRY_RUN:
        print()
        print("这是 dry-run，没有实际写入。加 --apply 参数执行写入。")


if __name__ == "__main__":
    main()
