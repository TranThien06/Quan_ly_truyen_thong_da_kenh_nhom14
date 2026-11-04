"""Quy tắc chung cho đăng ngay, lịch và worker. Khóa post trước variant/job."""

from app.dependencies import membership
from app.errors import check
from app.events import change_status
from app.modules.posts import load_post


def validate_publish(db, post, variant):
    account = db.one(
        "SELECT a.*,p.code,p.max_caption_length FROM social_accounts a JOIN platforms p ON p.platform_id=a.platform_id WHERE a.social_account_id=%s AND a.workspace_id=%s",
        (variant["social_account_id"], post["workspace_id"]),
    )
    check(account and account["status"] == "connected", 409, "CHANNEL_UNAVAILABLE", "Kênh chưa kết nối")
    setting = db.one(
        "SELECT setting_value FROM workspace_settings WHERE workspace_id=%s AND setting_key='require_approval'",
        (post["workspace_id"],),
    )
    if setting and setting["setting_value"] is True:
        approval = db.one(
            "SELECT * FROM approval_requests WHERE post_id=%s ORDER BY approval_id DESC LIMIT 1",
            (post["post_id"],),
        )
        check(
            approval
            and approval["status"] == "approved"
            and post["status"] not in ("draft", "rejected", "pending_approval"),
            409,
            "APPROVAL_REQUIRED",
            "Bài chưa được duyệt",
        )
    media = db.all(
        "SELECT m.* FROM post_media pm JOIN media_assets m ON m.media_id=pm.media_id WHERE pm.post_id=%s ORDER BY pm.sort_order",
        (post["post_id"],),
    )
    check(not any(m["deleted_at"] for m in media), 422, "MEDIA_DELETED", "Media đã xóa")
    caption = variant["caption"] if variant["caption"] is not None else post["base_caption"] or ""
    check(len(caption) <= account["max_caption_length"], 422, "CAPTION_TOO_LONG", "Caption vượt giới hạn")
    check(caption.strip() or media, 422, "EMPTY_POST", "Bài chưa có nội dung")
    if account["code"] == "instagram":
        check(media, 422, "MEDIA_REQUIRED", "Instagram cần ảnh/video")
    if account["code"] == "tiktok":
        check(
            len(media) == 1 and media[0]["media_type"] == "video",
            422,
            "VIDEO_REQUIRED",
            "TikTok cần một video",
        )
    return {"account": account, "media": media, "caption": caption}


def enqueue(db, variant, schedule_id=None, user_id=None):
    jobs = db.all(
        "SELECT * FROM publish_jobs WHERE variant_id=%s ORDER BY attempt_no DESC", (variant["variant_id"],)
    )
    check(
        variant["status"] != "published" and not any(j["status"] == "success" for j in jobs),
        409,
        "ALREADY_PUBLISHED",
        "Kênh đã đăng thành công",
    )
    active = next((j for j in jobs if j["status"] in ("queued", "running")), None)
    if active:
        return {"job_id": active["job_id"]}
    check(
        not any(j["needs_reconciliation"] for j in jobs),
        409,
        "RECONCILIATION_REQUIRED",
        "Cần đối soát kết quả trên nền tảng trước",
    )
    job_id = db.insert(
        "publish_jobs",
        {
            "variant_id": variant["variant_id"],
            "schedule_id": schedule_id,
            "requested_by": user_id,
            "attempt_no": (jobs[0]["attempt_no"] if jobs else 0) + 1,
        },
    )
    db.execute("UPDATE post_variants SET status='publishing' WHERE variant_id=%s", (variant["variant_id"],))
    return {"job_id": job_id}


def variant_context(db, workspace_id, variant_id, user_id):
    found = db.one(
        "SELECT post_id FROM post_variants WHERE variant_id=%s AND workspace_id=%s",
        (variant_id, workspace_id),
    )
    check(found, 404, "NOT_FOUND", "Không tìm thấy phiên bản bài")
    post = load_post(db, workspace_id, found["post_id"], True)
    membership(db, workspace_id, user_id, "post.publish")
    variant = db.one("SELECT * FROM post_variants WHERE variant_id=%s FOR UPDATE", (variant_id,))
    return post, variant


def aggregate(db, post):
    states = [
        v["status"] for v in db.all("SELECT status FROM post_variants WHERE post_id=%s", (post["post_id"],))
    ]
    if "publishing" in states:
        status = "publishing"
    elif "scheduled" in states:
        status = "scheduled"
    elif states and all(s == "published" for s in states):
        status = "published"
    elif "published" in states:
        status = "partially_failed"
    elif states and all(s == "cancelled" for s in states):
        status = "cancelled"
    else:
        status = "failed"
    change_status(db, post, status)
