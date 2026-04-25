#!/usr/bin/env python3
"""导出报名名单

用法:
  python export_enrollments.py --csv     # 导出 CSV
  python export_enrollments.py --excel   # 导出 Excel (需要 openpyxl)

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


def export_csv(rows):
    ts = datetime.now().strftime("%Y%m%d%H%M")
    filename = f"{ts}_报名名单.csv"
    with open(filename, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["序号", "BattleTag", "AccountIdLo", "报名时间", "状态"])
        for i, r in enumerate(rows, 1):
            status = "替补" if r.get("status") == "waitlist" else ""
            writer.writerow([
                i,
                r.get("battleTag", ""),
                r.get("accountIdLo", ""),
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
    headers = ["序号", "BattleTag", "AccountIdLo", "报名时间", "状态"]
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2a2a4a", end_color="2a2a4a", fill_type="solid")
    thin_border = Border(
        bottom=Side(style="thin", color="cccccc")
    )

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    # 数据
    for i, r in enumerate(rows, 2):
        seq = i - 1
        status = "替补" if r.get("status") == "waitlist" else ""
        ws.cell(row=i, column=1, value=seq).alignment = Alignment(horizontal="center")
        ws.cell(row=i, column=2, value=r.get("battleTag", ""))
        ws.cell(row=i, column=3, value=r.get("accountIdLo", ""))
        ws.cell(row=i, column=4, value=r.get("enrollAt", ""))
        cell_status = ws.cell(row=i, column=5, value=status)
        if status == "替补":
            cell_status.font = Font(color="e2b714")
        for col in range(1, 6):
            ws.cell(row=i, column=col).border = thin_border

    # 列宽
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 24
    ws.column_dimensions["E"].width = 8

    wb.save(filename)
    print(f"已导出: {filename} ({len(rows)} 人)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("--csv", "--excel"):
        print("用法: python export_enrollments.py --csv | --excel")
        sys.exit(1)

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
