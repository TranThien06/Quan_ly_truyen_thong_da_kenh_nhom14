import hashlib
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import Field
from app.common import Input, Id, Pagination, ok
from app.db import transaction, json_value
from app.dependencies import workspace, membership
from app.errors import check
from app.events import audit, change_status, notify, notify_managers

router = APIRouter(prefix="/workspaces/{workspace_id}/posts", tags=["Bài đăng và duyệt"])
PostStatus = Literal[
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "scheduled",
    "publishing",
    "published",
    "partially_failed",
    "failed",
    "cancelled",
]


class VariantInput(Input):
    social_account_id: Id
    caption: str | None = Field(None, max_length=65000)
    platform_options: dict = Field(default_factory=dict)


class PostInput(Input):
    title: str | None = Field(None, max_length=255)
    base_caption: str | None = Field(None, max_length=65000)
    media_ids: list[Id] = Field(default_factory=list, max_length=20)
    variants: list[VariantInput] = Field(min_length=1, max_length=30)


class ApprovalInput(Input):
    note: str | None = Field(None, max_length=500)


class DecisionInput(Input):
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(None, max_length=1000)


class CommentInput(Input):
    content: str = Field(min_length=1, max_length=10000)
    parent_comment_id: Id | None = None


def load_post(db, workspace_id, post_id, lock=False):
    post = db.one(
        "SELECT * FROM posts WHERE post_id=%s AND workspace_id=%s AND deleted_at IS NULL"
        + (" FOR UPDATE" if lock else ""),
        (post_id, workspace_id),
    )
    check(post, 404, "POST_NOT_FOUND", "Không tìm thấy bài")
    return post


def locked_post(db, request, post_id, permission):
    post = load_post(db, request.state.workspace_id, post_id, True)
    membership(db, post["workspace_id"], request.state.user["user_id"], permission)
    return post


def save_content(db, workspace_id, post_id, data):
    accounts = [v.social_account_id for v in data.variants]
    check(len(accounts) == len(set(accounts)), 422, "DUPLICATE_CHANNEL", "Kênh bị lặp")
    check(len(data.media_ids) == len(set(data.media_ids)), 422, "DUPLICATE_MEDIA", "Media bị lặp")
    for account_id in accounts:
        check(
            db.one(
                "SELECT 1 FROM social_accounts WHERE social_account_id=%s AND workspace_id=%s",
                (account_id, workspace_id),
            ),
            422,
            "INVALID_CHANNEL",
            "Kênh không thuộc workspace",
        )
    for media_id in data.media_ids:
        check(
            db.one(
                "SELECT 1 FROM media_assets WHERE media_id=%s AND workspace_id=%s AND deleted_at IS NULL",
                (media_id, workspace_id),
            ),
            422,
            "INVALID_MEDIA",
            "Media không thuộc workspace hoặc đã xóa",
        )
    for old in db.all("SELECT * FROM post_variants WHERE post_id=%s", (post_id,)):
        if str(old["social_account_id"]) not in accounts:
            used = db.one(
                "SELECT 1 FROM publish_jobs WHERE variant_id=%s UNION SELECT 1 FROM schedules WHERE variant_id=%s LIMIT 1",
                (old["variant_id"], old["variant_id"]),
            )
            check(not used, 409, "VARIANT_HAS_HISTORY", "Không xóa kênh đã có lịch sử; hãy nhân bản bài")
            db.execute("DELETE FROM post_variants WHERE variant_id=%s", (old["variant_id"],))
    for variant in data.variants:
        db.execute(
            """INSERT INTO post_variants(post_id,workspace_id,social_account_id,caption,platform_options) VALUES (%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE caption=VALUES(caption),platform_options=VALUES(platform_options),status='draft'""",
            (
                post_id,
                workspace_id,
                variant.social_account_id,
                variant.caption,
                json_value(variant.platform_options),
            ),
        )
    db.execute("DELETE FROM post_media WHERE post_id=%s", (post_id,))
    for index, media_id in enumerate(data.media_ids):
        db.insert(
            "post_media",
            {"post_id": post_id, "workspace_id": workspace_id, "media_id": media_id, "sort_order": index},
        )


