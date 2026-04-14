#!/usr/bin/env python3
"""
toggle-test-mode.py — 切换联赛网站测试/正常模式

测试模式：等待组匹配失败时，按重叠人数判定（至少 MIN_MATCH_PLAYERS 人即算联赛）
正常模式：精确匹配 8 人，不匹配则为普通天梯局

用法：
  python toggle-test-mode.py          # 显示当前状态
  python toggle-test-mode.py test     # 切换到测试模式
  python toggle-test-mode.py normal   # 切换到正常模式
  python toggle-test-mode.py flip     # 翻转

工作原理：基于代码中的 BEGIN/END TEST_MODE 标记进行整块替换。
"""

import sys
import os
import re

os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")

BEGIN = "BEGIN TEST_MODE"
END = "END TEST_MODE"

FLASK_PATH = "app.py"

FLASK_NORMAL = '''\
    # >>> BEGIN TEST_MODE
    if matched_group is None:
        # fallback：等待组已被队友匹配删除，但联赛对局已创建
        is_league = db.league_matches.find_one({"gameUuid": game_uuid}) is not None
        resp = {"isLeague": is_league}
        vc = _ensure_verification_code(
            db,
            player_id=data.get("playerId", "").strip(),
            account_id_lo=data.get("accountIdLo", "").strip(),
            mode=data.get("mode", "solo"),
            region=data.get("region", "CN"),
        )
        if vc:
            resp["verificationCode"] = vc
        return jsonify(resp)
    # <<< END TEST_MODE'''

FLASK_TEST = '''\
    # >>> BEGIN TEST_MODE
    if matched_group is None:
        # 统计各等待组与本局玩家的重叠人数
        best_overlap = 0
        best_group = None
        for group in waiting_groups:
            queue_ids_check = set()
            all_have_lo = True
            for p in group.get("players", []):
                lo = str(p.get("accountIdLo", ""))
                if lo:
                    queue_ids_check.add(lo)
                else:
                    all_have_lo = False
                    break
            if not all_have_lo:
                continue
            overlap = len(account_ids & queue_ids_check)
            if overlap > best_overlap:
                best_overlap = overlap
                best_group = group

        if best_overlap >= MIN_MATCH_PLAYERS and best_group:
            # 达到阈值，当作联赛处理
            db.league_waiting_queue.delete_one({"_id": best_group["_id"]})
            detailed_players = data.get("players", {})
            players = []
            for lo in account_ids:
                detail = detailed_players.get(lo, {})
                players.append({
                    "accountIdLo": lo,
                    "battleTag": detail.get("battleTag", ""),
                    "displayName": detail.get("displayName", ""),
                    "heroCardId": detail.get("heroCardId", ""),
                    "heroName": detail.get("heroName", ""),
                    "placement": None,
                    "points": None,
                })
            mode = data.get("mode", "solo")
            region = data.get("region", "CN")
            started_at = data.get("startedAt", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
            db.league_matches.update_one(
                {"gameUuid": game_uuid},
                {"$setOnInsert": {
                    "players": players,
                    "region": region,
                    "mode": mode,
                    "startedAt": started_at,
                    "endedAt": None,
                }},
                upsert=True,
            )
            resp = {"isLeague": True}
            vc = _ensure_verification_code(
                db,
                player_id=data.get("playerId", "").strip(),
                account_id_lo=data.get("accountIdLo", "").strip(),
                mode=mode, region=region, timestamp=started_at,
            )
            if vc:
                resp["verificationCode"] = vc
            log.info(f"[check-league] [TEST] 匹配 {best_overlap} 人（阈值 {MIN_MATCH_PLAYERS}），gameUuid={game_uuid}")
            return jsonify(resp)

        # 未达阈值，普通天梯局
        log.info(f"[check-league] [TEST] 最多匹配 {best_overlap} 人（阈值 {MIN_MATCH_PLAYERS}），跳过")
        resp = {"isLeague": False}
        vc = _ensure_verification_code(
            db,
            player_id=data.get("playerId", "").strip(),
            account_id_lo=data.get("accountIdLo", "").strip(),
            mode=data.get("mode", "solo"),
            region=data.get("region", "CN"),
        )
        if vc:
            resp["verificationCode"] = vc
        return jsonify(resp)
    # <<< END TEST_MODE'''


def find_marker_block(content: str) -> str | None:
    """返回两个标记之间的完整文本（含标记行），找不到返回 None"""
    begin_idx = content.find(BEGIN)
    end_idx = content.find(END)
    if begin_idx < 0 or end_idx < 0:
        return None
    block_start = content.rfind("\n", 0, begin_idx) + 1
    end_line_end = content.find("\n", end_idx)
    if end_line_end < 0:
        end_line_end = len(content)
    return content[block_start:end_line_end]


def detect_mode(content: str) -> str | None:
    block = find_marker_block(content)
    if block is None:
        return None
    def strip_markers(b):
        return "\n".join(
            line for line in b.split("\n")
            if BEGIN not in line and END not in line
        )
    block_core = strip_markers(block)
    if block_core == strip_markers(FLASK_NORMAL):
        return "normal"
    if block_core == strip_markers(FLASK_TEST):
        return "test"
    return None


def replace_block(content: str, new_block: str) -> str:
    old_block = find_marker_block(content)
    if old_block is None:
        raise ValueError(f"找不到 {BEGIN}/{END} 标记")
    return content.replace(old_block, new_block, 1)


MIN_MATCH_RE = re.compile(r"^(MIN_MATCH_PLAYERS\s*=\s*)\d+", re.MULTILINE)

def replace_min_match(content: str, target: str) -> str:
    """切换 MIN_MATCH_PLAYERS 的值：normal=8, test=3"""
    val = "8" if target == "normal" else "3"
    return MIN_MATCH_RE.sub(rf"\g<1>{val}", content, count=1)


def main():
    if not os.path.exists(FLASK_PATH):
        print(f"⚠ 找不到 {FLASK_PATH}")
        sys.exit(1)

    with open(FLASK_PATH, "r", encoding="utf-8") as f:
        flask_content = f.read()

    mode = detect_mode(flask_content)
    if mode is None:
        print(f"⚠ {FLASK_PATH} 无法识别模式，TEST_MODE 标记可能被修改")
        sys.exit(1)

    print(f"[网站] 当前模式: {mode}")

    args = sys.argv[1:]
    if not args:
        sys.exit(0)

    target = args[0]
    if target == "flip":
        target = "test" if mode == "normal" else "normal"
    if target not in ("test", "normal"):
        print(f"用法: {sys.argv[0]} [test|normal|flip]")
        sys.exit(1)

    if target == mode:
        print(f"已经是 {target} 模式，无需切换")
        sys.exit(0)

    print(f"[网站] 切换到: {target} 模式")

    new_content = replace_block(flask_content, FLASK_TEST if target == "test" else FLASK_NORMAL)
    new_content = replace_min_match(new_content, target)
    with open(FLASK_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✅ 网站已切换到 {target} 模式")
    os.system(f"git diff --stat {FLASK_PATH}")


if __name__ == "__main__":
    main()
