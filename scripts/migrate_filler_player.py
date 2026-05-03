#!/usr/bin/env python3
"""
迁移脚本：将补位玩家的最近 N 局替换为公用替补账号。
用于修正补位局污染真实玩家数据的问题。

仅修改 league_matches 和 tournament_groups 中的玩家标识，
不改动 player_records / league_players / 队列等。

用法：
  python scripts/migrate_filler_player.py              # dry-run（只打印，不写入）
  python scripts/migrate_filler_player.py --apply      # 实际执行写入

  python scripts/migrate_filler_player.py \
    --tag "补位玩家#1234" --count 3

参数说明：
  --tag          补位玩家的 BattleTag（必填，自动查 accountIdLo）
  --lo           补位玩家的 accountIdLo（可选，不传则从 league_players 查）
  --count        要替换的最近局数（必填）
  --sub-tag      替补账号 BattleTag（默认 无耻之替补#1234）
  --sub-lo       替补账号 accountIdLo（可选，自动查）
  --sub-display  替补账号显示名（默认取 sub-tag # 前部分）
  --apply        实际执行写入（默认 dry-run）
"""

import os
import sys

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")

DRY_RUN = "--apply" not in sys.argv

DEFAULT_SUB_TAG = "无耻之替补#1234"


def parse_args():
    args = {
        "tag": None, "lo": None, "count": None,
        "sub_tag": None, "sub_lo": None, "sub_display": None,
    }
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i] in ("--apply", "--dry-run"):
            i += 1
            continue
        if argv[i] == "--tag" and i + 1 < len(argv):
            args["tag"] = argv[i + 1].strip()
            i += 2
        elif argv[i] == "--lo" and i + 1 < len(argv):
            args["lo"] = argv[i + 1].strip()
            i += 2
        elif argv[i] == "--count" and i + 1 < len(argv):
            args["count"] = int(argv[i + 1].strip())
            i += 2
        elif argv[i] == "--sub-tag" and i + 1 < len(argv):
            args["sub_tag"] = argv[i + 1].strip()
            i += 2
        elif argv[i] == "--sub-lo" and i + 1 < len(argv):
            args["sub_lo"] = argv[i + 1].strip()
            i += 2
        elif argv[i] == "--sub-display" and i + 1 < len(argv):
            args["sub_display"] = argv[i + 1].strip()
            i += 2
        else:
            print(f"未知参数: {argv[i]}")
            sys.exit(1)
    return args


def lookup_account_id(db, battle_tag, label):
    """从 league_players 查 accountIdLo"""
    lp = db.league_players.find_one({"battleTag": battle_tag})
    if lp and lp.get("accountIdLo"):
        lo = str(lp["accountIdLo"])
        print(f"  {label} accountIdLo: {lo}（从 league_players 自动查到）")
        return lo
    print(f"  ⚠️  league_players 中未找到 {battle_tag}，无法自动查 accountIdLo")
    return None


def _id(doc):
    return str(doc.get("_id", "?"))