def post_detail(db, workspace_id, post_id):
    post = load_post(db, workspace_id, post_id)
    post["variants"] = db.all(
        "SELECT v.*,a.account_name,p.code platform FROM post_variants v JOIN social_accounts a ON a.social_account_id=v.social_account_id JOIN platforms p ON p.platform_id=a.platform_id WHERE v.post_id=%s ORDER BY v.variant_id",
        (post_id,),
    )
    post["media"] = db.all(
        "SELECT m.*,pm.sort_order FROM post_media pm JOIN media_assets m ON m.media_id=pm.media_id WHERE pm.post_id=%s ORDER BY pm.sort_order",
        (post_id,),
    )
    return post


def create_post(db, workspace_id, data, request, use_idempotency=True):
    user_id = request.state.user["user_id"]
    membership(db, workspace_id, user_id, "post.create")
    key = request.headers.get("idempotency-key") if use_idempotency else None
    if key:
        import re
        import json

        check(
            re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key),
            422,
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key không hợp lệ",
        )
        original = json.loads(request._body) if getattr(request, "_body", None) else data.model_dump()
        digest = hashlib.sha256(json_value(original).encode()).hexdigest()
        db.execute(
            "INSERT IGNORE INTO api_idempotency(workspace_id,user_id,operation,request_key,body_hash) VALUES (%s,%s,'post.create',%s,%s)",
            (workspace_id, user_id, key, digest),
        )
        item = db.one(
            "SELECT * FROM api_idempotency WHERE workspace_id=%s AND user_id=%s AND operation='post.create' AND request_key=%s FOR UPDATE",
            (workspace_id, user_id, key),
        )
        check(item["body_hash"] == digest, 409, "IDEMPOTENCY_CONFLICT", "Key đã dùng với nội dung khác")
        if item["resource_id"]:
            return post_detail(db, workspace_id, item["resource_id"])
    post_id = db.insert(
        "posts",
        {
            "workspace_id": workspace_id,
            "created_by": user_id,
            "title": data.title,
            "base_caption": data.base_caption,
        },
    )
    save_content(db, workspace_id, post_id, data)
    db.insert("post_status_history", {"post_id": post_id, "to_status": "draft", "changed_by": user_id})
    audit(db, request, "post.create", "post", post_id)
    if key:
        db.execute(
            "UPDATE api_idempotency SET resource_id=%s WHERE workspace_id=%s AND user_id=%s AND operation='post.create' AND request_key=%s",
            (post_id, workspace_id, user_id, key),
        )
    return post_detail(db, workspace_id, post_id)


@router.get("", dependencies=[Depends(workspace())])
def list_posts(
    workspace_id: Id, status: PostStatus | None = None, q: str | None = None, paging: Pagination = Depends()
):
    with transaction() as db:
        return ok(
            db.all(
                "SELECT * FROM posts WHERE workspace_id=%s AND deleted_at IS NULL AND (%s IS NULL OR status=%s) AND (%s IS NULL OR title LIKE %s) ORDER BY created_at DESC,post_id DESC LIMIT %s OFFSET %s",
                (workspace_id, status, status, q, f"%{q or ''}%", *paging.sql),
            )
        )


@router.post("", status_code=201, dependencies=[Depends(workspace("post.create"))])
def create(workspace_id: Id, data: PostInput, request: Request):
    with transaction() as db:
        return ok(create_post(db, workspace_id, data, request))


@router.get("/{post_id}", dependencies=[Depends(workspace())])
def detail(workspace_id: Id, post_id: Id):
    with transaction() as db:
        return ok(post_detail(db, workspace_id, post_id))


@router.put("/{post_id}", dependencies=[Depends(workspace("post.edit"))])
def update(workspace_id: Id, post_id: Id, data: PostInput, request: Request):
    with transaction() as db:
        post = locked_post(db, request, post_id, "post.edit")
        check(
            post["status"] in ("draft", "rejected", "approved"),
            409,
            "POST_NOT_EDITABLE",
            "Bài đang chờ duyệt/có lịch/đã đăng không được sửa",
        )
        check(
            not db.one(
                "SELECT 1 FROM schedules s JOIN post_variants v ON v.variant_id=s.variant_id WHERE v.post_id=%s AND s.status IN ('pending','queued')",
                (post_id,),
            ),
            409,
            "ACTIVE_SCHEDULE",
            "Hủy lịch trước khi sửa",
        )
        db.execute(
            "UPDATE posts SET title=%s,base_caption=%s WHERE post_id=%s",
            (data.title, data.base_caption, post_id),
        )
        save_content(db, workspace_id, post_id, data)
        change_status(
            db, post, "draft", request.state.user["user_id"], "Sửa nội dung; cần duyệt lại nếu bật duyệt"
        )
        audit(db, request, "post.edit", "post", post_id)
        return ok(post_detail(db, workspace_id, post_id))


