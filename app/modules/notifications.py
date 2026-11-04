from fastapi import APIRouter, Depends
from pydantic import Field
from app.common import Input, Id, Pagination, ok
from app.db import transaction
from app.dependencies import current_user, workspace
from app.errors import check

router = APIRouter(tags=["Thông báo"])
VISIBLE = """n.user_id=%s AND n.in_app_visible=1 AND (n.workspace_id IS NULL OR EXISTS
    (SELECT 1 FROM workspace_members m JOIN workspaces w ON w.workspace_id=m.workspace_id
    WHERE m.workspace_id=n.workspace_id AND m.user_id=n.user_id AND m.status='active' AND w.deleted_at IS NULL))"""


class ReadInput(Input):
    all: bool = False
    notification_ids: list[Id] = Field(default_factory=list, max_length=100)


class NotificationInput(Input):
    type: str = Field(min_length=1, max_length=50)
    in_app_enabled: bool
    email_enabled: bool


@router.get("/me/notifications")
def notifications(user=Depends(current_user), paging: Pagination = Depends()):
    with transaction() as db:
        return ok(
            db.all(
                f"SELECT n.notification_id,n.workspace_id,n.type,n.title,n.body,n.entity_type,n.entity_id,n.is_read,n.read_at,n.created_at FROM notifications n WHERE {VISIBLE} ORDER BY n.notification_id DESC LIMIT %s OFFSET %s",
                (user["user_id"], *paging.sql),
            )
        )


@router.get("/me/notifications/unread-count")
def unread(user=Depends(current_user)):
    with transaction() as db:
        return ok(
            db.one(
                f"SELECT COUNT(*) count FROM notifications n WHERE {VISIBLE} AND n.is_read=0",
                (user["user_id"],),
            )
        )


@router.post("/me/notifications/read")
def mark_read(data: ReadInput, user=Depends(current_user)):
    check(data.all or data.notification_ids, 422, "INVALID_SELECTION", "Chọn thông báo hoặc all=true")
    with transaction() as db:
        if data.all:
            db.execute(
                f"UPDATE notifications n SET n.is_read=1,n.read_at=UTC_TIMESTAMP() WHERE {VISIBLE}",
                (user["user_id"],),
            )
        else:
            for notification_id in data.notification_ids:
                db.execute(
                    f"UPDATE notifications n SET n.is_read=1,n.read_at=UTC_TIMESTAMP() WHERE {VISIBLE} AND n.notification_id=%s",
                    (user["user_id"], notification_id),
                )
    return unread(user)


@router.get("/workspaces/{workspace_id}/notification-settings", dependencies=[Depends(workspace())])
def notification_settings(workspace_id: Id, user=Depends(current_user)):
    with transaction() as db:
        return ok(
            db.all(
                "SELECT type,in_app_enabled,email_enabled FROM notification_settings WHERE workspace_id=%s AND user_id=%s",
                (workspace_id, user["user_id"]),
            )
        )


@router.put("/workspaces/{workspace_id}/notification-settings", dependencies=[Depends(workspace())])
def update_settings(workspace_id: Id, data: NotificationInput, user=Depends(current_user)):
    with transaction() as db:
        db.execute(
            "INSERT INTO notification_settings(user_id,workspace_id,type,in_app_enabled,email_enabled) VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE in_app_enabled=VALUES(in_app_enabled),email_enabled=VALUES(email_enabled)",
            (user["user_id"], workspace_id, data.type, int(data.in_app_enabled), int(data.email_enabled)),
        )
    return notification_settings(workspace_id, user)
