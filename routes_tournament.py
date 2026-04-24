"""淘汰赛路由：对阵图、赛事管理、报名"""

import hashlib
import json
import logging
import os
import secrets
import struct
from datetime import datetime, timedelta, UTC
from bson import ObjectId
from flask import Blueprint, jsonify, request, session, render_template

from db import get_db, to_iso_str, ENROLL_CAP, ENROLL_SLOTS, ENROLL_DEADLINE, TOURNAMENT_PHASE
from auth import is_admin, _admin_required
from data import get_group_rankings, try_advance_group, try_advance_round
from cleanup import cleanup_enrollment_deadline

log = logging.getLogger("bgtracker")
tournament_bp = Blueprint("tournament", __name__)


def _enroll_deadline_reached():
    """检查报名是否已截止"""
    if not ENROLL_DEADLINE:
        return False
    try:
        deadline = datetime.fromisoformat(ENROLL_DEADLINE)
        if deadline.tzinfo is None:
            from datetime import timezone
            deadline = deadline.replace(tzinfo=timezone.utc)
        return datetime.now(UTC) >= deadline
    except Exception:
        return False


def _promote_waitlist(db):
    """正选退出后，从替补队列按报名时间顺序补上"""
    enrolled_count = db.tournament_enrollments.count_documents({"status": "enrolled"})
    if enrolled_count >= ENROLL_SLOTS:
        return

    slots_available = ENROLL_SLOTS - enrolled_count
    waitlist = list(db.tournament_enrollments.find(
        {"status": "waitlist"}
    ).sort("enrollAt", 1).limit(slots_available))

    for w in waitlist:
        db.tournament_enrollments.update_one(
            {"_id": w["_id"]},
            {"$set": {"status": "enrolled", "position": enrolled_count + 1}}
        )
        enrolled_count += 1
        log.info(f"[enroll] 替补补上: {w.get('battleTag')} → 正选 #{enrolled_count}")



