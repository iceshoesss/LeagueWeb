"""
MongoDB 集合重命名迁移脚本
bg_ratings → player_records

用法:
  python migrate_rename_collection.py

环境变量:
  MONGO_URL — MongoDB 地址（默认 mongodb://localhost:27017）
  DB_NAME   — 数据库名（默认 hearthstone）

功能:
  1. 检查旧集合是否存在
  2. 检查新集合是否已存在（防止重复执行）
  3. 执行 renameCollection
  4. 验证结果
"""

import os
import sys
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")
OLD_NAME = "bg_ratings"
NEW_NAME = "player_records"


def main():
    print(f"连接 MongoDB: {MONGO_URL}")
    print(f"数据库: {DB_NAME}")
    print()

    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)

    # 测试连接
    try:
        client.admin.command("ping")
    except Exception as e:
        print(f"❌ 无法连接 MongoDB: {e}")
        sys.exit(1)

    db = client[DB_NAME]
    collections = db.list_collection_names()

    # 检查旧集合
    if OLD_NAME not in collections:
        print(f"ℹ️  旧集合 '{OLD_NAME}' 不存在，可能已经迁移过了")
        if NEW_NAME in collections:
            count = db[NEW_NAME].count_documents({})
            print(f"✅ 新集合 '{NEW_NAME}' 已存在，共 {count} 条记录")
        sys.exit(0)

    old_count = db[OLD_NAME].count_documents({})
    print(f"📦 旧集合 '{OLD_NAME}': {old_count} 条记录")

    # 检查新集合是否已存在
    if NEW_NAME in collections:
        new_count = db[NEW_NAME].count_documents({})
        print(f"⚠️  新集合 '{NEW_NAME}' 已存在，共 {new_count} 条记录")
        print("   为避免数据冲突，请手动确认后处理")
        sys.exit(1)

    # 执行重命名
    print(f"🔄 正在重命名: {OLD_NAME} → {NEW_NAME} ...")
    try:
        db[OLD_NAME].rename(NEW_NAME)
    except Exception as e:
        print(f"❌ 重命名失败: {e}")
        sys.exit(1)

    # 验证
    new_count = db[NEW_NAME].count_documents({})
    print(f"✅ 重命名成功! 新集合 '{NEW_NAME}': {new_count} 条记录")

    if new_count != old_count:
        print(f"⚠️  记录数不一致: 旧={old_count}, 新={new_count}")
    else:
        print("   记录数一致，迁移完成")


if __name__ == "__main__":
    main()
