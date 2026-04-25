#!/usr/bin/env python3
"""导出报名名单

用法:
  python export_enrollments.py --csv           # 导出 CSV
  python export_enrollments.py --excel         # 导出 Excel (需要 openpyxl)
  python export_enrollments.py --missing-lo    # 查找 Lo 为空的玩家

环境变量:
  MONGO_URL  MongoDB 地址 (默认 mongodb://mongo:27017)
  DB_NAME    数据库名 (默认 hearthstone)
"""

import os
import sys
import csv
from datetime import datetime

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
DB_NAME = os.environ.get("DB_NAME", "hearthstone")


def get_enrollments():
    from pymongo import MongoClient
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    return list(db.tournament_enrollments.find(
        {"status": {"$in": ["enrolled", "waitlist"]}},
        {"_id": 0, "battleTag": 1, "accountIdLo": 1, "status": 1, "position": 1, "enrollAt": 1}
    ).sort("position", 1))


def check_missing_lo():
    """查找 Lo 为空的玩家，交叉比对 league_players 和 player_records"""
    from pymongo import MongoClient
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]

    enrollments = get_enrollments()
    if not enrollments:
        print("暂无报名数据")
        return

    missing = []
    for r in enrollments:
        lo = str(r.get("accountIdLo", "")).strip()
        if not lo or lo == "None":
            missing.append(r)

    if not missing:
        print(f"✅ 全部 {len(enrollments)} 人都有 Lo，没有问题")
        return

    print(f"⚠️  共 {len(missing)} 人 Lo 为空（总计 {len(enrollments)} 人报名）\n")
    print(f"{'序号':>4}  {'BattleTag':<30}  {'league_players Lo':<18}  {'player_records Lo':<18}  {'状态'}")
    print("-" * 100)

    for i, r in enumerate(missing, 1):
        tag = r.get("battleTag", "")
        status = "替补" if r.get("status") == "waitlist" else "正选"

        # 查 league_players 是否有 Lo
        lp = db.league_players.find_one({"battleTag": tag}, {"accountIdLo": 1})
        lp_lo = str(lp.get("accountIdLo", "")) if lp else "-"

        # 查 player_records 是否有 Lo
        pr = db.player_records.find_one({"playerId": tag}, {"accountIdLo": 1})
        pr_lo = str(pr.get("accountIdLo", "")) if pr else "-"

        print(f"{i:>4}  {tag:<30}  {lp_lo:<18}  {pr_lo:<18}  {status}")

    print()
    print("修复方法:")
    print("  1. 玩家用 bg_tool/HDT 插件打一局，插件会自动上报 accountIdLo")
    print("  2. 管理员手动补 Lo: db.tournament_enrollments.updateOne({battleTag: 'xxx'}, {$set: {accountIdLo: '12345'}})")
    print("  3. 同步到 league_players: db.league_players.updateOne({battleTag: 'xxx'}, {$set: {accountIdLo: '12345'}})")


def export_csv(rows):
    ts = datetime.now().strftime("%Y%m%d%H%M")
    filename = f"{ts}_报名名单.csv"
    with open(filename, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["序号", "BattleTag", "AccountIdLo", "Lo为空", "报名时间", "状态"])
        for i, r in enumerate(rows, 1):
            status = "替补" if r.get("status") == "waitlist" else ""
            lo = str(r.get("accountIdLo", "")).strip()
            lo_empty = "⚠️ 是" if (not lo or lo == "None") else ""
            writer.writerow([
                i,
                r.get("battleTag", ""),
                r.get("accountIdLo", ""),
                lo_empty,
                r.get("enrollAt", ""),
                status,
            ])
    print(f"已导出: {filename} ({len(rows)} 人)")


def export_excel(rows):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        print("Excel 模式需要 openpyxl: pip install openpyxl")
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d%H%M")
    filename = f"{ts}_报名名单.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "报名名单"

    # 表头
    headers = ["序号", "BattleTag", "AccountIdLo", "Lo为空", "报名时间", "状态"]
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2a2a4a", end_color="2a2a4a", fill_type="solid")
    thin_border = Border(
        bottom=Side(style="thin", color="cccccc")
    )
    warn_fill = PatternFill(start_color="ffcccc", end_color="ffcccc", fill_type="solid")

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    # 数据
    for i, r in enumerate(rows, 2):
        seq = i - 1
        status = "替补" if r.get("status") == "waitlist" else ""
        lo = str(r.get("accountIdLo", "")).strip()
        lo_empty = "⚠️ 是" if (not lo or lo == "None") else ""

        ws.cell(row=i, column=1, value=seq).alignment = Alignment(horizontal="center")
        ws.cell(row=i, column=2, value=r.get("battleTag", ""))
        ws.cell(row=i, column=3, value=r.get("accountIdLo", ""))
        cell_warn = ws.cell(row=i, column=4, value=lo_empty)
        ws.cell(row=i, column=5, value=r.get("enrollAt", ""))
        cell_status = ws.cell(row=i, column=6, value=status)
        if status == "替补":
            cell_status.font = Font(color="e2b714")
        if lo_empty:
            cell_warn.font = Font(bold=True, color="cc0000")
            # 整行高亮
            for col in range(1, 7):
                ws.cell(row=i, column=col).fill = warn_fill
        for col in range(1, 7):
            ws.cell(row=i, column=col).border = thin_border

    # 列宽
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 10
    ws.column_dimensions["E"].width = 24
    ws.column_dimensions["F"].width = 8

    wb.save(filename)
    print(f"已导出: {filename} ({len(rows)} 人)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("--csv", "--excel", "--missing-lo"):
        print("用法: python export_enrollments.py --csv | --excel | --missing-lo")
        sys.exit(1)

    if sys.argv[1] == "--missing-lo":
        check_missing_lo()
        return

    rows = get_enrollments()
    if not rows:
        print("暂无报名数据")
        return

    if sys.argv[1] == "--csv":
        export_csv(rows)
    else:
        export_excel(rows)


if __name__ == "__main__":
    main()