def _build_bracket_mock():
    """对阵图 mock 数据（tournament_groups 为空时的 fallback）"""
    PLAYERS = [
        {'displayName': '衣锦夜行', 'battleTag': '衣锦夜行#1001', 'accountIdLo': 1000001, 'heroCardId': 'TB_BaconShop_HERO_56', 'heroName': '阿莱克丝塔萨'},
        {'displayName': '瓦莉拉',   'battleTag': '瓦莉拉#1002',   'accountIdLo': 1000002, 'heroCardId': 'TB_BaconShop_HERO_02', 'heroName': '帕奇维克'},
        {'displayName': '雷克萨',   'battleTag': '雷克萨#1003',   'accountIdLo': 1000003, 'heroCardId': 'TB_BaconShop_HERO_22', 'heroName': '巫妖王'},
        {'displayName': '古尔丹',   'battleTag': '古尔丹#1004',   'accountIdLo': 1000004, 'heroCardId': 'TB_BaconShop_HERO_19', 'heroName': '米尔豪斯'},
        {'displayName': '吉安娜',   'battleTag': '吉安娜#1005',   'accountIdLo': 1000005, 'heroCardId': 'TB_BaconShop_HERO_01', 'heroName': '鼠王'},
        {'displayName': '萨尔',     'battleTag': '萨尔#1006',     'accountIdLo': 1000006, 'heroCardId': 'TB_BaconShop_HERO_08', 'heroName': '尤格-萨隆'},
        {'displayName': '乌瑟尔',   'battleTag': '乌瑟尔#1007',   'accountIdLo': 1000007, 'heroCardId': 'TB_BaconShop_HERO_13', 'heroName': '伊瑟拉'},
        {'displayName': '玛法里奥', 'battleTag': '玛法里奥#1008', 'accountIdLo': 1000008, 'heroCardId': 'TB_BaconShop_HERO_36', 'heroName': '拉卡尼休'},
    ]
    GROUP_LABELS = 'ABCDEFGH'

    def _round_label(r):
        return {1: '小组赛', 2: '第二轮', 3: '半决赛', 4: '决赛'}.get(r, f'第 {r} 轮')

    def _group_label(r, gi, total):
        if r >= 4 and total == 1:
            return '决赛'
        if r == 1:
            return f'{GROUP_LABELS[gi % 8]}{gi // 8 + 1} 组' if total > 8 else f'{GROUP_LABELS[gi]} 组'
        return f'{gi + 1} 组'

    def mk_players(qual_count=0, done=False):
        players = []
        for i, bp in enumerate(PLAYERS):
            qual = done and i < qual_count
            players.append({
                **bp,
                'placement': (i + 1) if done else None,
                'points': (9 if i == 0 else max(1, 9 - i)) if done else None,
                'qualified': qual,
                'eliminated': done and not qual,
                'empty': False,
            })
        return players

    def empty_players():
        return [{'displayName': '待定', 'battleTag': None, 'accountIdLo': None,
                 'heroCardId': None, 'heroName': None,
                 'placement': None, 'points': None,
                 'qualified': False, 'eliminated': False, 'empty': True}] * 8

    def mk_group(round_num, gi, total, status, players=None, bo_n=1):
        return {
            'round': round_num, 'groupIndex': gi + 1,
            'label': _group_label(round_num, gi, total),
            'status': status, 'boN': bo_n,
            'gamesPlayed': bo_n if status == 'done' else 0,
            'players': players or empty_players(),
            'startedAt': '2026-04-21T20:00:00Z' if status != 'waiting' else None,
            'endedAt': '2026-04-21T20:45:00Z' if status == 'done' else None,
            'nextRoundGroupId': (gi + 2) // 2 if total > 1 else None,
        }

    r1_groups = [mk_group(1, i, 8, 'done', mk_players(4, True), bo_n=3) for i in range(8)]

    def build_round(prev_groups, round_num):
        buckets = {}
        for g in prev_groups:
            gi = g.get('groupIndex', 0)
            nrg = (gi + 1) // 2 if len(prev_groups) > 1 else None
            if nrg is not None:
                buckets.setdefault(nrg, []).append(g)
        groups = []
        for gid in sorted(buckets.keys()):
            srcs = buckets[gid]
            total = len(buckets)
            all_done = all(s['status'] == 'done' for s in srcs)
            if all_done:
                quals = []
                for s in srcs:
                    for p in s['players']:
                        if p.get('qualified'):
                            quals.append({**p, 'placement': None, 'points': None,
                                          'qualified': False, 'eliminated': False})
                while len(quals) < 8:
                    quals.append({'displayName': '待定', 'battleTag': None, 'accountIdLo': None,
                                  'heroCardId': None, 'heroName': None,
                                  'placement': None, 'points': None,
                                  'qualified': False, 'eliminated': False, 'empty': True})
                groups.append(mk_group(round_num, gid - 1, total, 'waiting', quals, bo_n=5))
            else:
                groups.append(mk_group(round_num, gid - 1, total, 'waiting', bo_n=5))
        return groups

    r2_groups = build_round(r1_groups, 2)
    for g in r2_groups:
        g['status'] = 'done'
        g['endedAt'] = '2026-04-21T21:00:00Z'
        g['boN'] = 3
        g['gamesPlayed'] = 3
        for i, p in enumerate(g['players']):
            if not p.get('empty'):
                p['placement'] = i + 1
                p['points'] = 9 if i == 0 else max(1, 9 - i)
                p['qualified'] = i < 4
                p['eliminated'] = i >= 4

    r3_groups = build_round(r2_groups, 3)
    for g in r3_groups:
        g['status'] = 'active'
        g['startedAt'] = '2026-04-22T10:00:00Z'
        g['boN'] = 3
        g['gamesPlayed'] = 1
        for i, p in enumerate(g['players']):
            if not p.get('empty') and i >= 6:
                p['placement'] = i + 1
                p['points'] = 9 if i == 0 else max(1, 9 - i)
                p['eliminated'] = True

    final_groups = build_round(r3_groups, 4)
    for g in final_groups:
        g.pop('nextRoundGroupId', None)

    return {'tournaments': [{'name': '2026 春季赛', 'rounds': [
        {'label': _round_label(1), 'groups': r1_groups},
        {'label': _round_label(2), 'groups': r2_groups},
        {'label': _round_label(3), 'groups': r3_groups},
        {'label': _round_label(4), 'groups': final_groups},
    ]}]}


