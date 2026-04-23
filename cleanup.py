"""后台清理任务：超时对局、掉线对局、过期队列、过期绑定码"""

import json
import logging
import os
import threading
import time
import urllib.request
from datetime import datetime, timedelta, UTC

from db import get_db, GAME_TIMEOUT_MINUTES, QUEUE_TIMEOUT_MINUTES, WAITING_QUEUE_TIMEOUT_MINUTES

log = logging.getLogger("bgtracker")

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
CLEANUP_INTERVAL = int(os.environ.get("CLEANUP_INTERVAL", "60"))

_last_queue_cleanup_ts = 0


def send_webhook(payload):
    """发送通知到 QQ 机器人 webhook（失败不阻塞主流程）"""
    if not WEBHOOK_URL:
        return
    try:
        def _do_post():
            req = urllib.request.Request(
                WEBHOOK_URL,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                log.warning(f"webhook 发送失败: {e}")
        threading.Thread(target=_do_post, daemon=True).start()
    except Exception as e:
        log.warning(f"webhook 启动失败: {e}")


def cleanup_expired_bind_codes():
    """清理过期的绑定码"""
    db = get_db()
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = db.league_players.update_many(
        {"bindCodeExpire": {"$lt": now_str}},
        {"$unset": {"bindCode": "", "bindCodeExpire": ""}}
    )
    if result.modified_count > 0:
        log.info(f"清理了 {result.modified_count} 个过期绑定码")


def cleanup_stale_games():
    """将超过超时时间的未结束对局标记为超时结束，并发送 webhook 通知"""
    db = get_db()
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_str = (datetime.now(UTC) - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = {
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$lt": cutoff_str}},
            {"status": {"$exists": False}},
        ]
    }
    matches = list(db.league_matches.find(query))
    if not matches:
        return

    for m in matches:
        players = [p.get("displayName", p.get("battleTag", "")) for p in m.get("players", [])]
        send_webhook({
            "type": "timeout",
            "gameUuid": m.get("gameUuid", ""),
            "players": players,
            "startedAt": m.get("startedAt", ""),
        })

    result = db.league_matches.update_many(
        query,
        {"$set": {"endedAt": now_str, "status": "timeout"}}
    )
    if result.modified_count > 0:
        log.info(f"清理了 {result.modified_count} 个超时对局")


def cleanup_partial_matches():
    """处理部分掉线导致永不结束的对局"""
    db = get_db()
    cutoff_str = (datetime.now(UTC) - timedelta(minutes=GAME_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = {
        "$and": [
            {"$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}]},
            {"startedAt": {"$lt": cutoff_str}},
            {"status": {"$exists": False}},
        ]
    }
    matches = list(db.league_matches.find(query))
    if not matches:
        return

    count = 0
    for m in matches:
        players = m.get("players", [])
        has_any_placement = any(p.get("placement") is not None for p in players)
        if not has_any_placement:
            continue

        player_names = [p.get("displayName", p.get("battleTag", "")) for p in players if p.get("placement") is None]
        send_webhook({
            "type": "abandoned",
            "gameUuid": m.get("gameUuid", ""),
            "players": player_names,
            "startedAt": m.get("startedAt", ""),
        })

        db.league_matches.update_one(
            {"_id": m["_id"]},
            {"$set": {
                "endedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "abandoned"
            }}
        )
        count += 1

    if count > 0:
        log.info(f"标记了 {count} 个部分掉线对局")


def cleanup_stale_queues():
    """清理过期的队列条目"""
    global _last_queue_cleanup_ts
    now = time.time()
    if now - _last_queue_cleanup_ts < 30:
        return
    _last_queue_cleanup_ts = now

    db = get_db()
    now_dt = datetime.now(UTC)

    # 清理报名队列中超时的玩家
    queue_cutoff = (now_dt - timedelta(minutes=QUEUE_TIMEOUT_MINUTES)).isoformat() + "Z"
    expired_queue = list(db.league_queue.find({"lastSeen": {"$lt": queue_cutoff}}))
    if expired_queue:
        names = [p["name"] for p in expired_queue]
        db.league_queue.delete_many({"name": {"$in": names}})
        log.info(f"报名队列踢出超时玩家: {names}")

    # 清理等待队列中超时的组
    waiting_cutoff = (now_dt - timedelta(minutes=WAITING_QUEUE_TIMEOUT_MINUTES)).isoformat() + "Z"
    expired_groups = list(db.league_waiting_queue.find({"createdAt": {"$lt": waiting_cutoff}}))
    for group in expired_groups:
        db.league_waiting_queue.delete_one({"_id": group["_id"]})
        expired_names = [p.get("name", "") for p in group.get("players", [])]
        log.info(f"等待组解散: {expired_names}")


def _background_cleanup():
    """后台定时清理"""
    while True:
        try:
            cleanup_stale_games()
            cleanup_partial_matches()
            cleanup_stale_queues()
            cleanup_expired_bind_codes()
        except Exception as e:
            log.error(f"后台 cleanup 异常: {e}")
        time.sleep(CLEANUP_INTERVAL)
