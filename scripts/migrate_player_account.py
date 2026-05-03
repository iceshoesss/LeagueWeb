#!/usr/bin/env python3
"""
选手账号迁移脚本
用途：比赛进行中选手更换账号，将旧账号数据迁移到新账号

用法：
  # 1. 先 dry-run 检查影响范围
  python scripts/migrate_player_account.py \
    --old-tag "旧名字#1234" --old-lo "11111111" \
    --new-tag "新名字#5678" --new-lo "99999999" \
    --dry-run

  # 2. 确认无误后执行
  python scripts/migrate_player_account.py \
    --old-tag "旧名字#1234" --old-lo "11111111" \
    --new-tag "新名字#5678" --new-lo "99999999"

环境变量：
  MONGO_URL  MongoDB 连接地址（默认 mongodb://localhost:27017）
  DB_NAME    数据库名（默认 hearthstone）
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import pymongo


def parse_args():
    parser = argparse.ArgumentParser(description="选手账号迁移")
    parser.add_argument("--old-tag", required=True, help="旧 BattleTag（如 旧名字#1234）")
    parser.add_argument("--old-lo", required=True, help="旧 accountIdLo")
    parser.add_argument("--new-tag", required=True, help="新 BattleTag（如 新名字#5678）")
    parser.add_argument("--new-lo", required=True, help="新 accountIdLo")
    parser.add_argument("--new-display", default=None, help="新显示名（默认取新 BattleTag # 前部分）")
    parser.add_argument("--dry-run", action="store_true", help="只检查不写入")
    parser.add_argument("--yes", action="store_true", help="跳过确认提示")
    return parser.parse_args()


def get_display_name(tag):
    """从 BattleTag 提取显示名（# 前部分）"""
    return tag.split("#")[0] if "#" in tag else tag


def now_utc():
    return datetime.now(timezone.utc)


def collect_changes(db, old_tag, old_lo, new_tag, new_lo, new_display):
    """扫描所有集合，返回变更列表"""
    changes = []

    # accountIdLo 可能存储为 int 或 string
    old_lo_types = [old_lo, int(old_lo)] if old_lo.isdigit() else [old_lo]

    # 1. player_records — key: playerId (= battleTag)
    rec = db.player_records.find_one({"playerId": old_tag})
    if rec:
        changes.append(("player_records", f"playerId={old_tag}", {
            "$set": {
                "playerId": new_tag,
                "accountIdLo": new_lo,
            }
        }))

    # 2. league_players — key: battleTag
    lp = db.league_players.find_one({"battleTag": old_tag})
    if lp:
        changes.append(("league_players", f"battleTag={old_tag}", {
            "$set": {
                "battleTag": new_tag,
                "accountIdLo": new_lo,
                "displayName": new_display,
            }
        }))

    # 3. league_matches — players 数组内嵌
    match_count = db.league_matches.count_documents({"players.accountIdLo": {"$in": old_lo_types}})
    if match_count > 0:
        changes.append(("league_matches", f"players.accountIdLo={old_lo} ({match_count}局)", "array_update"))

    # 4. league_queue — name 字段 = battleTag
    q = db.league_queue.find_one({"name": old_tag})
    if q:
        changes.append(("league_queue", f"name={old_tag}", {
            "$set": {"name": new_tag}
        }))

    # 5. league_waiting_queue — players[].name + players[].accountIdLo
    wq_count = db.league_waiting_queue.count_documents({"players.accountIdLo": {"$in": old_lo_types}})
    if wq_count > 0:
        changes.append(("league_waiting_queue", f"players.accountIdLo={old_lo} ({wq_count}组)", "array_update"))

    # 6. tournament_groups — players 数组内嵌
    tg_count = db.tournament_groups.count_documents({"players.accountIdLo": {"$in": old_lo_types}})
    if tg_count > 0:
        changes.append(("tournament_groups", f"players.accountIdLo={old_lo} ({tg_count}组)", "array_update"))

    # 7. tournament_enrollments — key: battleTag
    te = db.tournament_enrollments.find_one({"battleTag": old_tag})
    if te:
        changes.append(("tournament_enrollments", f"battleTag={old_tag}", {
            "$set": {
                "battleTag": new_tag,
                "accountIdLo": new_lo,
                "displayName": new_display,
            }
        }))

    # 8. league_admins — battleTag 字段
    admin = db.league_admins.find_one({"battleTag": old_tag})
    if admin:
        changes.append(("league_admins", f"battleTag={old_tag}", {
            "$set": {"battleTag": new_tag}
        }))

    return changes


