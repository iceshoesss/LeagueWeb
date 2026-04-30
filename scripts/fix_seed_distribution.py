#!/usr/bin/env python3
"""
重新分配淘汰赛分组，保证每组恰好 1 个种子选手。

用法：
  python scripts/fix_seed_distribution.py "赛事名称"

可选参数：
  --seed "自定义seed"   指定洗牌种子（默认用赛事名称）
  --dry-run             只打印不写入
"""

import sys
import os
import hashlib
import struct
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


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
    parser = argparse.ArgumentParser(description="重新分配分组，每组 1 种子 + 7 晋级者")
    parser.add_argument("tournament", help="赛事名称")
    parser.add_argument("--seed", help="洗牌种子（默认=赛事名称）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写入")
    args = parser.parse_args()

    tournament = args.tournament
    seed_str = args.seed or tournament
    db = get_db()

    # 1. 获取所有分组
    groups = list(db.tournament_groups.find({"tournamentName": tournament}))
    if not groups:
        print(f"❌ 赛事「{tournament}」没有分组")
        return

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
    total = len(all_seeds) + len(all_qualifiers)
    group_count = len(groups)

    if len(all_seeds) < group_count:
        print(f"❌ 种子选手 ({len(all_seeds)}) 少于分组数 ({group_count})，无法保证每组 1 种子")
        return

    # 4. 分别洗牌
    shuffled_seeds = deterministic_shuffle(all_seeds, f"{seed_str}:seeds")
    shuffled_qualifiers = deterministic_shuffle(all_qualifiers, f"{seed_str}:qualifiers")

    # 5. 重新分配：每组 1 种子 + 7 晋级者
    new_groups = []
    seed_idx = 0
    qual_idx = 0

    for gi in range(group_count):
        players = []
        # 1 个种子
        if seed_idx < len(shuffled_seeds):
            players.append(shuffled_seeds[seed_idx])
            seed_idx += 1
        # 7 个晋级者
        for _ in range(7):
            if qual_idx < len(shuffled_qualifiers):
                players.append(shuffled_qualifiers[qual_idx])
                qual_idx += 1
        new_groups.append(players)

    # 剩余选手（如果有）放到最后一组
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

    if args.dry_run:
        print("\n⚠️  dry-run 模式，未写入数据库")
        return

    # 7. 写入数据库
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