def build_bracket_data():
    """从 tournament_groups 集合读取对阵图数据"""
    db = get_db()
    GROUP_LABELS = "ABCDEFGH"

    def _round_label(r, total_rounds, layout="bracket"):
        if layout == "grid":
            return f"第 {r} 轮" if total_rounds > 1 else "海选"
        if r == total_rounds:
            return "决赛"
        if r == total_rounds - 1:
            return "半决赛"
        return f"第 {r} 轮"

    def _group_label(r, gi, total, total_rounds, layout="bracket"):
        if layout == "grid":
            return f"{GROUP_LABELS[gi]} 组" if total <= 8 else f"{GROUP_LABELS[gi % 8]}{gi // 8 + 1} 组"
        if r == total_rounds and total == 1:
            return "决赛"
        if r == 1:
            return f"{GROUP_LABELS[gi % 8]}{gi // 8 + 1} 组" if total > 8 else f"{GROUP_LABELS[gi]} 组"
        return f"{gi + 1} 组"

    groups = list(db.tournament_groups.find().sort([("round", 1), ("groupIndex", 1)]))
    if not groups:
        return _build_bracket_mock()

    tournaments_map = {}
    for g in groups:
        tname = g.get("tournamentName", "赛事")
        tournaments_map.setdefault(tname, []).append(g)

    group_rankings = get_group_rankings(db)

    result = []
    for tname, tgroups in tournaments_map.items():
        rounds_map = {}
        for g in tgroups:
            r = g.get("round", 1)
            rounds_map.setdefault(r, []).append(g)

        # 提前确定赛事布局
        layout = "bracket"
        for g in tgroups:
            if g.get("layout"):
                layout = g["layout"]
                break

        rounds_data = []
        sorted_rounds = sorted(rounds_map.keys())

        # 为每组附加排名数据
        for r in sorted_rounds:
            for g in rounds_map[r]:
                tg_str = str(g["_id"])
                rankings = group_rankings.get(tg_str, {})
                for p in g.get("players", []):
                    lo = str(p.get("accountIdLo", ""))
                    rank_data = rankings.get(lo)
                    if rank_data:
                        p["totalPoints"] = rank_data["totalPoints"]
                        p["games"] = rank_data["games"]
                        p["placement"] = rank_data["placement"]
                        p["points"] = rank_data["totalPoints"]
                        p["qualified"] = rank_data["qualified"]
                        p["eliminated"] = rank_data["eliminated"]
                        p["chickens"] = rank_data.get("chickens", 0)
                        p["lastGamePlacement"] = rank_data.get("lastGamePlacement", 999)
                    else:
                        p["totalPoints"] = 0
                        p["games"] = []
                        p["placement"] = None
                        p["points"] = None
                        p["qualified"] = False
                        p["eliminated"] = False
                        p["chickens"] = 0
                        p["lastGamePlacement"] = 999
                    p["empty"] = p.get("empty", False)

        # done 组排序
        for r in sorted_rounds:
            for g in rounds_map[r]:
                if g.get("status") == "done":
                    g["players"].sort(key=lambda p: (
                        -(p.get("totalPoints", 0)),
                        -(p.get("chickens", 0)),
                        p.get("lastGamePlacement", 999),
                    ))

        # waiting 组（BO 间歇期）排序
        for r in sorted_rounds:
            for g in rounds_map[r]:
                if g.get("status") == "waiting" and g.get("gamesPlayed", 0) > 0:
                    tg_id = g["_id"]
                    agg_pipeline = [
                        {"$match": {"tournamentGroupId": tg_id, "endedAt": {"$ne": None}}},
                        {"$unwind": "$players"},
                        {"$match": {"players.placement": {"$ne": None}}},
                        {"$group": {
                            "_id": "$players.accountIdLo",
                            "totalPoints": {"$sum": "$players.points"},
                        }},
                    ]
                    points_by_lo = {}
                    for doc in db.league_matches.aggregate(agg_pipeline):
                        points_by_lo[str(doc["_id"])] = doc["totalPoints"]

                    for p in g.get("players", []):
                        lo = str(p.get("accountIdLo", ""))
                        if lo in points_by_lo:
                            p["totalPoints"] = points_by_lo[lo]
                            p["points"] = points_by_lo[lo]
                        elif not p.get("empty"):
                            p["totalPoints"] = 0
                            p["points"] = 0
                    g["players"].sort(key=lambda p: (
                        -(p.get("totalPoints", 0)),
                        -(p.get("chickens", 0)),
                        p.get("lastGamePlacement", 999),
                    ))

        # 活跃组注入英雄 + 死亡状态
        active_tg_ids = [g["_id"] for r in sorted_rounds for g in rounds_map[r] if g.get("status") == "active"]
        active_matches_by_tg = {}
        if active_tg_ids:
            for m in db.league_matches.find({
                "tournamentGroupId": {"$in": active_tg_ids},
                "$or": [{"endedAt": None}, {"endedAt": {"$exists": False}}],
            }):
                tg_id = m.get("tournamentGroupId")
                if tg_id:
                    active_matches_by_tg[str(tg_id)] = m

        for r in sorted_rounds:
            rgroups = sorted(rounds_map[r], key=lambda g: g.get("groupIndex", 0))
            total = len(rgroups)
            groups_data = []
            for g in rgroups:
                gi = g.get("groupIndex", 1) - 1
                bo_n = g.get("boN", 1)
                games_played = g.get("gamesPlayed", 0)
                status = g.get("status", "waiting")

                tg_str = str(g["_id"])
                current_match = active_matches_by_tg.get(tg_str)
                match_players_by_lo = {}
                if current_match:
                    status = "active"
                    for mp in current_match.get("players", []):
                        match_players_by_lo[str(mp.get("accountIdLo", ""))] = mp

                players = g.get("players", [])
                if match_players_by_lo:
                    for p in players:
                        lo = str(p.get("accountIdLo", ""))
                        mp = match_players_by_lo.get(lo)
                        if mp:
                            p["heroCardId"] = mp.get("heroCardId", p.get("heroCardId", ""))
                            p["heroName"] = mp.get("heroName", p.get("heroName", ""))
                            p["dead"] = mp.get("placement") is not None
                            p["currentPlacement"] = mp.get("placement")
                            if p["dead"] and p["currentPlacement"]:
                                cp = p["currentPlacement"]
                                this_game_pts = 9 if cp == 1 else max(1, 9 - cp)
                                prev_pts = p.get("points") or 0
                                p["points"] = prev_pts + this_game_pts
                        else:
                            p["dead"] = False

                gd = {
                    "round": r,
                    "groupIndex": gi + 1,
                    "label": _group_label(r, gi, total, len(sorted_rounds), layout),
                    "status": status,
                    "boN": bo_n,
                    "gamesPlayed": games_played,
                    "players": players,
                    "startedAt": g.get("startedAt"),
                    "endedAt": g.get("endedAt"),
                }
                if g.get("nextRoundGroupId"):
                    gd["nextRoundGroupId"] = g["nextRoundGroupId"]
                groups_data.append(gd)

            rounds_data.append({"label": _round_label(r, len(sorted_rounds), layout), "groups": groups_data})

        # 取赛事布局（默认 bracket，兼容旧数据）
        # layout 已提前确定

        result.append({"name": tname, "rounds": rounds_data, "layout": layout})

    # 如果有 bracket 布局的赛事，隐藏 grid 布局（海选）
    has_bracket = any(t["layout"] == "bracket" for t in result)
    if has_bracket:
        result = [t for t in result if t["layout"] != "grid"]

    # 多个 bracket 赛事时只显示最后创建的（海选 → 512强）
    bracket_tournaments = [t for t in result if t["layout"] == "bracket"]
    if len(bracket_tournaments) > 1:
        def _created_at(t):
            for rd in t.get("rounds", []):
                for g in rd.get("groups", []):
                    ca = g.get("createdAt")
                    if ca:
                        return ca
            return ""
        bracket_tournaments.sort(key=_created_at)
        result = [bracket_tournaments[-1]]

    return {"tournaments": result}