@router.delete("/{post_id}", dependencies=[Depends(workspace("post.edit"))])
def delete(workspace_id: Id, post_id: Id, request: Request):
    with transaction() as db:
        post = locked_post(db, request, post_id, "post.edit")
        check(
            post["status"] not in ("scheduled", "publishing", "pending_approval"),
            409,
            "POST_BUSY",
            "Hủy lịch hoặc rút duyệt trước",
        )
        db.execute("UPDATE posts SET deleted_at=UTC_TIMESTAMP() WHERE post_id=%s", (post_id,))
        audit(db, request, "post.delete", "post", post_id)
    return ok({"message": "Đã xóa mềm"})


@router.post("/{post_id}/duplicate", status_code=201, dependencies=[Depends(workspace("post.create"))])
def duplicate(workspace_id: Id, post_id: Id, request: Request):
    with transaction() as db:
        post = post_detail(db, workspace_id, post_id)
        data = PostInput(
            title=post["title"],
            base_caption=post["base_caption"],
            media_ids=[str(m["media_id"]) for m in post["media"] if not m["deleted_at"]],
            variants=[
                VariantInput(
                    social_account_id=str(v["social_account_id"]),
                    caption=v["caption"],
                    platform_options=v["platform_options"] or {},
                )
                for v in post["variants"]
            ],
        )
        return ok(create_post(db, workspace_id, data, request, use_idempotency=False))


@router.get("/{post_id}/preview", dependencies=[Depends(workspace())])
def preview(workspace_id: Id, post_id: Id):
    with transaction() as db:
        post = post_detail(db, workspace_id, post_id)
        return ok(
            [
                {
                    **{k: v[k] for k in ("variant_id", "platform", "account_name", "platform_options")},
                    "caption": v["caption"] if v["caption"] is not None else post["base_caption"],
                    "media": post["media"],
                }
                for v in post["variants"]
            ]
        )


@router.get("/{post_id}/history", dependencies=[Depends(workspace())])
def history(workspace_id: Id, post_id: Id, paging: Pagination = Depends()):
    with transaction() as db:
        load_post(db, workspace_id, post_id)
        return ok(
            db.all(
                "SELECT * FROM post_status_history WHERE post_id=%s ORDER BY history_id LIMIT %s OFFSET %s",
                (post_id, *paging.sql),
            )
        )


@router.get("/{post_id}/approvals", dependencies=[Depends(workspace())])
def approvals(workspace_id: Id, post_id: Id, paging: Pagination = Depends()):
    with transaction() as db:
        load_post(db, workspace_id, post_id)
        return ok(
            db.all(
                "SELECT * FROM approval_requests WHERE post_id=%s ORDER BY approval_id DESC LIMIT %s OFFSET %s",
                (post_id, *paging.sql),
            )
        )


@router.post("/{post_id}/approvals", status_code=201, dependencies=[Depends(workspace("post.edit"))])
def submit_approval(workspace_id: Id, post_id: Id, data: ApprovalInput, request: Request):
    with transaction() as db:
        post = locked_post(db, request, post_id, "post.edit")
        check(
            post["status"] in ("draft", "rejected"),
            409,
            "INVALID_STATE",
            "Chỉ gửi duyệt bài nháp hoặc bị từ chối",
        )
        approval_id = db.insert(
            "approval_requests",
            {"post_id": post_id, "requested_by": request.state.user["user_id"], "request_note": data.note},
        )
        change_status(db, post, "pending_approval", request.state.user["user_id"])
        notify_managers(db, workspace_id, "approval_requested", "Có bài cần duyệt", "post", post_id)
        audit(db, request, "post.submit_approval", "post", post_id)
    return ok({"approval_id": approval_id})