def main():
    args = parse_args()
    tag = args["tag"]
    lo = args["lo"]
    count = args["count"]
    sub_tag = args["sub_tag"] or DEFAULT_SUB_TAG
    sub_lo = args["sub_lo"]
    sub_display = args["sub_display"]

    if not tag:
        print("用法: python scripts/migrate_filler_player.py --tag <补位玩家Tag> --count <局数>")
        sys.exit(1)
    if not count or count <= 0:
        print("❌ --count 必须为正整数")
        sys.exit(1)

    from pymongo import MongoClient
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    # 自动查 accountIdLo
    if not lo:
        lo = lookup_account_id(db, tag, "补位玩家")
    if not sub_lo:
        sub_lo = lookup_account_id(db, sub_tag, "替补账号")

    if not lo:
        print("\n❌ 无法确定补位玩家的 accountIdLo，请用 --lo 手动指定")
        sys.exit(1)
    if not sub_lo:
        print(f"\n❌ 替补账号 {sub_tag} 未注册，请先用插件打一局或用 --sub-lo 手动指定")
        sys.exit(1)

    if not sub_display:
        sub_display = sub_tag.split("#")[0] if "#" in sub_tag else sub_tag

    # accountIdLo 可能存储为 int 或 string
    lo_types = [lo]
    if lo.isdigit():
        lo_types.append(int(lo))

    # 查该玩家所有联赛对局，按时间倒序
    matches = list(db.league_matches.find(
        {"players.accountIdLo": {"$in": lo_types}, "endedAt": {"$ne": None}},
        {"gameUuid": 1, "endedAt": 1, "startedAt": 1, "players": 1, "tournamentGroupId": 1}
    ).sort("endedAt", -1))

    if not matches:
        print(f"\n❌ 未找到 {tag} 的联赛对局记录")
        sys.exit(1)

    print(f"{'='*60}")
    print(f"  补位玩家迁移 {'[DRY-RUN]' if DRY_RUN else '[APPLY]'}")
    print(f"{'='*60}")
    print(f"  补位玩家: {tag} (Lo: {lo})")
    print(f"  替补账号: {sub_tag} (Lo: {sub_lo})")
    print(f"  替换最近: {count} 局")
    print(f"  共有对局: {len(matches)} 局")
    print()

    # 展示全部对局，标记要替换的
    print("  对局列表（按时间倒序）：")
    for idx, m in enumerate(matches):
        marker = " ← 要替换" if idx < count else ""
        ended = m.get("endedAt", m.get("startedAt", "?"))
        # 找该玩家在这局的排名和积分
        player_info = None
        for p in m.get("players", []):
            if str(p.get("accountIdLo", "")) == lo or p.get("accountIdLo") == lo:
                player_info = p
                break
        placement = player_info.get("placement", "?") if player_info else "?"
        points = player_info.get("points", "?") if player_info else "?"
        is_tournament = "淘汰赛" if m.get("tournamentGroupId") else "积分赛"
        print(f"    [{idx+1}] {ended}  排名={placement}  积分={points}  {is_tournament}{marker}")

    if count > len(matches):
        print(f"\n⚠️  只有 {len(matches)} 局，将全部替换")
        count = len(matches)

    print()

    # 筛选要替换的对局
    target_matches = matches[:count]
    stats = {"league_matches": 0, "tournament_groups": 0}

    # 1. league_matches
    for m in target_matches:
        mid = _id(m)
        for i, p in enumerate(m.get("players", [])):
            if str(p.get("accountIdLo", "")) == lo or p.get("accountIdLo") == lo:
                old_name = p.get("displayName") or p.get("battleTag") or "?"
                print(f"  league_matches {mid} player[{i}]: {old_name} → {sub_display}")
                stats["league_matches"] += 1
                if not DRY_RUN:
                    update = {"$set": {
                        f"players.{i}.accountIdLo": sub_lo,
                        f"players.{i}.battleTag": sub_tag,
                        f"players.{i}.displayName": sub_display,
                    }}
                    db.league_matches.update_one({"_id": m["_id"]}, update)

    # 2. tournament_groups — 只处理这些对局关联的淘汰赛组
    tg_ids = set()
    for m in target_matches:
        if m.get("tournamentGroupId"):
            tg_ids.add(m["tournamentGroupId"])

    for tg_id in tg_ids:
        g = db.tournament_groups.find_one({"_id": tg_id})
        if not g:
            continue
        gid = _id(g)
        for i, p in enumerate(g.get("players", [])):
            if str(p.get("accountIdLo", "")) == lo or p.get("accountIdLo") == lo:
                old_name = p.get("displayName") or p.get("battleTag") or "?"
                print(f"  tournament_groups {gid} player[{i}]: {old_name} → {sub_display}")
                stats["tournament_groups"] += 1
                if not DRY_RUN:
                    update = {"$set": {
                        f"players.{i}.accountIdLo": sub_lo,
                        f"players.{i}.battleTag": sub_tag,
                        f"players.{i}.displayName": sub_display,
                    }}
                    db.tournament_groups.update_one({"_id": g["_id"]}, update)

    print()
    print("=" * 60)
    print(f"{'[DRY-RUN] ' if DRY_RUN else ''}迁移完成:")
    print(f"  league_matches:     {stats['league_matches']} 个玩家条目")
    print(f"  tournament_groups:  {stats['tournament_groups']} 个玩家条目")
    if DRY_RUN:
        print()
        print("这是 dry-run，没有实际写入。加 --apply 参数执行写入。")
    else:
        print()
        print("💡 提醒：如涉及淘汰赛组，建议再跑 python scripts/migrate_rankings.py --force 重算排名")


if __name__ == "__main__":
    main()