def apply_changes(db, old_tag, old_lo, new_tag, new_lo, new_display):
    """执行迁移"""
    results = {}

    # 1. player_records — 唯一索引 playerId，需处理冲突
    rec = db.player_records.find_one({"playerId": old_tag})
    if rec:
        existing_new = db.player_records.find_one({"playerId": new_tag})
        if existing_new:
            # 合并：保留旧记录的 verificationCode 等字段，更新到新记录
            merge_fields = {}
            for k in ["verificationCode", "rating", "region", "mode"]:
                if rec.get(k) and not existing_new.get(k):
                    merge_fields[k] = rec[k]
            merge_fields["migratedFrom"] = old_tag
            merge_fields["migratedAt"] = now_utc()
            if merge_fields:
                db.player_records.update_one({"playerId": new_tag}, {"$set": merge_fields})
            db.player_records.delete_one({"playerId": old_tag})
            results["player_records"] = f"merged into existing {new_tag}, deleted old"
        else:
            r = db.player_records.update_one(
                {"playerId": old_tag},
                {"$set": {
                    "playerId": new_tag,
                    "accountIdLo": new_lo,
                    "migratedFrom": old_tag,
                    "migratedAt": now_utc(),
                }}
            )
            results["player_records"] = f"updated {r.modified_count} doc"

    # 2. league_players — 唯一索引 battleTag，需处理冲突
    lp = db.league_players.find_one({"battleTag": old_tag})
    if lp:
        existing_new_lp = db.league_players.find_one({"battleTag": new_tag})
        if existing_new_lp:
            merge_fields = {}
            for k in ["lastSeen", "seed"]:
                if lp.get(k) and not existing_new_lp.get(k):
                    merge_fields[k] = lp[k]
            merge_fields["migratedFrom"] = old_tag
            merge_fields["migratedAt"] = now_utc()
            merge_fields["accountIdLo"] = new_lo
            merge_fields["displayName"] = new_display
            if merge_fields:
                db.league_players.update_one({"battleTag": new_tag}, {"$set": merge_fields})
            db.league_players.delete_one({"battleTag": old_tag})
            results["league_players"] = f"merged into existing {new_tag}, deleted old"
        else:
            r = db.league_players.update_one(
                {"battleTag": old_tag},
                {"$set": {
                    "battleTag": new_tag,
                    "accountIdLo": new_lo,
                    "displayName": new_display,
                    "migratedFrom": old_tag,
                    "migratedAt": now_utc(),
                }}
            )
            results["league_players"] = f"updated {r.modified_count} doc"

    # accountIdLo 可能存储为 int 或 string，两种都匹配
    old_lo_types = [old_lo, int(old_lo)] if old_lo.isdigit() else [old_lo]

    # 3. league_matches — 用 arrayFilters 批量更新
    r = db.league_matches.update_many(
        {"players.accountIdLo": {"$in": old_lo_types}},
        {"$set": {
            "players.$[elem].accountIdLo": new_lo,
            "players.$[elem].battleTag": new_tag,
            "players.$[elem].displayName": new_display,
        }},
        array_filters=[{"elem.accountIdLo": {"$in": old_lo_types}}]
    )
    results["league_matches"] = f"updated {r.modified_count}/{r.matched_count} docs"

    # 4. league_queue
    r = db.league_queue.update_many({"name": old_tag}, {"$set": {"name": new_tag}})
    results["league_queue"] = f"updated {r.modified_count} docs"

    # 5. league_waiting_queue
    r = db.league_waiting_queue.update_many(
        {"players.accountIdLo": {"$in": old_lo_types}},
        {"$set": {
            "players.$[elem].accountIdLo": new_lo,
            "players.$[elem].name": new_tag,
        }},
        array_filters=[{"elem.accountIdLo": {"$in": old_lo_types}}]
    )
    results["league_waiting_queue"] = f"updated {r.modified_count}/{r.matched_count} docs"

    # 6. tournament_groups
    r = db.tournament_groups.update_many(
        {"players.accountIdLo": {"$in": old_lo_types}},
        {"$set": {
            "players.$[elem].accountIdLo": new_lo,
            "players.$[elem].battleTag": new_tag,
            "players.$[elem].displayName": new_display,
        }},
        array_filters=[{"elem.accountIdLo": {"$in": old_lo_types}}]
    )
    results["tournament_groups"] = f"updated {r.modified_count}/{r.matched_count} docs"

    # 7. tournament_enrollments — 唯一索引 battleTag
    te = db.tournament_enrollments.find_one({"battleTag": old_tag})
    if te:
        existing_new_te = db.tournament_enrollments.find_one({"battleTag": new_tag})
        if existing_new_te:
            db.tournament_enrollments.delete_one({"battleTag": old_tag})
            results["tournament_enrollments"] = f"deleted old (new already exists)"
        else:
            r = db.tournament_enrollments.update_one(
                {"battleTag": old_tag},
                {"$set": {
                    "battleTag": new_tag,
                    "accountIdLo": new_lo,
                    "displayName": new_display,
                }}
            )
            results["tournament_enrollments"] = f"updated {r.modified_count} docs"

    # 8. league_admins
    r = db.league_admins.update_many(
        {"battleTag": old_tag},
        {"$set": {"battleTag": new_tag}}
    )
    results["league_admins"] = f"updated {r.modified_count} docs"

    return results


