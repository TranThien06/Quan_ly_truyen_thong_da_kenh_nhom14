from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field
from app.common import Input, Id, Pagination, ok
from app.config import settings
from app.db import transaction, json_value
from app.dependencies import workspace
from app.errors import check
from app.events import audit

router = APIRouter(prefix="/workspaces/{workspace_id}/reports", tags=["Báo cáo"])
Metric = Literal["impressions", "reach", "views", "likes", "comments", "shares", "saves", "clicks"]


class ExportInput(Input):
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    social_account_id: Id | None = None
    report_type: Literal["channel_overview", "post_performance"]
    file_format: Literal["csv", "xlsx", "pdf"]
    metric: Metric = "likes"


def valid_range(start, end):
    start = date.fromisoformat(start) if isinstance(start, str) else start
    end = date.fromisoformat(end) if isinstance(end, str) else end
    check(start <= end and (end - start).days <= 366, 422, "INVALID_RANGE", "Khoảng ngày tối đa 367 ngày")
    return start, end


def overview_data(db, workspace_id, params):
    start, end = valid_range(params["from"], params["to"])
    account_id = params.get("social_account_id")
    return db.all(
        "SELECT m.*,a.account_name,p.code platform FROM account_daily_metrics m JOIN social_accounts a ON a.social_account_id=m.social_account_id JOIN platforms p ON p.platform_id=a.platform_id WHERE a.workspace_id=%s AND m.metric_date BETWEEN %s AND %s AND (%s IS NULL OR a.social_account_id=%s) ORDER BY m.metric_date,a.social_account_id LIMIT 10000",
        (workspace_id, start, end, account_id, account_id),
    )


def post_data(db, workspace_id, params):
    start, end = valid_range(params["from"], params["to"])
    account_id = params.get("social_account_id")
    metric = params.get("metric", "likes")
    check(
        metric in ("impressions", "reach", "views", "likes", "comments", "shares", "saves", "clicks"),
        422,
        "INVALID_METRIC",
        "Chỉ số không hợp lệ",
    )
    return db.all(
        f"""SELECT p.post_id,p.title,v.variant_id,v.social_account_id,v.external_post_url,v.published_at,
        s.snapshot_id,s.collected_at,s.impressions,s.reach,s.views,s.likes,s.comments,s.shares,s.saves,s.clicks
        FROM post_variants v JOIN posts p ON p.post_id=v.post_id LEFT JOIN post_metric_snapshots s ON s.snapshot_id=
        (SELECT s2.snapshot_id FROM post_metric_snapshots s2 WHERE s2.variant_id=v.variant_id ORDER BY s2.collected_at DESC LIMIT 1)
        WHERE v.workspace_id=%s AND v.status='published' AND v.published_at>=%s AND v.published_at<DATE_ADD(%s,INTERVAL 1 DAY)
        AND (%s IS NULL OR v.social_account_id=%s) ORDER BY s.{metric} DESC,v.variant_id DESC LIMIT 1000""",
        (workspace_id, start, end, account_id, account_id),
    )


@router.get("/overview", dependencies=[Depends(workspace("report.view"))])
def overview(
    workspace_id: Id,
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    social_account_id: Id | None = None,
):
    with transaction() as db:
        return ok(
            overview_data(
                db, workspace_id, {"from": from_date, "to": to_date, "social_account_id": social_account_id}
            )
        )


@router.get("/posts", dependencies=[Depends(workspace("report.view"))])
def posts(
    workspace_id: Id,
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    social_account_id: Id | None = None,
    metric: Metric = "likes",
):
    with transaction() as db:
        return ok(
            post_data(
                db,
                workspace_id,
                {"from": from_date, "to": to_date, "social_account_id": social_account_id, "metric": metric},
            )
        )


@router.post("/exports", status_code=202, dependencies=[Depends(workspace("report.export"))])
def create_export(workspace_id: Id, data: ExportInput, request: Request):
    valid_range(data.from_date, data.to_date)
    with transaction() as db:
        params = data.model_dump(by_alias=True, exclude={"report_type", "file_format"}, mode="json")
        export_id = db.insert(
            "report_exports",
            {
                "workspace_id": workspace_id,
                "created_by": request.state.user["user_id"],
                "report_type": data.report_type,
                "file_format": data.file_format,
                "params": json_value(params),
            },
        )
        audit(db, request, "report.export", "report_export", export_id, new={"format": data.file_format})
    return ok({"export_id": export_id, "status": "pending"})


@router.get("/exports", dependencies=[Depends(workspace("report.export"))])
def exports(workspace_id: Id, paging: Pagination = Depends()):
    with transaction() as db:
        return ok(
            db.all(
                "SELECT * FROM report_exports WHERE workspace_id=%s ORDER BY export_id DESC LIMIT %s OFFSET %s",
                (workspace_id, *paging.sql),
            )
        )


def export_detail(db, workspace_id, export_id):
    row = db.one(
        "SELECT * FROM report_exports WHERE export_id=%s AND workspace_id=%s", (export_id, workspace_id)
    )
    check(row, 404, "NOT_FOUND", "Không tìm thấy báo cáo")
    return row


@router.get("/exports/{export_id}", dependencies=[Depends(workspace("report.export"))])
def detail(workspace_id: Id, export_id: Id):
    with transaction() as db:
        return ok(export_detail(db, workspace_id, export_id))


@router.get("/exports/{export_id}/download", dependencies=[Depends(workspace("report.export"))])
def download(workspace_id: Id, export_id: Id):
    with transaction() as db:
        row = export_detail(db, workspace_id, export_id)
    check(row["status"] == "done", 409, "EXPORT_NOT_READY", "Báo cáo chưa hoàn tất")
    return FileResponse(
        settings.storage / "reports" / f"{export_id}.{row['file_format']}",
        filename=f"report-{export_id}.{row['file_format']}",
    )