# ── 页面路由 ──────────────────────────────────────────

@tournament_bp.route("/bracket")
def bracket_page():
    data = build_bracket_data()
    return render_template("bracket.html", data_json=json.dumps(data, ensure_ascii=False))


@tournament_bp.route("/verify-shuffle")
def verify_shuffle_page():
    return render_template("verify_shuffle.html")


@tournament_bp.route("/enroll")
def enroll_page():
    return render_template("enroll.html")


# ── API 路由 ──────────────────────────────────────────

@tournament_bp.route("/api/bracket")
def api_bracket():
    return jsonify(build_bracket_data())


@tournament_bp.route("/api/tournament/create", methods=["POST"])
def api_tournament_create():
    battle_tag = session.get("battleTag")
    if not is_admin(battle_tag):
        return jsonify({"error": "需要管理员权限"}), 403

    data = request.get_json() or {}
    tname = data.get("tournamentName", "").strip()
    rounds = data.get("rounds", [])
    layout = data.get("layout", "bracket")  # "bracket" | "grid"

    if layout not in ("bracket", "grid"):
        layout = "bracket"

    if not tname or not rounds:
        return jsonify({"error": "tournamentName 和 rounds 不能为空"}), 400

    db = get_db()
    groups_to_insert = []

    for rd in rounds:
        r = rd.get("round", 1)
        bo_n = rd.get("boN", 1)
        for g in rd.get("groups", []):
            players = []
            for p in g.get("players", []):
                players.append({
                    "battleTag": p.get("battleTag", ""),
                    "accountIdLo": str(p.get("accountIdLo", "")),
                    "displayName": p.get("displayName", ""),
                    "heroCardId": p.get("heroCardId", ""),
                    "heroName": p.get("heroName", ""),
                    "empty": False,
                })
            while len(players) < 8:
                players.append({"battleTag": None, "accountIdLo": None, "displayName": "待定",
                                "heroCardId": None, "heroName": None, "empty": True})

            gi = g.get("groupIndex", 1)
            total_groups = len(rd.get("groups", []))
            nrg = (gi + 1) // 2 if total_groups > 1 else None

            now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            groups_to_insert.append({
                "tournamentName": tname,
                "round": r,
                "groupIndex": gi,
                "status": "waiting",
                "boN": bo_n,
                "gamesPlayed": 0,
                "players": players,
                "nextRoundGroupId": nrg,
                "layout": layout,
                "createdAt": now_str,
                "startedAt": None,
                "endedAt": None,
            })

    if groups_to_insert:
        db.tournament_groups.insert_many(groups_to_insert)

    log.info(f"[tournament] 创建赛事: {tname} {len(groups_to_insert)} 个分组 layout={layout}")
    return jsonify({"ok": True, "tournamentName": tname, "groupsCreated": len(groups_to_insert), "layout": layout})