def main():
    args = parse_args()
    old_tag = args.old_tag.strip()
    old_lo = args.old_lo.strip()
    new_tag = args.new_tag.strip()
    new_lo = args.new_lo.strip()
    new_display = args.new_display or get_display_name(new_tag)

    # 校验
    if "#" not in old_tag or "#" not in new_tag:
        print("❌ BattleTag 格式错误，需包含 #（如 名字#1234）")
        sys.exit(1)

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "hearthstone")
    client = pymongo.MongoClient(mongo_url)
    db = client[db_name]

    print(f"{'='*60}")
    print(f"选手账号迁移 {'[DRY RUN]' if args.dry_run else '[LIVE]'}")
    print(f"{'='*60}")
    print(f"旧账号: {old_tag} (Lo: {old_lo})")
    print(f"新账号: {new_tag} (Lo: {new_lo})")
    print(f"显示名: {new_display}")
    print(f"数据库: {mongo_url}/{db_name}")
    print(f"{'='*60}")

    # 前置检查：新账号是否已存在于有唯一索引的集合
    existing_rec = db.player_records.find_one({"playerId": new_tag})
    existing_lp = db.league_players.find_one({"battleTag": new_tag})
    existing_te = db.tournament_enrollments.find_one({"battleTag": new_tag})
    has_conflict = False
    if existing_rec:
        print(f"⚠️  新账号已存在于 player_records: {new_tag}")
        print(f"   已有 accountIdLo: {existing_rec.get('accountIdLo')}")
        has_conflict = True
    if existing_lp:
        print(f"⚠️  新账号已存在于 league_players: {new_tag}")
        has_conflict = True
    if existing_te:
        print(f"⚠️  新账号已存在于 tournament_enrollments: {new_tag}")
        has_conflict = True
    if has_conflict and not args.dry_run:
        print(f"\n💡 建议：先手动处理冲突，或确认覆盖后加 --yes 执行")
        if not args.yes:
            resp = input("   继续覆盖？(y/N) ")
            if resp.lower() != "y":
                print("已取消")
                sys.exit(0)

    # 收集变更
    changes = collect_changes(db, old_tag, old_lo, new_tag, new_lo, new_display)
    if not changes:
        print("\n✅ 未找到旧账号数据，无需迁移")
        sys.exit(0)

    print(f"\n📋 影响范围（{len(changes)} 个集合）：\n")
    for coll, detail, op in changes:
        print(f"  • {coll}: {detail}")

    if args.dry_run:
        print(f"\n🔍 Dry run 完成，以上数据将在正式执行时被修改")
        print(f"   去掉 --dry-run 参数并加 --yes 确认执行")
        sys.exit(0)

    # 确认
    if not args.yes:
        resp = input(f"\n⚠️  确认执行迁移？此操作不可逆 (y/N) ")
        if resp.lower() != "y":
            print("已取消")
            sys.exit(0)

    # 执行
    print(f"\n🚀 开始迁移...\n")
    results = apply_changes(db, old_tag, old_lo, new_tag, new_lo, new_display)

    for coll, msg in results.items():
        print(f"  ✅ {coll}: {msg}")

    print(f"\n{'='*60}")
    print(f"✅ 迁移完成")
    print(f"{'='*60}")
    print(f"\n💡 提醒：")
    print(f"  • 已登录的旧 session 不会自动更新，需通知选手重新登录")
    print(f"  • 插件侧需使用新账号重新打一局以生成 player_records")
    print(f"  • 如需回滚，请用新旧账号反向执行本脚本")


if __name__ == "__main__":
    main()
