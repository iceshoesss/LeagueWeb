#!/usr/bin/env python3
"""导出所有已归档赛事到 archive-site/data/（静态归档站）

用法: python scripts/export_archived.py

环境变量:
  MONGO_URL  MongoDB 地址 (默认 mongodb://mongo:27017)
  DB_NAME    数据库名 (默认 hearthstone)
"""

import json
import os
import re
import sys
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")


def sanitize_filename(name):
    return re.sub(r'[^\w\u4e00-\u9fff\-]', '_', name).strip('_')


def main():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    archive_dir = os.path.join(os.path.dirname(__file__), '..', 'archive-site', 'data')
    os.makedirs(archive_dir, exist_ok=True)

    archived = list(db.tournaments.find(
        {"status": "archived", "bracketData": {"$exists": True}},
        {"bracketData": 1, "name": 1, "archivedAt": 1}
    ))

    if not archived:
        print("没有找到已归档赛事")
        return

    index_data = {"tournaments": []}

    for t in archived:
        name = t["name"]
        filename = sanitize_filename(name)
        bracket_data = t["bracketData"]

        # 写赛事 JSON
        filepath = os.path.join(archive_dir, filename + '.json')
        export_data = {"tournaments": bracket_data}
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        print(f"  ✓ {name} → {filename}.json")

        # index 条目
        round_meta = []
        for bd in bracket_data:
            for r in bd.get("rounds", []):
                round_meta.append({
                    "label": r.get("label", ""),
                    "groupCount": len(r.get("groups", []))
                })

        index_data["tournaments"].append({
            "name": name,
            "filename": filename,
            "rounds": round_meta,
            "archivedAt": t.get("archivedAt", "")
        })

    # 写 index.json
    index_path = os.path.join(archive_dir, 'index.json')
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)
    print(f"\n  ✓ index.json（{len(archived)} 个赛事）")

    client.close()
    print(f"\n完成！文件在 archive-site/data/ 目录")


if __name__ == '__main__':
    main()
