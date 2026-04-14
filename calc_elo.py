#!/usr/bin/env python3
"""
ELO 评分计算脚本

从 league_matches 读取所有已完成对局，按时间顺序计算 ELO，
结果写入 league_players.elo 字段。

用法：
  python calc_elo.py           # 计算并写入
  python calc_elo.py --dry-run # 只计算不写入
"""

import sys
import os
from pymongo import MongoClient
from datetime import datetime, timezone

# ── 配置 ──────────────────────────────────────────
INITIAL_ELO = 50
K_FACTOR = 2
SCALE_FACTOR = 400

# ── 数据库连接 ────────────────────────────────────
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")


def expected_score(rating_a, rating_b):
    """A 对 B 的预期胜率"""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / SCALE_FACTOR))


def calculate_elo(matches):
    """
    按时间顺序遍历所有对局，计算 ELO。
    返回 {battleTag: elo} 字典。
    """
    ratings = {}  # battleTag → elo

    for match in matches:
        players = match.get("players", [])
        # 过滤掉没有 placement 的玩家（掉线等）
        valid_players = [p for p in players if p.get("placement") is not None]
        if len(valid_players) < 2:
            continue

        # 初始化新玩家
        for p in valid_players:
            tag = p.get("battleTag", "")
            if tag and tag not in ratings:
                ratings[tag] = INITIAL_ELO

        # 逐对计算 ELO 变动
        deltas = {p.get("battleTag", ""): 0.0 for p in valid_players}

        for i in range(len(valid_players)):
            for j in range(i + 1, len(valid_players)):
                a = valid_players[i]
                b = valid_players[j]
                tag_a = a.get("battleTag", "")
                tag_b = b.get("battleTag", "")

                r_a = ratings.get(tag_a, INITIAL_ELO)
                r_b = ratings.get(tag_b, INITIAL_ELO)

                e_a = expected_score(r_a, r_b)

                # 排名数字越小越好（1 = 第一）
                p_a = a.get("placement", 4)
                p_b = b.get("placement", 4)

                if p_a < p_b:
                    s_a = 1.0
                elif p_a > p_b:
                    s_a = 0.0
                else:
                    s_a = 0.5  # 平局（罕见）

                s_b = 1.0 - s_a

                deltas[tag_a] += K_FACTOR * (s_a - e_a)
                deltas[tag_b] += K_FACTOR * (s_b - (1.0 - e_a))

        # 应用变动
        for tag, delta in deltas.items():
            if tag in ratings:
                ratings[tag] += delta

    return ratings


def main():
    dry_run = "--dry-run" in sys.argv

    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    # 读取所有已完成对局，按时间排序
    matches = list(db.league_matches.find(
        {"endedAt": {"$ne": None}},
        {"players.battleTag": 1, "players.placement": 1, "endedAt": 1}
    ).sort("endedAt", 1))

    print(f"共 {len(matches)} 局已完成对局")

    if not matches:
        print("无对局数据，退出")
        return

    # 计算 ELO
    ratings = calculate_elo(matches)

    # 按分数排序展示
    sorted_ratings = sorted(ratings.items(), key=lambda x: x[1], reverse=True)

    print(f"\n{'排名':<5} {'玩家':<30} {'ELO':>8}")
    print("-" * 45)
    for i, (tag, elo) in enumerate(sorted_ratings, 1):
        print(f"{i:<5} {tag:<30} {elo:>8.1f}")

    if dry_run:
        print("\n--dry-run 模式，未写入数据库")
        return

    # 写入 league_players
    updated = 0
    for tag, elo in ratings.items():
        result = db.league_players.update_one(
            {"battleTag": tag},
            {"$set": {"elo": round(elo, 1)}},
            upsert=False
        )
        if result.modified_count > 0:
            updated += 1

    # 对于不在 league_players 但在 matches 中的玩家，也写入
    for tag, elo in ratings.items():
        existing = db.league_players.find_one({"battleTag": tag})
        if not existing:
            db.league_players.insert_one({
                "battleTag": tag,
                "displayName": tag.split("#")[0] if "#" in tag else tag,
                "elo": round(elo, 1),
                "verified": False,
            })
            updated += 1

    print(f"\n已更新 {updated} 名玩家的 ELO")


if __name__ == "__main__":
    main()
