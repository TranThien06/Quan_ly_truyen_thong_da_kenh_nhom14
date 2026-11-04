import csv
import logging
import os
import uuid
from xml.sax.saxutils import escape
from openpyxl import Workbook
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from app.config import settings
from app.db import transaction
from app.dependencies import membership
from app.events import notify
from app.modules.reports import overview_data, post_data


def safe_cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def write_report(path, kind, rows):
    rows = rows or [{"message": "No data"}]
    columns = [key for key in rows[0] if key != "extra_metrics"]
    if kind == "csv":
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(columns)
            writer.writerows([[safe_cell(row.get(c)) for c in columns] for row in rows])
    elif kind == "xlsx":
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Report")
        sheet.append(columns)
        for row in rows:
            sheet.append([safe_cell(row.get(c)) for c in columns])
        workbook.save(path)
    else:
        font = os.getenv("REPORT_FONT_PATH") or (
            "C:/Windows/Fonts/arial.ttf"
            if os.name == "nt"
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        )
        pdfmetrics.registerFont(TTFont("ReportVietnamese", font))
        style = getSampleStyleSheet()["Normal"]
        style.fontName = "ReportVietnamese"
        content = [Paragraph("Báo cáo truyền thông đa kênh", style), Spacer(1, 12)]
        for row in rows:
            lines = [
                f"{escape(c)}: {escape(str(row[c])) if row.get(c) is not None else 'N/A'}" for c in columns
            ]
            content.extend([Paragraph("<br/>".join(lines), style), Spacer(1, 12)])
        SimpleDocTemplate(str(path)).build(content)


def export_one_report():
    with transaction() as db:
        db.execute(
            "UPDATE report_exports SET status='pending',claim_token=NULL WHERE status='running' AND lease_expires_at<UTC_TIMESTAMP()"
        )
        item = db.one(
            "SELECT * FROM report_exports WHERE status='pending' ORDER BY export_id LIMIT 1 FOR UPDATE SKIP LOCKED"
        )
        if not item:
            return False
        claim = str(uuid.uuid4())
        db.execute(
            "UPDATE report_exports SET status='running',claim_token=%s,lease_expires_at=DATE_ADD(UTC_TIMESTAMP(),INTERVAL 5 MINUTE) WHERE export_id=%s",
            (claim, item["export_id"]),
        )
    temporary = None
    try:
        with transaction() as db:
            membership(db, item["workspace_id"], item["created_by"], "report.export")
            rows = (
                overview_data(db, item["workspace_id"], item["params"])
                if item["report_type"] == "channel_overview"
                else post_data(db, item["workspace_id"], item["params"])
            )
        directory = settings.storage / "reports"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / f"{item['export_id']}.{item['file_format']}"
        temporary = directory / f"{item['export_id']}.{claim}.tmp"
        write_report(temporary, item["file_format"], rows)
        with transaction() as db:
            locked = db.one(
                "SELECT * FROM report_exports WHERE export_id=%s AND claim_token=%s AND status='running' FOR UPDATE",
                (item["export_id"], claim),
            )
            if not locked:
                temporary.unlink(missing_ok=True)
                return True
            temporary.replace(output)
            url = f"{settings.app_url}/api/v1/workspaces/{item['workspace_id']}/reports/exports/{item['export_id']}/download"
            db.execute(
                "UPDATE report_exports SET status='done',file_url=%s,finished_at=UTC_TIMESTAMP(),lease_expires_at=NULL WHERE export_id=%s",
                (url, item["export_id"]),
            )
            notify(
                db,
                item["workspace_id"],
                item["created_by"],
                "report_ready",
                "Báo cáo đã sẵn sàng",
                "report_export",
                item["export_id"],
            )
    except Exception as error:
        with transaction() as db:
            db.execute(
                "UPDATE report_exports SET status='failed',finished_at=UTC_TIMESTAMP(),lease_expires_at=NULL WHERE export_id=%s AND claim_token=%s",
                (item["export_id"], claim),
            )
        if temporary:
            temporary.unlink(missing_ok=True)
        logging.error("task=report code=%s", type(error).__name__)
    return True
