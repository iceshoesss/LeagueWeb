"""
将 league_players 中所有已注册选手批量报名到 tournament_enrollments。
用法：python enroll_all.py
"""

from pymongo import MongoClient
from datetime import datetime, UTC
import os

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")
ENROLL_CAP = 1024

db = MongoClient(MONGO_URL)[DB_NAME]

# 已报名的 battleTag 集合
existing = {e["battleTag"] for e in db.tournament_enrollments.find({}, {"battleTag": 1})}

# 当前最大 position
max_pos_doc = db.tournament_enrollments.find_one(
    {"status": {"$in": ["enrolled", "waitlist"]}},
    sort=[("position", -1)]
)
pos = max_pos_doc["position"] if max_pos_doc else 0

enrolled_count = db.tournament_enrollments.count_documents({"status": "enrolled"})
now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

players = list(db.league_players.find({"verified": True}))
added = 0
skipped = 0

for p in players:
    bt = p.get("battleTag", "")
    if not bt or bt in existing:
        skipped += 1
        continue

    pos += 1
    status = "enrolled" if enrolled_count < ENROLL_CAP else "waitlist"
    if status == "enrolled":
        enrolled_count += 1

    db.tournament_enrollments.insert_one({
        "battleTag": bt,
        "displayName": p.get("displayName", bt.split("#")[0]),
        "accountIdLo": str(p.get("accountIdLo", "")),
        "status": status,
        "enrollAt": now_str,
        "withdrawnAt": None,
        "position": pos,
    })
    added += 1

print(f"完成：新增 {added}，跳过 {skipped}（已报名或无 battleTag）")
print(f"当前正选 {enrolled_count} / {ENROLL_CAP}")
