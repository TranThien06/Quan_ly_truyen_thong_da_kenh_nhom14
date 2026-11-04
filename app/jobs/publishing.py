"""Nhận job bằng transaction; gọi nền tảng sau commit; ghi kết quả có claim token."""

import uuid
from datetime import timedelta
from app.common import now
from app.db import transaction, json_value
from app.dependencies import membership
from app.errors import check
from app.events import change_status, notify
from app.modules.posts import load_post
from app.modules.channels import credentials
from app.publishing import validate_publish, enqueue, aggregate
from app.social import publish_remote, ProviderError


def job_context(db, job):
    v = db.one("SELECT post_id,workspace_id FROM post_variants WHERE variant_id=%s", (job["variant_id"],))
    post = load_post(db, v["workspace_id"], v["post_id"], True)
    variant = db.one("SELECT * FROM post_variants WHERE variant_id=%s FOR UPDATE", (job["variant_id"],))
    return post, variant


def queue_due_schedules(limit=20):
    with transaction() as db:
        candidates = db.all(
            "SELECT s.*,v.post_id,v.workspace_id FROM schedules s JOIN post_variants v ON v.variant_id=s.variant_id WHERE s.status='pending' AND s.scheduled_at<=UTC_TIMESTAMP() ORDER BY s.scheduled_at LIMIT %s",
            (limit,),
        )
    for candidate in candidates:
        with transaction() as db:
            post = load_post(db, candidate["workspace_id"], candidate["post_id"], True)
            variant = db.one(
                "SELECT * FROM post_variants WHERE variant_id=%s FOR UPDATE", (candidate["variant_id"],)
            )
            schedule = db.one(
                "SELECT * FROM schedules WHERE schedule_id=%s AND status='pending' AND scheduled_at<=UTC_TIMESTAMP() FOR UPDATE",
                (candidate["schedule_id"],),
            )
            if not schedule:
                continue
            try:
                membership(db, post["workspace_id"], schedule["created_by"], "post.publish")
                validate_publish(db, post, variant)
                enqueue(db, variant, schedule["schedule_id"], schedule["created_by"])
                db.execute(
                    "UPDATE schedules SET status='queued' WHERE schedule_id=%s", (schedule["schedule_id"],)
                )
                change_status(db, post, "publishing")
            except Exception as error:
                # Business failures are recorded; SQL/connection errors must roll back and retry the tick.
                if not hasattr(error, "code"):
                    raise
                db.execute(
                    "UPDATE schedules SET status='completed' WHERE schedule_id=%s", (schedule["schedule_id"],)
                )
                db.execute(
                    "UPDATE post_variants SET status='failed' WHERE variant_id=%s", (variant["variant_id"],)
                )
                last = db.one(
                    "SELECT COALESCE(MAX(attempt_no),0) n FROM publish_jobs WHERE variant_id=%s",
                    (variant["variant_id"],),
                )
                db.insert(
                    "publish_jobs",
                    {
                        "variant_id": variant["variant_id"],
                        "schedule_id": schedule["schedule_id"],
                        "requested_by": schedule["created_by"],
                        "attempt_no": last["n"] + 1,
                        "status": "failed",
                        "error_code": error.code,
                        "error_message": "Điều kiện đăng không còn hợp lệ",
                        "finished_at": now(),
                    },
                )
                notify(
                    db,
                    post["workspace_id"],
                    post["created_by"],
                    "post_failed",
                    "Không thể xử lý lịch đăng",
                    "post",
                    post["post_id"],
                )
                aggregate(db, post)


def recover_expired_jobs():
    with transaction() as db:
        candidates = db.all(
            "SELECT * FROM publish_jobs WHERE status='running' AND lease_expires_at<UTC_TIMESTAMP() LIMIT 20"
        )
    for candidate in candidates:
        with transaction() as db:
            post, variant = job_context(db, candidate)
            job = db.one(
                "SELECT * FROM publish_jobs WHERE job_id=%s AND status='running' AND lease_expires_at<UTC_TIMESTAMP() FOR UPDATE",
                (candidate["job_id"],),
            )
            if not job:
                continue
            db.execute(
                "UPDATE publish_jobs SET status='failed',needs_reconciliation=1,error_code='WORKER_INTERRUPTED',error_message='Cần đối soát kết quả trên nền tảng',finished_at=UTC_TIMESTAMP() WHERE job_id=%s",
                (job["job_id"],),
            )
            db.execute(
                "UPDATE post_variants SET status='failed' WHERE variant_id=%s", (variant["variant_id"],)
            )
            if job["schedule_id"]:
                db.execute(
                    "UPDATE schedules SET status='completed' WHERE schedule_id=%s", (job["schedule_id"],)
                )
            aggregate(db, post)
            notify(
                db,
                post["workspace_id"],
                post["created_by"],
                "post_failed",
                "Worker bị gián đoạn; cần đối soát",
                "post",
                post["post_id"],
            )


def claim_one():
    with transaction() as db:
        candidate = db.one(
            "SELECT * FROM publish_jobs WHERE status='queued' AND (next_retry_at IS NULL OR next_retry_at<=UTC_TIMESTAMP()) ORDER BY job_id LIMIT 1"
        )
        if not candidate:
            return None
        post, variant = job_context(db, candidate)
        job = db.one(
            "SELECT * FROM publish_jobs WHERE job_id=%s AND status='queued' AND (next_retry_at IS NULL OR next_retry_at<=UTC_TIMESTAMP()) FOR UPDATE",
            (candidate["job_id"],),
        )
        if not job:
            return None
        claim = str(uuid.uuid4())
        db.execute(
            "UPDATE publish_jobs SET status='running',claim_token=%s,lease_expires_at=DATE_ADD(UTC_TIMESTAMP(),INTERVAL 120 SECOND),started_at=UTC_TIMESTAMP() WHERE job_id=%s",
            (claim, job["job_id"]),
        )
        return job, post, variant, claim