@tournament_bp.route("/api/tournament/group/<group_id>")
def api_tournament_group(group_id):
    db = get_db()
    try:
        group = db.tournament_groups.find_one({"_id": ObjectId(group_id)})
    except Exception:
        return jsonify({"error": "无效的 group ID"}), 400

    if not group:
        return jsonify({"error": "分组不存在"}), 404

    group_rankings = get_group_rankings(db, group.get("tournamentName"))
    tg_str = str(group["_id"])
    rankings = group_rankings.get(tg_str, {})
    for p in group.get("players", []):
        lo = str(p.get("accountIdLo", ""))
        rank_data = rankings.get(lo)
        if rank_data:
            p.update(rank_data)

    group["_id"] = str(group["_id"])
    return jsonify(group)


@tournament_bp.route("/api/tournament/manage/<path:tournament_name>")
def api_tournament_manage(tournament_name):
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    groups = list(db.tournament_groups.find({"tournamentName": tournament_name}).sort([("round", 1), ("groupIndex", 1)]))
    if not groups:
        return jsonify({"error": "赛事不存在"}), 404

    group_rankings = get_group_rankings(db, tournament_name)
    for g in groups:
        tg_str = str(g["_id"])
        rankings = group_rankings.get(tg_str, {})
        for p in g.get("players", []):
            lo = str(p.get("accountIdLo", ""))
            rank_data = rankings.get(lo)
            if rank_data:
                p.update(rank_data)
        g["_id"] = str(g["_id"])

    return jsonify({"name": tournament_name, "groups": groups})


@tournament_bp.route("/api/tournament/shuffle", methods=["POST"])
def api_tournament_shuffle():
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    data = request.get_json() or {}
    seed = data.get("seed", "").strip()
    players = data.get("players", [])

    if not seed:
        return jsonify({"error": "seed 不能为空"}), 400
    if len(players) < 2:
        return jsonify({"error": "至少需要 2 位选手"}), 400

    h = hashlib.sha256(seed.encode("utf-8")).digest()
    seed_int = sum(struct.unpack_from("<I", h, i)[0] for i in range(0, 32, 4))

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

    rng = make_rng(seed_int)
    arr = list(players)
    for i in range(len(arr) - 1, 0, -1):
        j = rng(i + 1)
        arr[i], arr[j] = arr[j], arr[i]

    log.info(f"[tournament] 管理员 {admin_tag} 执行洗牌，seed=\"{seed}\"，{len(arr)} 位选手")
    return jsonify({"ok": True, "seed": seed, "players": arr})


@tournament_bp.route("/api/tournament/group/<group_id>/update", methods=["PUT"])
def api_tournament_group_update(group_id):
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    try:
        oid = ObjectId(group_id)
    except Exception:
        return jsonify({"error": "无效的 group ID"}), 400

    group = db.tournament_groups.find_one({"_id": oid})
    if not group:
        return jsonify({"error": "分组不存在"}), 404

    if group.get("status") not in ("waiting", None):
        if group.get("gamesPlayed", 0) > 0:
            return jsonify({"error": "已开始的分组不能编辑玩家"}), 400

    data = request.get_json() or {}
    update = {}

    if "boN" in data:
        bo_n = int(data["boN"])
        if bo_n < 1 or bo_n > 20:
            return jsonify({"error": "boN 必须在 1-20 之间"}), 400
        update["boN"] = bo_n

    if "players" in data:
        players = []
        for p in data["players"]:
            players.append({
                "battleTag": p.get("battleTag") or None,
                "accountIdLo": str(p.get("accountIdLo", "")) if p.get("accountIdLo") else None,
                "displayName": p.get("displayName", "待定"),
                "heroCardId": p.get("heroCardId") or None,
                "heroName": p.get("heroName") or None,
                "empty": not bool(p.get("battleTag")),
            })
        while len(players) < 8:
            players.append({"battleTag": None, "accountIdLo": None, "displayName": "待定",
                            "heroCardId": None, "heroName": None, "empty": True})
        update["players"] = players

    if update:
        db.tournament_groups.update_one({"_id": oid}, {"$set": update})

    log.info(f"[tournament] 管理员 {admin_tag} 更新分组 {group_id}: {list(update.keys())}")
    return jsonify({"ok": True})


@tournament_bp.route("/api/tournament/<path:tournament_name>", methods=["DELETE"])
def api_tournament_delete(tournament_name):
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    groups = list(db.tournament_groups.find({"tournamentName": tournament_name}))
    if not groups:
        return jsonify({"error": "赛事不存在"}), 404

    active = [g for g in groups if g.get("gamesPlayed", 0) > 0 or g.get("status") == "active"]
    if active:
        return jsonify({"error": f"有 {len(active)} 个分组已开始比赛，不能删除"}), 400

    result = db.tournament_groups.delete_many({"tournamentName": tournament_name})
    log.info(f"[tournament] 管理员 {admin_tag} 删除赛事 {tournament_name}，删除 {result.deleted_count} 个分组")
    return jsonify({"ok": True, "deleted": result.deleted_count})


