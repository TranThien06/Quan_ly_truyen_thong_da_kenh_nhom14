from fastapi import APIRouter, Depends, Request, Query
from app.common import Input, Id, Pagination, utc, ok
from app.db import transaction, json_value
from app.dependencies import workspace, system_admin
from app.errors import check
from app.events import audit

router = APIRouter(tags=["Quản trị"])


class SettingInput(Input):
    value: int | list[int]


@router.get("/workspaces/{workspace_id}/audit-logs", dependencies=[Depends(workspace("audit.view"))])
def logs(
    workspace_id: Id,
    user_id: Id | None = None,
    action: str | None = None,
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    paging: Pagination = Depends(),
):
    start, end = utc(from_date) if from_date else None, utc(to_date) if to_date else None
    with transaction() as db:
        return ok(
            db.all(
                "SELECT * FROM audit_logs WHERE workspace_id=%s AND (%s IS NULL OR user_id=%s) AND (%s IS NULL OR action=%s) AND (%s IS NULL OR created_at>=%s) AND (%s IS NULL OR created_at<%s) ORDER BY audit_id DESC LIMIT %s OFFSET %s",
                (workspace_id, user_id, user_id, action, action, start, start, end, end, *paging.sql),
            )
        )


@router.get("/admin/settings")
def settings(user=Depends(system_admin)):
    with transaction() as db:
        return ok(db.all("SELECT * FROM system_settings ORDER BY setting_key"))


@router.put("/admin/settings/{key}")
def update(key: str, data: SettingInput, request: Request, user=Depends(system_admin)):
    value = data.value
    valid = (
        key == "media.workspace_quota_bytes"
        and type(value) is int
        and 1024 <= value <= 10**12
        or key == "publish.max_retries"
        and type(value) is int
        and 0 <= value <= 10
        or key == "publish.retry_delays_seconds"
        and isinstance(value, list)
        and 1 <= len(value) <= 10
        and all(type(v) is int and 1 <= v <= 86400 for v in value)
    )
    check(valid, 422, "SETTING_NOT_EDITABLE", "Khóa cấu hình hoặc giá trị không hợp lệ")
    with transaction() as db:
        old = db.one("SELECT * FROM system_settings WHERE setting_key=%s FOR UPDATE", (key,))
        db.execute(
            "INSERT INTO system_settings(setting_key,setting_value,updated_by) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value),updated_by=VALUES(updated_by)",
            (key, json_value(value), user["user_id"]),
        )
        audit(
            db,
            request,
            "system.setting_update",
            "system_setting",
            old={"key": key, "value": old["setting_value"] if old else None},
            new={"key": key, "value": value},
        )
    return ok({"key": key, "value": value})
