from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from pydantic import Field
from app.common import Input, Id, Pagination, now, ok
from app.db import transaction
from app.dependencies import workspace, membership
from app.errors import check
from app.events import audit
from app.modules.channels import account, credentials
from app.social import reply_remote

router = APIRouter(prefix="/workspaces/{workspace_id}/conversations", tags=["Hộp thư"])


class ConversationInput(Input):
    status: Literal["open", "resolved", "spam"] | None = None
    assigned_to: Id | None = None


class ReplyInput(Input):
    content: str = Field(min_length=1, max_length=10000)
    request_id: UUID


def conversation(db, workspace_id, conversation_id, lock=False):
    row = db.one(
        "SELECT * FROM conversations WHERE conversation_id=%s AND workspace_id=%s"
        + (" FOR UPDATE" if lock else ""),
        (conversation_id, workspace_id),
    )
    check(row, 404, "NOT_FOUND", "Không tìm thấy hội thoại")
    return row


@router.get("", dependencies=[Depends(workspace("inbox.reply"))])
def list_conversations(
    workspace_id: Id,
    social_account_id: Id | None = None,
    status: Literal["open", "resolved", "spam"] | None = None,
    assigned_to: Id | None = None,
    paging: Pagination = Depends(),
):
    with transaction() as db:
        return ok(
            db.all(
                "SELECT * FROM conversations WHERE workspace_id=%s AND (%s IS NULL OR social_account_id=%s) AND (%s IS NULL OR status=%s) AND (%s IS NULL OR assigned_to=%s) ORDER BY last_message_at DESC,conversation_id DESC LIMIT %s OFFSET %s",
                (
                    workspace_id,
                    social_account_id,
                    social_account_id,
                    status,
                    status,
                    assigned_to,
                    assigned_to,
                    *paging.sql,
                ),
            )
        )


@router.get("/{conversation_id}/messages", dependencies=[Depends(workspace("inbox.reply"))])
def messages(workspace_id: Id, conversation_id: Id, paging: Pagination = Depends()):
    with transaction() as db:
        conversation(db, workspace_id, conversation_id)
        return ok(
            db.all(
                "SELECT * FROM messages WHERE conversation_id=%s ORDER BY sent_at,message_id LIMIT %s OFFSET %s",
                (conversation_id, *paging.sql),
            )
        )


@router.patch("/{conversation_id}", dependencies=[Depends(workspace("inbox.reply"))])
def update(workspace_id: Id, conversation_id: Id, data: ConversationInput, request: Request):
    with transaction() as db:
        row = conversation(db, workspace_id, conversation_id, True)
        if data.assigned_to:
            membership(db, workspace_id, data.assigned_to)
        assigned = data.assigned_to if "assigned_to" in data.model_fields_set else row["assigned_to"]
        db.execute(
            "UPDATE conversations SET status=%s,assigned_to=%s WHERE conversation_id=%s",
            (data.status or row["status"], assigned, conversation_id),
        )
        audit(
            db,
            request,
            "inbox.update",
            "conversation",
            conversation_id,
            {"status": row["status"], "assigned_to": row["assigned_to"]},
            data.model_dump(exclude_unset=True),
        )
        return ok(conversation(db, workspace_id, conversation_id))


@router.post("/{conversation_id}/messages", status_code=201, dependencies=[Depends(workspace("inbox.reply"))])
def reply(workspace_id: Id, conversation_id: Id, data: ReplyInput, request: Request):
    request_id = str(data.request_id)
    with transaction() as db:
        row = conversation(db, workspace_id, conversation_id, True)
        existing = db.one(
            "SELECT * FROM outbound_requests WHERE conversation_id=%s AND request_id=%s",
            (conversation_id, request_id),
        )
        if existing:
            check(
                existing["content"] == data.content,
                409,
                "IDEMPOTENCY_CONFLICT",
                "request_id đã dùng với nội dung khác",
            )
            check(
                existing["status"] == "sent",
                409,
                "REPLY_UNCONFIRMED",
                "Đang gửi hoặc cần đối soát; không gửi lại với mã khác",
            )
            return ok({"message_id": existing["message_id"]})
        db.insert(
            "outbound_requests",
            {
                "conversation_id": conversation_id,
                "request_id": request_id,
                "user_id": request.state.user["user_id"],
                "content": data.content,
                "status": "sending",
            },
        )
    try:
        with transaction() as db:
            a = account(db, workspace_id, row["social_account_id"])
            check(a["status"] == "connected", 409, "CHANNEL_UNAVAILABLE", "Kênh chưa kết nối")
            auth = credentials(db, a["social_account_id"])
        result = reply_remote(a, row, data.content, auth["access_token"], request_id)
        with transaction() as db:
            message_id = db.insert(
                "messages",
                {
                    "conversation_id": conversation_id,
                    "external_message_id": result["external_message_id"],
                    "direction": "outbound",
                    "sent_by_user_id": request.state.user["user_id"],
                    "sender_name": request.state.user["full_name"],
                    "content": data.content,
                    "sent_at": now(),
                },
            )
            db.execute(
                "UPDATE outbound_requests SET status='sent',message_id=%s WHERE conversation_id=%s AND request_id=%s",
                (message_id, conversation_id, request_id),
            )
            db.execute(
                "UPDATE conversations SET last_message_at=UTC_TIMESTAMP() WHERE conversation_id=%s",
                (conversation_id,),
            )
            audit(db, request, "inbox.reply", "conversation", conversation_id)
        return ok({"message_id": message_id})
    except Exception:
        with transaction() as db:
            db.execute(
                "UPDATE outbound_requests SET status='unknown' WHERE conversation_id=%s AND request_id=%s",
                (conversation_id, request_id),
            )
        raise