@router.post("/{post_id}/approvals/decision", dependencies=[Depends(workspace("post.approve"))])
def decide(workspace_id: Id, post_id: Id, data: DecisionInput, request: Request):
    check(
        data.decision != "rejected" or (data.reason and data.reason.strip()),
        422,
        "REASON_REQUIRED",
        "Từ chối cần lý do",
    )
    with transaction() as db:
        post = locked_post(db, request, post_id, "post.approve")
        item = db.one(
            "SELECT * FROM approval_requests WHERE post_id=%s AND status='pending' FOR UPDATE", (post_id,)
        )
        check(
            item and post["status"] == "pending_approval",
            409,
            "NO_PENDING_APPROVAL",
            "Không có yêu cầu đang chờ",
        )
        setting = db.one(
            "SELECT setting_value FROM workspace_settings WHERE workspace_id=%s AND setting_key='allow_self_approval'",
            (workspace_id,),
        )
        check(
            item["requested_by"] != request.state.user["user_id"]
            or (setting and setting["setting_value"] is True),
            403,
            "SELF_APPROVAL_DISABLED",
            "Không được tự duyệt",
        )
        db.execute(
            "UPDATE approval_requests SET status=%s,resolved_by=%s,reason=%s,resolved_at=UTC_TIMESTAMP() WHERE approval_id=%s",
            (data.decision, request.state.user["user_id"], data.reason, item["approval_id"]),
        )
        change_status(db, post, data.decision, request.state.user["user_id"], data.reason)
        notify(
            db,
            workspace_id,
            item["requested_by"],
            "approval_result",
            f"Bài đã {data.decision}",
            "post",
            post_id,
        )
        audit(db, request, "post.approval", "post", post_id, new={"decision": data.decision})
    return ok({"status": data.decision})


@router.post("/{post_id}/approvals/withdraw", dependencies=[Depends(workspace("post.edit"))])
def withdraw(workspace_id: Id, post_id: Id, request: Request):
    with transaction() as db:
        post = locked_post(db, request, post_id, "post.edit")
        check(post["status"] == "pending_approval", 409, "INVALID_STATE", "Bài không chờ duyệt")
        db.execute(
            "UPDATE approval_requests SET status='cancelled',resolved_at=UTC_TIMESTAMP() WHERE post_id=%s AND status='pending'",
            (post_id,),
        )
        change_status(db, post, "draft", request.state.user["user_id"], "Rút duyệt")
    return ok({"status": "draft"})


@router.get("/{post_id}/comments", dependencies=[Depends(workspace())])
def comments(workspace_id: Id, post_id: Id, paging: Pagination = Depends()):
    with transaction() as db:
        load_post(db, workspace_id, post_id)
        return ok(
            db.all(
                "SELECT comment_id,post_id,user_id,parent_comment_id,content,created_at FROM post_comments WHERE post_id=%s AND deleted_at IS NULL ORDER BY comment_id LIMIT %s OFFSET %s",
                (post_id, *paging.sql),
            )
        )


@router.post("/{post_id}/comments", status_code=201, dependencies=[Depends(workspace("post.edit"))])
def comment(workspace_id: Id, post_id: Id, data: CommentInput, request: Request):
    with transaction() as db:
        load_post(db, workspace_id, post_id, True)
        if data.parent_comment_id:
            check(
                db.one(
                    "SELECT 1 FROM post_comments WHERE comment_id=%s AND post_id=%s AND deleted_at IS NULL",
                    (data.parent_comment_id, post_id),
                ),
                422,
                "INVALID_PARENT",
                "Bình luận cha không thuộc bài",
            )
        return ok(
            {
                "comment_id": db.insert(
                    "post_comments",
                    {"post_id": post_id, "user_id": request.state.user["user_id"], **data.model_dump()},
                )
            }
        )


@router.delete("/{post_id}/comments/{comment_id}", dependencies=[Depends(workspace())])
def delete_comment(workspace_id: Id, post_id: Id, comment_id: Id, request: Request):
    with transaction() as db:
        load_post(db, workspace_id, post_id, True)
        row = db.one(
            "SELECT * FROM post_comments WHERE comment_id=%s AND post_id=%s AND deleted_at IS NULL",
            (comment_id, post_id),
        )
        check(row, 404, "NOT_FOUND", "Không tìm thấy bình luận")
        check(
            row["user_id"] == request.state.user["user_id"] or request.state.member["role_code"] == "admin",
            403,
            "FORBIDDEN",
            "Chỉ tác giả hoặc Admin được xóa",
        )
        db.execute("UPDATE post_comments SET deleted_at=UTC_TIMESTAMP() WHERE comment_id=%s", (comment_id,))
    return ok({"message": "Đã xóa"})