@tournament_bp.route("/api/tournaments")
def api_tournaments():
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    groups = list(db.tournament_groups.find().sort([("tournamentName", 1), ("round", 1), ("groupIndex", 1)]))

    tournaments_map = {}
    for g in groups:
        tname = g.get("tournamentName", "未知赛事")
        if tname not in tournaments_map:
            tournaments_map[tname] = {"name": tname, "rounds": {}, "totalGroups": 0, "statusCounts": {}}
        r = g.get("round", 1)
        tournaments_map[tname]["rounds"].setdefault(r, []).append(g)
        tournaments_map[tname]["totalGroups"] += 1
        s = g.get("status", "waiting")
        tournaments_map[tname]["statusCounts"][s] = tournaments_map[tname]["statusCounts"].get(s, 0) + 1

    result = []
    for tname, info in tournaments_map.items():
        result.append({
            "name": tname,
            "totalGroups": info["totalGroups"],
            "statusCounts": info["statusCounts"],
            "rounds": sorted(info["rounds"].keys()),
        })

    return jsonify(result)


@tournament_bp.route("/api/tournament/qualifier-pool")
def api_tournament_qualifier_pool():
    """获取指定赛事的晋级者 + 种子选手（合并后的选手池）"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    source_tournament = request.args.get("tournament", "").strip()
    if not source_tournament:
        return jsonify({"error": "tournament 参数不能为空"}), 400

    db = get_db()

    # 从源赛事所有 done 组聚合前 4 名
    source_groups = list(db.tournament_groups.find({
        "tournamentName": source_tournament,
        "status": "done",
    }))
    if not source_groups:
        return jsonify({"error": f"赛事「{source_tournament}」没有已完成的分组"}), 400

    group_rankings = get_group_rankings(db, source_tournament)
    qualifiers = []
    seen_los = set()
    for g in source_groups:
        tg_str = str(g["_id"])
        rankings = group_rankings.get(tg_str, {})
        ranked = sorted(
            g.get("players", []),
            key=lambda p: rankings.get(str(p.get("accountIdLo", "")), {}).get("totalPoints", 0),
            reverse=True,
        )
        for p in ranked[:4]:
            lo = str(p.get("accountIdLo", ""))
            if lo and lo not in seen_los:
                seen_los.add(lo)
                qualifiers.append({
                    "battleTag": p.get("battleTag", ""),
                    "accountIdLo": lo,
                    "displayName": p.get("displayName", ""),
                    "heroCardId": p.get("heroCardId", ""),
                    "heroName": p.get("heroName", ""),
                })

    # 种子选手（不在晋级者中）
    seeds = list(db.league_players.find({"isSeed": True}))
    seed_players = []
    for s in seeds:
        lo = str(s.get("accountIdLo", ""))
        if lo and lo not in seen_los:
            seen_los.add(lo)
            seed_players.append({
                "battleTag": s.get("battleTag", ""),
                "accountIdLo": lo,
                "displayName": s.get("displayName", ""),
                "heroCardId": "",
                "heroName": "",
            })

    all_players = qualifiers + seed_players
    return jsonify({
        "qualifiers": len(qualifiers),
        "seeds": len(seed_players),
        "total": len(all_players),
        "players": all_players,
    })


@tournament_bp.route("/api/tournament/generate-next", methods=["POST"])
def api_tournament_generate_next():
    """从指定赛事的晋级者 + 种子选手生成新赛事"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    data = request.get_json() or {}
    source_tournament = data.get("sourceTournament", "").strip()
    new_name = data.get("tournamentName", "").strip()
    bo_n = data.get("boN", 3)

    if not source_tournament:
        return jsonify({"error": "sourceTournament 不能为空"}), 400
    if not new_name:
        return jsonify({"error": "tournamentName 不能为空"}), 400
    if bo_n < 1 or bo_n > 20:
        return jsonify({"error": "boN 必须在 1-20 之间"}), 400

    db = get_db()

    # 1. 从源赛事所有 done 组聚合前 4 名晋级者
    source_groups = list(db.tournament_groups.find({
        "tournamentName": source_tournament,
        "status": "done",
    }))
    if not source_groups:
        return jsonify({"error": f"赛事「{source_tournament}」没有已完成的分组"}), 400

    group_rankings = get_group_rankings(db, source_tournament)
    qualifiers = []
    seen_los = set()
    for g in source_groups:
        tg_str = str(g["_id"])
        rankings = group_rankings.get(tg_str, {})
        ranked = sorted(
            g.get("players", []),
            key=lambda p: rankings.get(str(p.get("accountIdLo", "")), {}).get("totalPoints", 0),
            reverse=True,
        )
        for p in ranked[:4]:
            lo = str(p.get("accountIdLo", ""))
            if lo and lo not in seen_los:
                seen_los.add(lo)
                qualifiers.append({
                    "battleTag": p.get("battleTag", ""),
                    "accountIdLo": lo,
                    "displayName": p.get("displayName", ""),
                    "heroCardId": p.get("heroCardId", ""),
                    "heroName": p.get("heroName", ""),
                })

    # 2. 获取种子选手（不在晋级者中的）
    seeds = list(db.league_players.find({"isSeed": True}))
    seed_players = []
    for s in seeds:
        lo = str(s.get("accountIdLo", ""))
        if lo and lo not in seen_los:
            seen_los.add(lo)
            seed_players.append({
                "battleTag": s.get("battleTag", ""),
                "accountIdLo": lo,
                "displayName": s.get("displayName", ""),
                "heroCardId": "",
                "heroName": "",
            })

    all_players = qualifiers + seed_players
    total = len(all_players)
    if total < 16:
        return jsonify({"error": f"晋级者 {len(qualifiers)} 人 + 种子 {len(seed_players)} 人 = {total} 人，不足 16 人"}), 400

    # 3. 洗牌（确定性随机，基于新赛事名称）
    import hashlib, struct
    h = hashlib.sha256(new_name.encode("utf-8")).digest()
    seed_int = sum(struct.unpack_from("<I", h, i)[0] for i in range(0, 32, 4))

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

    rng = make_rng(seed_int)
    arr = list(all_players)
    for i in range(len(arr) - 1, 0, -1):
        j = rng(i + 1)
        arr[i], arr[j] = arr[j], arr[i]

    # 4. 分组（每组 8 人）
    group_count = len(arr) // 8
    if group_count < 2:
        return jsonify({"error": f"总共 {len(arr)} 人，不足 2 组"}), 400

    groups_to_insert = []
    for gi in range(group_count):
        start = gi * 8
        players = arr[start:start + 8]
        nrg = (gi + 2) // 2 if group_count > 1 else None
        groups_to_insert.append({
            "tournamentName": new_name,
            "round": 1,
            "groupIndex": gi + 1,
            "status": "waiting",
            "boN": bo_n,
            "gamesPlayed": 0,
            "players": players,
            "nextRoundGroupId": nrg,
            "startedAt": None,
            "endedAt": None,
        })

    db.tournament_groups.insert_many(groups_to_insert)

    log.info(f"[tournament] 管理员 {admin_tag} 生成新赛事: {new_name}，晋级者 {len(qualifiers)} 人 + 种子 {len(seed_players)} 人 = {len(arr)} 人，{group_count} 组")
    return jsonify({
        "ok": True,
        "tournamentName": new_name,
        "qualifiers": len(qualifiers),
        "seeds": len(seed_players),
        "total": len(arr),
        "groupsCreated": group_count,
    })


