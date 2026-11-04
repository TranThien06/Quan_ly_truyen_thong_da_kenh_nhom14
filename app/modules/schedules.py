from fastapi import APIRouter, Depends, Request, Query
from typing import Literal
from pydantic import Field
from app.common import Input, Id, Pagination, now, utc, ok
from app.db import transaction
from app.dependencies import workspace
from app.errors import check
from app.events import audit, change_status
from app.modules.posts import locked_post
from app.modules.channels import account, credentials
from app.publishing import validate_publish, enqueue, variant_context, aggregate
from app.social import verify_remote_post

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["Đăng bài và lịch"])


class ScheduleInput(Input):
    scheduled_at: str


class ScheduleUpdate(Input):
    scheduled_at: str | None = None
    cancel: bool | None = None


class ReconcileInput(Input):
    external_post_id: str = Field(min_length=1, max_length=100)


@router.post("/posts/{post_id}/publish", status_code=202, dependencies=[Depends(workspace("post.publish"))])
def publish(workspace_id: Id, post_id: Id, request: Request):
    with transaction() as db:
        post = locked_post(db, request, post_id, "post.publish")
        check(
            post["status"] not in ("pending_approval", "rejected", "cancelled"),
            409,
            "INVALID_STATE",
            "Bài chưa thể đăng",
        )
        variants = db.all(
            "SELECT * FROM post_variants WHERE post_id=%s ORDER BY variant_id FOR UPDATE", (post_id,)
        )
        jobs = []
        for variant in variants:
            if variant["status"] == "published":
                continue
            validate_publish(db, post, variant)
            check(
                not db.one(
                    "SELECT 1 FROM schedules WHERE variant_id=%s AND status IN ('pending','queued')",
                    (variant["variant_id"],),
                ),
                409,
                "ACTIVE_SCHEDULE",
                "Hủy lịch trước khi đăng ngay",
            )
            jobs.append(enqueue(db, variant, user_id=request.state.user["user_id"]))
        check(jobs, 409, "ALREADY_PUBLISHED", "Mọi kênh đã đăng")
        change_status(db, post, "publishing", request.state.user["user_id"])
        audit(db, request, "post.publish", "post", post_id)
    return ok({"jobs": jobs})


@router.post(
    "/variants/{variant_id}/schedules", status_code=201, dependencies=[Depends(workspace("post.publish"))]
)
def create(workspace_id: Id, variant_id: Id, data: ScheduleInput, request: Request):
    at = utc(data.scheduled_at)
    check(at > now(), 422, "PAST_TIME", "Giờ đăng phải ở tương lai")
    with transaction() as db:
        post, variant = variant_context(db, workspace_id, variant_id, request.state.user["user_id"])
        check(
            variant["status"] not in ("published", "publishing", "cancelled")
            and post["status"] not in ("pending_approval", "rejected", "cancelled"),
            409,
            "INVALID_STATE",
            "Bài chưa thể lên lịch",
        )
        validate_publish(db, post, variant)
        check(
            not db.one(
                "SELECT 1 FROM publish_jobs WHERE variant_id=%s AND needs_reconciliation=1", (variant_id,)
            ),
            409,
            "RECONCILIATION_REQUIRED",
            "Cần đối soát trước",
        )
        schedule_id = db.insert(
            "schedules",
            {"variant_id": variant_id, "scheduled_at": at, "created_by": request.state.user["user_id"]},
        )
        db.execute("UPDATE post_variants SET status='scheduled' WHERE variant_id=%s", (variant_id,))
        change_status(db, post, "scheduled", request.state.user["user_id"])
        audit(db, request, "schedule.create", "schedule", schedule_id, new={"scheduled_at": at})
    return ok({"schedule_id": schedule_id, "scheduled_at": at})


@router.patch(
    "/variants/{variant_id}/schedules/{schedule_id}", dependencies=[Depends(workspace("post.publish"))]
)
def update(workspace_id: Id, variant_id: Id, schedule_id: Id, data: ScheduleUpdate, request: Request):
    check(bool(data.cancel) != bool(data.scheduled_at), 422, "INVALID_ACTION", "Chọn hủy hoặc đổi giờ")
    at = utc(data.scheduled_at) if data.scheduled_at else None
    if at:
        check(at > now(), 422, "PAST_TIME", "Giờ phải ở tương lai")
    with transaction() as db:
        post, variant = variant_context(db, workspace_id, variant_id, request.state.user["user_id"])
        schedule = db.one(
            "SELECT * FROM schedules WHERE schedule_id=%s AND variant_id=%s FOR UPDATE",
            (schedule_id, variant_id),
        )
        check(
            schedule and schedule["status"] in ("pending", "queued"),
            409,
            "SCHEDULE_NOT_ACTIVE",
            "Lịch không còn hoạt động",
        )
        check(
            not db.one(
                "SELECT 1 FROM publish_jobs WHERE schedule_id=%s AND (status IN ('running','success') OR needs_reconciliation=1)",
                (schedule_id,),
            ),
            409,
            "PUBLISH_STARTED",
            "Đã bắt đầu đăng hoặc cần đối soát",
        )
        db.execute(
            "UPDATE publish_jobs SET status='failed',error_code='CANCELLED',finished_at=UTC_TIMESTAMP() WHERE schedule_id=%s AND status='queued'",
            (schedule_id,),
        )
        if data.cancel:
            db.execute(
                "UPDATE schedules SET status='cancelled',cancelled_at=UTC_TIMESTAMP() WHERE schedule_id=%s",
                (schedule_id,),
            )
            db.execute("UPDATE post_variants SET status='draft' WHERE variant_id=%s", (variant_id,))
            if not db.one(
                "SELECT 1 FROM post_variants WHERE post_id=%s AND status IN ('scheduled','publishing') LIMIT 1",
                (post["post_id"],),
            ):
                change_status(
                    db, post, "draft", request.state.user["user_id"], "Hủy lịch; duyệt lại nếu bật duyệt"
                )
        else:
            db.execute(
                "UPDATE schedules SET status='pending',scheduled_at=%s WHERE schedule_id=%s",
                (at, schedule_id),
            )
        audit(
            db,
            request,
            "schedule.cancel" if data.cancel else "schedule.reschedule",
            "schedule",
            schedule_id,
            new=data.model_dump(),
        )
    return ok({"schedule_id": schedule_id, "status": "cancelled" if data.cancel else "pending"})


