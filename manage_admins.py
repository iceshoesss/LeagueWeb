#!/usr/bin/env python3
"""
管理员管理脚本（服务端使用，不要暴露到公网）

用法：
  python manage_admins.py list                    # 查看所有管理员
  python manage_admins.py add "某人#1234"          # 添加管理员
  python manage_admins.py add "某人#1234" --super  # 添加超级管理员
  python manage_admins.py remove "某人#1234"       # 移除管理员
  python manage_admins.py promote "某人#1234"      # 提升为超级管理员
  python manage_admins.py demote "某人#1234"       # 降级为普通管理员
"""

import sys
import os
from datetime import datetime, timezone

from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")


def get_db():
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return client[DB_NAME]


def list_admins():
    db = get_db()
    admins = list(db.league_admins.find().sort("isSuperAdmin", -1).sort("addedAt", 1))
    if not admins:
        print("暂无管理员")
        return
    print(f"{'BattleTag':<30} {'角色':<14} {'添加时间':<22} {'添加者'}")
    print("-" * 85)
    for a in admins:
        added_at = a.get("addedAt", "")
        if isinstance(added_at, datetime):
            added_at = added_at.strftime("%Y-%m-%d %H:%M:%S")
        added_by = a.get("addedBy", "")
        role = "超级管理员" if a.get("isSuperAdmin") else "管理员"
        print(f"{a['battleTag']:<30} {role:<14} {added_at:<22} {added_by}")


def add_admin(battle_tag, added_by="cli", is_super=False):
    db = get_db()
    if db.league_admins.count_documents({"battleTag": battle_tag}) > 0:
        print(f"⚠️  {battle_tag} 已是管理员")
        return
    db.league_admins.insert_one({
        "battleTag": battle_tag,
        "addedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "addedBy": added_by,
        "isSuperAdmin": is_super,
    })
    role = "超级管理员" if is_super else "管理员"
    print(f"✅ 已添加{role}：{battle_tag}")


def remove_admin(battle_tag):
    db = get_db()
    target = db.league_admins.find_one({"battleTag": battle_tag})
    if not target:
        print(f"⚠️  {battle_tag} 不是管理员")
        return
    if target.get("isSuperAdmin"):
        # 检查是否还有其他超级管理员
        super_count = db.league_admins.count_documents({"isSuperAdmin": True})
        if super_count <= 1:
            print(f"⚠️  不能移除最后一个超级管理员")
            return
    db.league_admins.delete_one({"battleTag": battle_tag})
    print(f"✅ 已移除管理员：{battle_tag}")


def promote_admin(battle_tag):
    db = get_db()
    target = db.league_admins.find_one({"battleTag": battle_tag})
    if not target:
        print(f"⚠️  {battle_tag} 不是管理员")
        return
    if target.get("isSuperAdmin"):
        print(f"⚠️  {battle_tag} 已是超级管理员")
        return
    db.league_admins.update_one({"battleTag": battle_tag}, {"$set": {"isSuperAdmin": True}})
    print(f"✅ 已提升为超级管理员：{battle_tag}")


def demote_admin(battle_tag):
    db = get_db()
    target = db.league_admins.find_one({"battleTag": battle_tag})
    if not target:
        print(f"⚠️  {battle_tag} 不是管理员")
        return
    if not target.get("isSuperAdmin"):
        print(f"⚠️  {battle_tag} 不是超级管理员")
        return
    super_count = db.league_admins.count_documents({"isSuperAdmin": True})
    if super_count <= 1:
        print(f"⚠️  不能降级最后一个超级管理员")
        return
    db.league_admins.update_one({"battleTag": battle_tag}, {"$set": {"isSuperAdmin": False}})
    print(f"✅ 已降级为普通管理员：{battle_tag}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        list_admins()
    elif cmd == "add" and len(sys.argv) >= 3:
        is_super = "--super" in sys.argv
        add_admin(sys.argv[2], is_super=is_super)
    elif cmd == "remove" and len(sys.argv) >= 3:
        remove_admin(sys.argv[2])
    elif cmd == "promote" and len(sys.argv) >= 3:
        promote_admin(sys.argv[2])
    elif cmd == "demote" and len(sys.argv) >= 3:
        demote_admin(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