@tournament_bp.route("/api/admin/players-all")
def api_admin_players_all():
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    players = list(db.league_players.find({"verified": True}).sort("displayName", 1))
    return jsonify([{
        "battleTag": p.get("battleTag", ""),
        "displayName": p.get("displayName", ""),
        "accountIdLo": str(p.get("accountIdLo", "")),
    } for p in players])


@tournament_bp.route("/api/admin/enrolled-players")
def api_admin_enrolled_players():
    """报名选手列表（用于创建赛事分组，格式同 players-all）"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    limit = request.args.get("limit", type=int)
    query = {"status": "enrolled"}
    cursor = db.tournament_enrollments.find(query).sort("enrollAt", 1)
    if limit:
        cursor = cursor.limit(limit)
    enrollments = list(cursor)

    result = []
    for p in enrollments:
        bt = p.get("battleTag", "")
        lp = db.league_players.find_one({"battleTag": bt})
        result.append({
            "battleTag": bt,
            "displayName": p.get("displayName", ""),
            "accountIdLo": str(lp.get("accountIdLo", "")) if lp else "",
        })

    return jsonify(result)


# ── 报名 API ──────────────────────────────────────────

@tournament_bp.route("/api/enroll", methods=["POST"])
def api_enroll():
    battle_tag = session.get("battleTag")
    if not battle_tag:
        return jsonify({"error": "请先登录"}), 401

    if _enroll_deadline_reached():
        return jsonify({"error": "报名已截止"}), 400

    db = get_db()
    existing = db.tournament_enrollments.find_one({"battleTag": battle_tag})
    if existing:
        return jsonify({"error": "已报名", "status": existing.get("status")}), 400

    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    enrolled_count = db.tournament_enrollments.count_documents({"status": "enrolled"})
    waitlist_count = db.tournament_enrollments.count_documents({"status": "waitlist"})

    if enrolled_count < ENROLL_SLOTS:
        status = "enrolled"
        position = enrolled_count + 1
    else:
        status = "waitlist"
        position = ENROLL_SLOTS + waitlist_count + 1

    db.tournament_enrollments.insert_one({
        "battleTag": battle_tag,
        "displayName": session.get("displayName", battle_tag),
        "status": status,
        "position": position,
        "enrollAt": now_str,
    })

    log.info(f"[enroll] {battle_tag} → {status} #{position}")
    return jsonify({"ok": True, "status": status, "position": position,
                    "message": "报名成功" if status == "enrolled" else f"已进入替补队列（第 {position - ENROLL_SLOTS} 位）"})


@tournament_bp.route("/api/enroll/withdraw", methods=["POST"])
def api_enroll_withdraw():
    battle_tag = session.get("battleTag")
    if not battle_tag:
        return jsonify({"error": "请先登录"}), 401

    if _enroll_deadline_reached():
        return jsonify({"error": "报名已截止，无法退赛"}), 400

    db = get_db()
    existing = db.tournament_enrollments.find_one({"battleTag": battle_tag})
    if not existing:
        return jsonify({"error": "未报名"}), 400

    db.tournament_enrollments.delete_one({"battleTag": battle_tag})

    # 正选退出后替补补上
    if existing.get("status") == "enrolled":
        _promote_waitlist(db)

    log.info(f"[enroll] {battle_tag} 退赛")
    return jsonify({"ok": True, "message": "已退赛"})


@tournament_bp.route("/api/enroll/status")
def api_enroll_status():
    battle_tag = session.get("battleTag")
    if not battle_tag:
        return jsonify({"enrolled": False, "cap": ENROLL_CAP, "deadline": ENROLL_DEADLINE})

    db = get_db()
    existing = db.tournament_enrollments.find_one({"battleTag": battle_tag})
    enrolled_count = db.tournament_enrollments.count_documents({"status": "enrolled"})
    waitlist_count = db.tournament_enrollments.count_documents({"status": "waitlist"})
    total_count = enrolled_count + waitlist_count
    if existing:
        return jsonify({
            "enrolled": True,
            "status": existing.get("status"),
            "position": existing.get("position"),
            "enrollAt": to_iso_str(existing.get("enrollAt")),
            "cap": ENROLL_CAP,
            "enrolledCount": enrolled_count,
            "totalCount": total_count,
            "deadline": ENROLL_DEADLINE,
        })

    return jsonify({"enrolled": False, "cap": ENROLL_CAP, "enrolledCount": enrolled_count, "totalCount": total_count, "deadline": ENROLL_DEADLINE})


@tournament_bp.route("/api/enrollments")
def api_enrollments():
    db = get_db()
    enrolled_count = db.tournament_enrollments.count_documents({"status": "enrolled"})
    waitlist_count = db.tournament_enrollments.count_documents({"status": "waitlist"})

    players = list(db.tournament_enrollments.find(
        {"status": {"$in": ["enrolled", "waitlist"]}}
    ).sort("enrollAt", 1))

    return jsonify({
        "cap": ENROLL_CAP,
        "enrolledCount": enrolled_count,
        "waitlistCount": waitlist_count,
        "deadline": ENROLL_DEADLINE,
        "deadlineReached": _enroll_deadline_reached(),
        "players": [{
            "battleTag": p.get("battleTag", ""),
            "displayName": p.get("displayName", ""),
            "status": p.get("status"),
            "position": p.get("position"),
            "enrollAt": to_iso_str(p.get("enrollAt")),
        } for p in players],
    })


@tournament_bp.route("/api/admin/enrolled")
def api_admin_enrolled():
    """管理员查看报名列表（含 accountIdLo）"""
    admin_tag = _admin_required()
    if not admin_tag:
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    enrolled_count = db.tournament_enrollments.count_documents({"status": "enrolled"})
    waitlist_count = db.tournament_enrollments.count_documents({"status": "waitlist"})

    players = list(db.tournament_enrollments.find(
        {"status": {"$in": ["enrolled", "waitlist"]}}
    ).sort("enrollAt", 1))

    # 关联 accountIdLo
    for p in players:
        lp = db.league_players.find_one({"battleTag": p.get("battleTag", "")})
        p["accountIdLo"] = str(lp.get("accountIdLo", "")) if lp else ""

    return jsonify({
        "cap": ENROLL_CAP,
        "enrolledCount": enrolled_count,
        "waitlistCount": waitlist_count,
        "deadline": ENROLL_DEADLINE,
        "deadlineReached": _enroll_deadline_reached(),
        "players": [{
            "battleTag": p.get("battleTag", ""),
            "displayName": p.get("displayName", ""),
            "accountIdLo": p.get("accountIdLo", ""),
            "status": p.get("status"),
            "position": p.get("position"),
            "enrollAt": to_iso_str(p.get("enrollAt")),
        } for p in players],
    })
