"""HTML 页面路由"""

import json
import logging
from flask import Blueprint, render_template, redirect, url_for, session

from db import get_db, TOURNAMENT_PHASE, ENROLL_DEADLINE, ENROLL_SLOTS
from auth import is_admin
from data import (get_players, get_completed_matches, get_active_games,
                  get_player, get_player_matches, get_rival_stats,
                  get_match, get_problem_matches)

log = logging.getLogger("bgtracker")
pages = Blueprint("pages", __name__)


@pages.route("/")
def index():
    from routes_tournament import build_bracket_data, _enroll_deadline_reached
    phase = TOURNAMENT_PHASE
    if phase == "auto":
        if ENROLL_DEADLINE and not _enroll_deadline_reached():
            return render_template("enroll.html", enroll_slots=ENROLL_SLOTS)
        data = build_bracket_data()
        return render_template("bracket.html", data_json=json.dumps(data, ensure_ascii=False))
    elif phase == "enroll":
        return render_template("enroll.html", enroll_slots=ENROLL_SLOTS)
    else:  # bracket
        data = build_bracket_data()
        return render_template("bracket.html", data_json=json.dumps(data, ensure_ascii=False))


@pages.route("/player/<path:battle_tag>")
def player_page(battle_tag):
    player = get_player(battle_tag)
    if not player:
        return render_template("404.html", title="选手不存在", emoji="🔍",
            message=f"没有找到「{battle_tag}」的记录，可能还没有注册或打过联赛"), 404
    account_id_lo = player.get("accountIdLo") or None
    player_matches = get_player_matches(battle_tag, account_id_lo=account_id_lo)
    rival_stats = get_rival_stats(battle_tag, account_id_lo=account_id_lo)
    return render_template("player.html", player=player, matches=player_matches, matches_json=player_matches, rival=rival_stats)


@pages.route("/match/<game_uuid>")
def match_page(game_uuid):
    match = get_match(game_uuid)
    if not match:
        return render_template("404.html", title="对局不存在", emoji="⚔️",
            message="这局对局可能从未发生过，或者数据已被清理"), 404
    return render_template("match.html", match=match)


@pages.route("/match/<game_uuid>/edit")
def match_edit_page(game_uuid):
    battle_tag = session.get("battleTag")
    if not battle_tag:
        return redirect(url_for("pages.register_page"))

    match = get_match(game_uuid)
    if not match:
        return render_template("404.html", title="对局不存在", emoji="⚔️",
            message="这局对局可能从未发生过，或者数据已被清理"), 404
    is_problem = any(p.get("placement") is None for p in match.get("players", []))
    if not is_problem:
        return redirect(url_for("pages.match_page", game_uuid=game_uuid))

    admin = is_admin(battle_tag)
    if not admin:
        in_match = any(p.get("battleTag") == battle_tag for p in match.get("players", []))
        if not in_match:
            return redirect(url_for("pages.match_page", game_uuid=game_uuid))

    return render_template("match_edit.html", match=match, is_admin=admin, my_battle_tag=battle_tag)


@pages.route("/register")
def register_page():
    return render_template("register.html")


@pages.route("/problems")
def problems_page():
    matches = get_problem_matches()
    battle_tag = session.get("battleTag", "")
    admin = is_admin(battle_tag) if battle_tag else False
    return render_template("problems.html", matches=matches, is_admin=admin)


@pages.route("/guide")
def guide_page():
    return render_template("guide.html")


@pages.route("/admin")
def admin_page():
    from auth import _admin_required, is_super_admin
    from routes_admin import get_admin_stats
    admin_tag = _admin_required()
    if not admin_tag:
        return render_template("404.html", title="无权限", emoji="🔒", message="需要管理员权限"), 403
    stats = get_admin_stats()
    super_admin = is_super_admin(admin_tag)
    return render_template("admin.html", stats=stats, admin_tag=admin_tag, is_super_admin=super_admin)
