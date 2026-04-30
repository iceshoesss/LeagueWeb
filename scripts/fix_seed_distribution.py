#!/usr/bin/env python3
"""
重新分配淘汰赛分组，保证每组恰好 1 个种子选手。

用法:
  python scripts/fix_seed_distribution.py <赛事名称> [--seed 洗牌种子] [--dry-run]

示例:
  python scripts/fix_seed_distribution.py "2026 春季赛" --dry-run
  python scripts/fix_seed_distribution.py "2026 春季赛"
  python scripts/fix_seed_distribution.py "2026 春季赛" --seed "自定义种子"
"""

import os
import sys
import hashlib
import struct

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")


def make_rng(s):
    state = [s & 0xFFFFFFFF]
    def next_int(max_val):
        x = state[0]
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= (x >> 17)
        x ^= (x << 5) & 0xFFFFFFFF
        state[0] = x & 0xFFFFFFFF
        return x % max_val
    return next_int


def deterministic_shuffle(arr, seed_str):
    h = hashlib.sha256(seed_str.encode("utf-8")).digest()
    seed_int = sum(struct.unpack_from("<I", h, i) for i in range(0, 32, 4))
    rng = make_rng(seed_int)
    result = list(arr)
    for i in range(len(result) - 1, 0, -1):
        j = rng(i + 1)
        result[i], result[j] = result[j], result[i]
    return result


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/fix_seed_distribution.py <赛事名称> [--seed 洗牌种子] [--dry-run]")
        sys.exit(1)

    tournament = sys.argv[1]
    dry_run = "--dry-run" in sys.argv
    seed_str = tournament
    if "--seed" in sys.argv:
        idx = sys.argv.index("--seed")
        if idx + 1 < len(sys.argv):
            seed_str = sys.argv[idx + 1]

    from pymongo import MongoClient
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    # 1. 获取所有分组
    groups = list(db.tournament_groups.find({"tournamentName": tournament}))
    if not groups:
        print(f"❌ 赛事「{tournament}」没有分组")
        sys.exit(1)

    print(f"📋 赛事「{tournament}」共 {len(groups)} 组")

    # 2. 获取种子选手 accountIdLo 集合
    seed_players_db = list(db.league_players.find({"isSeed": True}))
    seed_lo_set = {str(s.get("accountIdLo", "")) for s in seed_players_db if s.get("accountIdLo")}
    print(f"🌱 数据库中种子选手: {len(seed_lo_set)} 人")

    # 3. 收集所有选手，区分种子和晋级者
    all_seeds = []
    all_qualifiers = []
    seen_los = set()

    for g in groups:
        for p in g.get("players", []):
            lo = str(p.get("accountIdLo", ""))
            if not lo or lo in seen_los:
                continue
            seen_los.add(lo)
            if lo in seed_lo_set:
                all_seeds.append(p)
            else:
                all_qualifiers.append(p)

    print(f"   种子选手: {len(all_seeds)} 人")
    print(f"   晋级选手: {len(all_qualifiers)} 人")
    group_count = len(groups)

    if len(all_seeds) < group_count:
        print(f"❌ 种子选手 ({len(all_seeds)}) 少于分组数 ({group_count})，无法保证每组 1 种子")
        sys.exit(1)

    # 4. 分别洗牌
    shuffled_seeds = deterministic_shuffle(all_seeds, f"{seed_str}:seeds")
    shuffled_qualifiers = deterministic_shuffle(all_qualifiers, f"{seed_str}:qualifiers")

    # 5. 重新分配：每组 1 种子 + 7 晋级者
    new_groups = []
    seed_idx = 0
    qual_idx = 0

    for gi in range(group_count):
        players = []
        if seed_idx < len(shuffled_seeds):
            players.append(shuffled_seeds[seed_idx])
            seed_idx += 1
        for _ in range(7):
            if qual_idx < len(shuffled_qualifiers):
                players.append(shuffled_qualifiers[qual_idx])
                qual_idx += 1
        new_groups.append(players)

    # 剩余选手放到最后一组
    while seed_idx < len(shuffled_seeds):
        new_groups[-1].append(shuffled_seeds[seed_idx])
        seed_idx += 1
    while qual_idx < len(shuffled_qualifiers):
        new_groups[-1].append(shuffled_qualifiers[qual_idx])
        qual_idx += 1

    # 6. 打印分配结果
    print(f"\n📊 重新分配结果:")
    for gi, players in enumerate(new_groups):
        seed_count = sum(1 for p in players if str(p.get("accountIdLo", "")) in seed_lo_set)
        names = [p.get("displayName", "?") for p in players]
        print(f"   第 {gi+1} 组 ({len(players)} 人, 种子 {seed_count}): {', '.join(names)}")

    if dry_run:
        print("\n⚠️  dry-run 模式，未写入数据库")
        return

    # 7. 写入数据库
    confirm = input(f"\n确认更新 {len(groups)} 个分组？(y/N): ").strip().lower()
    if confirm != "y":
        print("已取消")
        return

    from bson import ObjectId
    for gi, group in enumerate(groups):
        if gi < len(new_groups):
            db.tournament_groups.update_one(
                {"_id": group["_id"]},
                {"$set": {"players": new_groups[gi]}}
            )

    print(f"\n✅ 已更新 {len(groups)} 个分组")


if __name__ == "__main__":
    main()