def publish_one():
    claimed = claim_one()
    if not claimed:
        return False
    job, post, variant, claim = claimed
    result, error = None, None

    def save_progress(progress):
        with transaction() as db:
            updated = db.execute(
                "UPDATE publish_jobs SET response_payload=%s WHERE job_id=%s AND claim_token=%s AND status='running'",
                (json_value(progress), job["job_id"], claim),
            )
            check(updated, 409, "CLAIM_LOST", "Worker đã mất quyền xử lý")

    try:
        with transaction() as db:
            membership(db, post["workspace_id"], job["requested_by"], "post.publish")
            payload = validate_publish(db, post, variant)
            auth = credentials(db, variant["social_account_id"])
        result = publish_remote(
            **payload,
            variant=variant,
            access_token=auth["access_token"],
            progress=job["response_payload"] or {},
            save_progress=save_progress,
        )
    except Exception as cause:
        error = cause
    if result and not result.get("pending") and not result.get("external_post_id"):
        result, error = None, ProviderError("PROVIDER_RESULT_UNKNOWN", ambiguous=True)
    if result and result.get("pending") and now() - job["created_at"] > timedelta(hours=1):
        result, error = None, ProviderError("PROVIDER_PROCESSING_TIMEOUT", ambiguous=True)
    with transaction() as db:
        current_post, _ = job_context(db, job)
        active = db.one(
            "SELECT * FROM publish_jobs WHERE job_id=%s AND status='running' AND claim_token=%s FOR UPDATE",
            (job["job_id"], claim),
        )
        if not active:
            return True
        if result and result.get("pending"):
            db.execute(
                "UPDATE publish_jobs SET status='queued',next_retry_at=DATE_ADD(UTC_TIMESTAMP(),INTERVAL 15 SECOND),claim_token=NULL,lease_expires_at=NULL WHERE job_id=%s",
                (job["job_id"],),
            )
            return True
        if result:
            db.execute(
                "UPDATE publish_jobs SET status='success',finished_at=UTC_TIMESTAMP(),lease_expires_at=NULL,error_code=NULL WHERE job_id=%s",
                (job["job_id"],),
            )
            db.execute(
                "UPDATE post_variants SET status='published',external_post_id=%s,external_post_url=%s,published_at=UTC_TIMESTAMP() WHERE variant_id=%s",
                (result["external_post_id"], result.get("external_post_url"), variant["variant_id"]),
            )
            if job["schedule_id"]:
                db.execute(
                    "UPDATE schedules SET status='completed' WHERE schedule_id=%s", (job["schedule_id"],)
                )
            notify(
                db,
                post["workspace_id"],
                post["created_by"],
                "post_published",
                "Đã đăng thành công một kênh",
                "post",
                post["post_id"],
            )
        else:
            ambiguous = bool(getattr(error, "ambiguous", False))
            code = str(getattr(error, "code", "PUBLISH_FAILED"))[:100]
            db.execute(
                "UPDATE publish_jobs SET status='failed',finished_at=UTC_TIMESTAMP(),error_code=%s,error_message=%s,needs_reconciliation=%s,lease_expires_at=NULL WHERE job_id=%s",
                (
                    code,
                    "Cần đối soát trên nền tảng" if ambiguous else "Đăng thất bại; xem mã lỗi",
                    int(ambiguous),
                    job["job_id"],
                ),
            )
            maximum = db.one(
                "SELECT setting_value FROM system_settings WHERE setting_key='publish.max_retries'"
            )
            can_retry = (
                getattr(error, "retryable", False)
                and not ambiguous
                and job["retry_count"] < int(maximum["setting_value"] if maximum else 3)
            )
            if can_retry:
                setting = db.one(
                    "SELECT setting_value FROM system_settings WHERE setting_key='publish.retry_delays_seconds'"
                )
                delays = setting["setting_value"] if setting else [60, 120, 240]
                delay = max(
                    delays[min(job["retry_count"], len(delays) - 1)], getattr(error, "retry_after", 0)
                )
                db.insert(
                    "publish_jobs",
                    {
                        "variant_id": variant["variant_id"],
                        "schedule_id": job["schedule_id"],
                        "requested_by": job["requested_by"],
                        "attempt_no": job["attempt_no"] + 1,
                        "retry_count": job["retry_count"] + 1,
                        "next_retry_at": now() + timedelta(seconds=delay),
                        "response_payload": json_value(active["response_payload"])
                        if active["response_payload"]
                        else None,
                    },
                )
            else:
                db.execute(
                    "UPDATE post_variants SET status='failed' WHERE variant_id=%s", (variant["variant_id"],)
                )
                if job["schedule_id"]:
                    db.execute(
                        "UPDATE schedules SET status='completed' WHERE schedule_id=%s", (job["schedule_id"],)
                    )
                notify(
                    db,
                    post["workspace_id"],
                    post["created_by"],
                    "post_failed",
                    "Đăng bài thất bại",
                    "post",
                    post["post_id"],
                )
            if code == "TOKEN_EXPIRED":
                db.execute(
                    "UPDATE social_accounts SET status='expired',status_message='TOKEN_EXPIRED' WHERE social_account_id=%s",
                    (variant["social_account_id"],),
                )
        aggregate(db, current_post)
    return True