@router.get("/calendar", dependencies=[Depends(workspace())])
def calendar(
    workspace_id: Id,
    from_date: str = Query(alias="from"),
    to_date: str = Query(alias="to"),
    social_account_id: Id | None = None,
    status: Literal["pending", "queued", "completed", "cancelled"] | None = None,
    paging: Pagination = Depends(),
):
    start, end = utc(from_date), utc(to_date)
    check(start < end, 422, "INVALID_RANGE", "Khoảng ngày không hợp lệ")
    with transaction() as db:
        return ok(
            db.all(
                """SELECT s.*,v.post_id,v.social_account_id,a.account_name,p.title FROM schedules s
            JOIN post_variants v ON v.variant_id=s.variant_id JOIN posts p ON p.post_id=v.post_id JOIN social_accounts a ON a.social_account_id=v.social_account_id
            WHERE v.workspace_id=%s AND p.deleted_at IS NULL AND s.scheduled_at>=%s AND s.scheduled_at<%s
            AND (%s IS NULL OR v.social_account_id=%s) AND (%s IS NULL OR s.status=%s)
            ORDER BY s.scheduled_at,s.schedule_id LIMIT %s OFFSET %s""",
                (workspace_id, start, end, social_account_id, social_account_id, status, status, *paging.sql),
            )
        )


@router.get("/variants/{variant_id}/jobs", dependencies=[Depends(workspace())])
def jobs(workspace_id: Id, variant_id: Id, paging: Pagination = Depends()):
    with transaction() as db:
        check(
            db.one(
                "SELECT 1 FROM post_variants WHERE variant_id=%s AND workspace_id=%s",
                (variant_id, workspace_id),
            ),
            404,
            "NOT_FOUND",
            "Không tìm thấy phiên bản",
        )
        return ok(
            db.all(
                "SELECT job_id,attempt_no,status,started_at,finished_at,next_retry_at,error_code,error_message,needs_reconciliation FROM publish_jobs WHERE variant_id=%s ORDER BY attempt_no DESC LIMIT %s OFFSET %s",
                (variant_id, *paging.sql),
            )
        )


@router.post(
    "/variants/{variant_id}/retry", status_code=202, dependencies=[Depends(workspace("post.publish"))]
)
def retry(workspace_id: Id, variant_id: Id, request: Request):
    with transaction() as db:
        post, variant = variant_context(db, workspace_id, variant_id, request.state.user["user_id"])
        check(variant["status"] == "failed", 409, "INVALID_STATE", "Chỉ thử lại phiên bản lỗi")
        validate_publish(db, post, variant)
        check(
            not db.one(
                "SELECT 1 FROM schedules WHERE variant_id=%s AND status IN ('pending','queued')",
                (variant_id,),
            ),
            409,
            "ACTIVE_SCHEDULE",
            "Hủy lịch cũ trước",
        )
        job = enqueue(db, variant, user_id=request.state.user["user_id"])
        change_status(db, post, "publishing", request.state.user["user_id"], "Thử lại thủ công")
        audit(db, request, "post.retry", "variant", variant_id)
    return ok(job)


@router.post("/variants/{variant_id}/reconcile", dependencies=[Depends(workspace("post.publish"))])
def reconcile(workspace_id: Id, variant_id: Id, data: ReconcileInput, request: Request):
    with transaction() as db:
        post, variant = variant_context(db, workspace_id, variant_id, request.state.user["user_id"])
        job = db.one(
            "SELECT * FROM publish_jobs WHERE variant_id=%s AND needs_reconciliation=1 ORDER BY attempt_no DESC LIMIT 1 FOR UPDATE",
            (variant_id,),
        )
        check(job and variant["status"] == "failed", 409, "NO_RECONCILIATION", "Không có tác vụ cần đối soát")
        a = account(db, workspace_id, variant["social_account_id"])
        verified = verify_remote_post(
            a, variant, data.external_post_id, credentials(db, a["social_account_id"])["access_token"]
        )
        db.execute(
            "UPDATE publish_jobs SET status='success',needs_reconciliation=0,error_code=NULL,error_message=NULL,finished_at=UTC_TIMESTAMP() WHERE job_id=%s",
            (job["job_id"],),
        )
        db.execute(
            "UPDATE post_variants SET status='published',external_post_id=%s,external_post_url=%s,published_at=%s WHERE variant_id=%s",
            (data.external_post_id, verified.get("url"), verified.get("published_at") or now(), variant_id),
        )
        if job["schedule_id"]:
            db.execute("UPDATE schedules SET status='completed' WHERE schedule_id=%s", (job["schedule_id"],))
        aggregate(db, post)
        audit(
            db,
            request,
            "post.reconcile",
            "variant",
            variant_id,
            new={"external_post_id": data.external_post_id},
        )
    return ok({"status": "published", "external_post_id": data.external_post_id})
