#!/usr/bin/env python3
"""
窝要烟牌 — 确定性随机洗牌验证工具

用法：
    python verify_shuffle.py
    python verify_shuffle.py --seed "种子" --players "1.txt" --groups 2

算法：SHA256(seed) → xorshift32 PRNG → Fisher-Yates shuffle
与服务端 /api/tournament/shuffle 结果完全一致。
"""

import hashlib
import struct
import sys
import argparse


def shuffle(players: list, seed: str) -> list:
    """确定性随机洗牌"""
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    seed_int = sum(struct.unpack_from("<I", h, i)[0] for i in range(0, 32, 4))

    # xorshift32
    state = seed_int & 0xFFFFFFFF

    def rng(max_val):
        nonlocal state
        x = state
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= (x >> 17)
        x ^= (x << 5) & 0xFFFFFFFF
        state = x & 0xFFFFFFFF
        return x % max_val

    # Fisher-Yates
    arr = list(players)
    for i in range(len(arr) - 1, 0, -1):
        j = rng(i + 1)
        arr[i], arr[j] = arr[j], arr[i]
    return arr


def main():
    parser = argparse.ArgumentParser(description="窝要烟牌 — 洗牌验证")
    parser.add_argument("--seed", default=None, help="随机种子")
    parser.add_argument("--players", default=None, help="选手列表文件（每行一个 battleTag）")
    parser.add_argument("--groups", type=int, default=2, help="分组数（默认 2）")
    args = parser.parse_args()

    seed = args.seed or input("请输入随机种子: ").strip()
    if not seed:
        print("错误：seed 不能为空")
        sys.exit(1)

    if args.players:
        with open(args.players, "r", encoding="utf-8") as f:
            players = [line.strip() for line in f if line.strip()]
    else:
        print("请输入选手列表（每行一个，空行结束）:")
        players = []
        while True:
            line = input().strip()
            if not line:
                break
            players.append(line)

    needed = args.groups * 8
    if len(players) < needed:
        print(f"错误：需要 {needed} 位选手（{args.groups} 组 × 8），当前 {len(players)} 位")
        sys.exit(1)

    players = players[:needed]

    # 显示 seed hash
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    print(f"\nSeed: \"{seed}\"")
    print(f"SHA256: {h[:16]}...")
    print(f"选手数: {len(players)}，分组数: {args.groups}\n")

    result = shuffle(players, seed)

    group_size = 8
    for g in range(args.groups):
        group = result[g * group_size:(g + 1) * group_size]
        print(f"{'ABCDEFGH'[g]} 组:")
        for i, p in enumerate(group):
            print(f"  {i+1}. {p}")
        print()


if __name__ == "__main__":
    main()
