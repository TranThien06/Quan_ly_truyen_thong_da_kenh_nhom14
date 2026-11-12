"""HTTP + worker + MySQL thật. Chỉ dùng DB test, không gọi mạng xã hội thật."""

import hashlib
import hmac
import io
import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app.main import create_app
from app.db import transaction
from app.common import now
from app.security import decrypt
from app.jobs.publishing import publish_one, queue_due_schedules, recover_expired_jobs
from app.jobs.sync import sync_accounts, sync_post_snapshots, refresh_tokens
from app.jobs.reports import export_one_report
from app.jobs.mail import notification_emails, send_one_email
from app.social import demo_id

pytestmark = pytest.mark.integration


class Scenario:
    def __init__(self, client):
        self.client = client

    def api(self, method, path, body=None, token=None, status=200, headers=None):
        headers = dict(headers or {})
        if token:
            headers["Authorization"] = "Bearer " + token
        result = self.client.request(method, "/api/v1" + path, json=body, headers=headers)
        assert result.status_code == status, result.text
        return result.json().get("data")

    def user(self, name):
        email = f"{name}-{uuid.uuid4()}@example.com"
        password = "Password123!"
        self.api(
            "POST", "/auth/register", {"email": email, "password": password, "full_name": name}, status=201
        )
        with transaction() as db:
            mail = db.one(
                "SELECT * FROM email_outbox WHERE recipient=%s ORDER BY email_id DESC LIMIT 1", (email,)
            )
        raw = parse_qs(urlparse(decrypt(mail["body_enc"])).query)["token"][0]
        self.api("POST", "/auth/verify-email", {"token": raw})
        self.api("POST", "/auth/verify-email", {"token": raw}, status=422)
        result = self.api("POST", "/auth/login", {"email": email, "password": password})
        return {
            "email": email,
            "password": password,
            "token": result["access_token"],
            "user_id": result["user"]["user_id"],
        }

    def invite(self, user, role):
        self.api(
            "POST",
            self.base + "/invitations",
            {"email": user["email"], "role": role},
            self.owner["token"],
            201,
        )
        with transaction() as db:
            mail = db.one(
                "SELECT * FROM email_outbox WHERE recipient=%s ORDER BY email_id DESC LIMIT 1",
                (user["email"],),
            )
        raw = parse_qs(urlparse(decrypt(mail["body_enc"])).query)["token"][0]
        self.api("POST", "/invitations/accept", {"token": raw}, user["token"])

    def connect(self, platform="facebook"):
        result = self.api("POST", self.base + f"/social-accounts/oauth/{platform}", {}, self.owner["token"])
        url = urlparse(result["authorization_url"])
        callback = self.api("GET", url.path.removeprefix("/api/v1") + "?" + url.query)
        self.api("GET", url.path.removeprefix("/api/v1") + "?" + url.query, status=422)
        return self.api(
            "POST",
            self.base + "/social-accounts/oauth/complete-selection",
            {
                "connection_token": callback["connection_token"],
                "external_account_ids": [callback["accounts"][0]["external_account_id"]],
            },
            self.owner["token"],
        )[0]["social_account_id"]

    def post(self, options=None, owner=False):
        return self.api(
            "POST",
            self.base + "/posts",
            {
                "title": "Bài thử tiếng Việt",
                "base_caption": "Xin chào",
                "media_ids": [self.media_id],
                "variants": [{"social_account_id": self.channel_id, "platform_options": options or {}}],
            },
            self.owner["token"] if owner else self.editor["token"],
            201,
        )

    def send(self, post):
        return self.api("POST", self.base + f"/posts/{post['post_id']}/publish", {}, self.owner["token"], 202)

    def settings(self, **values):
        return self.api("PATCH", self.base + "/settings", values, self.owner["token"])


@pytest.fixture(scope="module")
def scenario():
    assert re.fullmatch(r"team14_api_test[A-Za-z0-9_]*", os.getenv("DB_NAME", "")), (
        "Refusing to clean a non-test DB"
    )
    assert os.getenv("SOCIAL_MODE") == "mock"
    assert os.getenv("MAIL_MODE") == "file"
    with transaction() as db:
        db.execute("SET FOREIGN_KEY_CHECKS=0")
        try:
            reference = {
                "roles",
                "permissions",
                "role_permissions",
                "platforms",
                "system_settings",
                "schema_migrations",
            }
            for row in db.all("SHOW TABLES"):
                table = next(iter(row.values()))
                assert re.fullmatch(r"[a-z_]+", table)
                if table not in reference:
                    db.execute(f"TRUNCATE TABLE `{table}`")
        finally:
            db.execute("SET FOREIGN_KEY_CHECKS=1")
    with TestClient(create_app()) as client:
        s = Scenario(client)
        s.owner, s.editor, s.approver, s.outsider, s.manager = [
            s.user(name) for name in ("owner", "editor", "approver", "outsider", "manager")
        ]
        created = s.api(
            "POST",
            "/workspaces",
            {"name": "Team 14 Python", "slug": f"team-{uuid.uuid4()}"},
            s.owner["token"],
            201,
        )
        s.workspace_id = created["workspace_id"]
        s.base = f"/workspaces/{s.workspace_id}"
        for user, role in ((s.editor, "editor"), (s.approver, "approver"), (s.manager, "manager")):
            s.invite(user, role)
        s.channel_id = s.connect()
        content = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(content, "PNG")
        upload = client.post(
            "/api/v1" + s.base + "/media",
            files={"file": ("test.png", content.getvalue(), "image/png")},
            headers={"Authorization": "Bearer " + s.editor["token"]},
        )
        assert upload.status_code == 201, upload.text
        s.media_id = upload.json()["data"]["media_id"]
        yield s


@pytest.fixture(autouse=True)
def reset_approval_policy(scenario):
    scenario.settings(require_approval=False, allow_self_approval=False)


def test_login_logout_lock_and_profile(scenario):
    s = scenario
    profile = s.api("GET", "/me", token=s.owner["token"])
    assert profile["email"] == s.owner["email"] and "password_hash" not in profile
    result = s.api("POST", "/auth/login", {"email": s.owner["email"], "password": s.owner["password"]})
    s.api("POST", "/auth/logout", {}, result["access_token"])
    s.api("GET", "/me", token=result["access_token"], status=401)
    for _ in range(5):
        s.api("POST", "/auth/login", {"email": s.outsider["email"], "password": "wrong"}, status=401)
    with transaction() as db:
        locked = db.one("SELECT * FROM users WHERE user_id=%s", (s.outsider["user_id"],))
    assert locked["failed_login_attempts"] == 5 and locked["locked_until"]


def test_workspace_isolation_owner_and_privilege_escalation(scenario):
    s = scenario
    s.api("GET", s.base, token=s.outsider["token"], status=404)
    s.api("PATCH", s.base, {"name": "Hack"}, s.editor["token"], 403)
    members = s.api("GET", s.base + "/members", token=s.owner["token"])
    owner = next(m for m in members if m["user_id"] == s.owner["user_id"])
    s.api("PATCH", s.base + f"/members/{owner['member_id']}", {"role": "viewer"}, s.owner["token"], 409)
    manager = next(m for m in members if m["user_id"] == s.manager["user_id"])
    s.api("PATCH", s.base + f"/members/{manager['member_id']}", {"role": "admin"}, s.manager["token"], 403)


def test_tiktok_oauth_persists_channel_and_handles_denial(scenario):
    s = scenario
    start = s.base + "/social-accounts/oauth/tiktok"
    result = s.api("POST", start, {}, s.owner["token"])
    url = urlparse(result["authorization_url"])
    state = parse_qs(url.query)["state"][0]
    denied = s.client.get(url.path, params={"state": state, "error": "access_denied"})
    assert denied.status_code == 400
    assert "OAUTH_DENIED" in denied.text
    assert s.client.get(result["authorization_url"]).status_code == 422

    account_id = s.connect("tiktok")
    accounts = s.api("GET", s.base + "/social-accounts", token=s.owner["token"])
    channel = next(a for a in accounts if a["social_account_id"] == account_id)
    assert channel["external_account_id"] == "mock-tiktok"
    assert channel["account_name"] == "DEMO tiktok" and channel["status"] == "connected"
    with transaction() as db:
        stored = db.one("SELECT * FROM social_account_tokens WHERE social_account_id=%s", (account_id,))
    assert stored["access_token_enc"] != "mock-token"
    assert decrypt(stored["access_token_enc"]) == "mock-token"
    assert decrypt(stored["refresh_token_enc"]) == "mock-refresh"
    assert stored["expires_at"]


def test_oauth_reconnect_tokens_and_images(scenario):
    s = scenario
    assert s.connect() == s.channel_id
    accounts = s.api("GET", s.base + "/social-accounts", token=s.owner["token"])
    facebook_accounts = [account for account in accounts if account["external_account_id"] == "mock-facebook"]
    assert [account["social_account_id"] for account in facebook_accounts] == [s.channel_id]
    assert all("access_token_enc" not in account for account in accounts)
    links = s.api("GET", s.base + f"/media/{s.media_id}/download-link", token=s.editor["token"])
    assert s.client.get(links["file_url"]).status_code == 200
    assert s.client.get(links["file_url"].split("?")[0]).status_code in (403, 422)
    folder = s.api("POST", s.base + "/media-folders", {"name": "Folder"}, s.editor["token"], 201)
    s.api(
        "PUT",
        s.base + f"/media-folders/{folder['folder_id']}",
        {"name": "Loop", "parent_folder_id": folder["folder_id"]},
        s.editor["token"],
        422,
    )


def test_approval_edit_lock_and_publish_permissions(scenario):
    s = scenario
    post = s.post()
    s.api("POST", s.base + f"/posts/{post['post_id']}/publish", {}, s.editor["token"], 403)
    s.settings(require_approval=True)
    s.api("POST", s.base + f"/posts/{post['post_id']}/publish", {}, s.owner["token"], 409)
    s.api(
        "POST", s.base + f"/posts/{post['post_id']}/approvals", {"note": "Nhờ duyệt"}, s.editor["token"], 201
    )
    body = {"title": "Sửa", "variants": [{"social_account_id": s.channel_id}]}
    s.api("PUT", s.base + f"/posts/{post['post_id']}", body, s.editor["token"], 409)
    s.api(
        "POST",
        s.base + f"/posts/{post['post_id']}/approvals/decision",
        {"decision": "rejected"},
        s.approver["token"],
        422,
    )
    s.api(
        "POST",
        s.base + f"/posts/{post['post_id']}/approvals/decision",
        {"decision": "approved"},
        s.approver["token"],
    )
    s.send(post)
    assert publish_one()
    s.published_post = post
    assert s.api("GET", s.base + f"/posts/{post['post_id']}", token=s.owner["token"])["status"] == "published"


def test_concurrent_publish_requests_and_workers_do_not_duplicate(scenario):
    s = scenario
    post = s.post()
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: s.send(post), range(2)))
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: publish_one(), range(2)))
    with transaction() as db:
        jobs = db.all("SELECT * FROM publish_jobs WHERE variant_id=%s", (post["variants"][0]["variant_id"],))
    assert len(jobs) == 1 and jobs[0]["status"] == "success"


def test_cancel_schedule_and_due_scan_idempotency(scenario):
    s = scenario
    post = s.post()
    variant = post["variants"][0]["variant_id"]
    when = (now() + timedelta(minutes=5)).isoformat() + "Z"
    schedule = s.api(
        "POST", s.base + f"/variants/{variant}/schedules", {"scheduled_at": when}, s.owner["token"], 201
    )
    s.api("POST", s.base + f"/variants/{variant}/schedules", {"scheduled_at": when}, s.owner["token"], 409)
    s.api(
        "PATCH",
        s.base + f"/variants/{variant}/schedules/{schedule['schedule_id']}",
        {"cancel": True},
        s.owner["token"],
    )
    with transaction() as db:
        db.execute(
            "UPDATE schedules SET scheduled_at=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 1 MINUTE) WHERE schedule_id=%s",
            (schedule["schedule_id"],),
        )
    queue_due_schedules()
    with transaction() as db:
        assert not db.all("SELECT * FROM publish_jobs WHERE variant_id=%s", (variant,))
    active = s.api(
        "POST", s.base + f"/variants/{variant}/schedules", {"scheduled_at": when}, s.owner["token"], 201
    )
    with transaction() as db:
        db.execute(
            "UPDATE schedules SET scheduled_at=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 1 MINUTE) WHERE schedule_id=%s",
            (active["schedule_id"],),
        )
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: queue_due_schedules(), range(2)))
    assert publish_one()
    with transaction() as db:
        assert len(db.all("SELECT * FROM publish_jobs WHERE variant_id=%s", (variant,))) == 1


def test_retry_backoff_and_limit(scenario):
    s = scenario
    post = s.post({"mock_failure": "temporary"})
    variant = post["variants"][0]["variant_id"]
    s.send(post)
    for index in range(4):
        with transaction() as db:
            db.execute(
                "UPDATE publish_jobs SET next_retry_at=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 1 SECOND) WHERE variant_id=%s AND status='queued'",
                (variant,),
            )
        publish_one()
        if index == 0:
            with transaction() as db:
                rows = db.all(
                    "SELECT * FROM publish_jobs WHERE variant_id=%s ORDER BY attempt_no", (variant,)
                )
            assert rows[1]["next_retry_at"] > now() + timedelta(seconds=30)
    with transaction() as db:
        rows = db.all("SELECT * FROM publish_jobs WHERE variant_id=%s", (variant,))
    assert len(rows) == 4 and all(j["status"] == "failed" for j in rows)


def test_unknown_send_and_crashed_worker_require_reconciliation(scenario):
    s = scenario
    post = s.post({"mock_failure": "ambiguous"})
    variant = post["variants"][0]["variant_id"]
    s.send(post)
    publish_one()
    s.api("POST", s.base + f"/variants/{variant}/retry", {}, s.owner["token"], 409)
    post = s.post()
    variant = post["variants"][0]["variant_id"]
    s.send(post)
    with transaction() as db:
        db.execute(
            "UPDATE publish_jobs SET status='running',lease_expires_at=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 1 MINUTE) WHERE variant_id=%s",
            (variant,),
        )
    recover_expired_jobs()
    s.api("POST", s.base + f"/variants/{variant}/retry", {}, s.owner["token"], 409)
    s.api(
        "POST",
        s.base + f"/variants/{variant}/reconcile",
        {"external_post_id": "wrong"},
        s.owner["token"],
        422,
    )
    s.api(
        "POST",
        s.base + f"/variants/{variant}/reconcile",
        {"external_post_id": demo_id(variant)},
        s.owner["token"],
    )
    assert s.api("GET", s.base + f"/posts/{post['post_id']}", token=s.owner["token"])["status"] == "published"


def test_post_idempotency_and_cross_workspace_media(scenario):
    s = scenario
    body = {"base_caption": "Idempotent", "variants": [{"social_account_id": s.channel_id}]}
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    first = s.api("POST", s.base + "/posts", body, s.editor["token"], 201, headers)
    second = s.api("POST", s.base + "/posts", body, s.editor["token"], 201, headers)
    assert first["post_id"] == second["post_id"]
    s.api("POST", s.base + "/posts", {**body, "title": "Changed"}, s.editor["token"], 409, headers)
    other = s.api(
        "POST", "/workspaces", {"name": "Khác", "slug": f"other-{uuid.uuid4()}"}, s.outsider["token"], 201
    )
    s.api(
        "GET",
        f"/workspaces/{other['workspace_id']}/posts/{first['post_id']}",
        token=s.outsider["token"],
        status=404,
    )
    s.api("POST", f"/workspaces/{other['workspace_id']}/posts", body, s.outsider["token"], 422)


def test_self_approval_and_edit_invalidation(scenario):
    s = scenario
    post = s.post(owner=True)
    path = s.base + f"/posts/{post['post_id']}"
    s.api("POST", path + "/approvals", {}, s.owner["token"], 201)
    s.api("POST", path + "/approvals/decision", {"decision": "approved"}, s.owner["token"], 403)
    s.api("POST", path + "/approvals/decision", {"decision": "approved"}, s.approver["token"])
    s.api(
        "PUT",
        path,
        {"base_caption": "Đã sửa", "variants": [{"social_account_id": s.channel_id}]},
        s.owner["token"],
    )
    s.settings(require_approval=True)
    s.api("POST", path + "/publish", {}, s.owner["token"], 409)


def test_sync_reports_inbox_and_notifications(scenario):
    s = scenario
    sync_accounts()
    with transaction() as db:
        db.execute(
            "UPDATE social_accounts SET last_inbox_sync_at=NULL WHERE social_account_id=%s", (s.channel_id,)
        )
    sync_accounts()
    threads = s.api("GET", s.base + "/conversations", token=s.editor["token"])
    threads = [thread for thread in threads if thread["social_account_id"] == s.channel_id]
    assert len(threads) == 1
    path = s.base + f"/conversations/{threads[0]['conversation_id']}/messages"
    body = {"content": "Chào bạn", "request_id": str(uuid.uuid4())}
    first = s.api("POST", path, body, s.editor["token"], 201)
    assert first == s.api("POST", path, body, s.editor["token"], 201)
    s.api("POST", path, {**body, "content": "Khác nội dung"}, s.editor["token"], 409)
    today = now().date().isoformat()
    overview = s.api("GET", s.base + f"/reports/overview?from={today}&to={today}", token=s.owner["token"])
    assert overview and overview[0]["extra_metrics"]["source"] == "mock"
    for kind in ("csv", "xlsx", "pdf"):
        result = s.api(
            "POST",
            s.base + "/reports/exports",
            {"from": today, "to": today, "report_type": "channel_overview", "file_format": kind},
            s.owner["token"],
            202,
        )
        assert export_one_report()
        path = s.base + f"/reports/exports/{result['export_id']}"
        assert s.api("GET", path, token=s.owner["token"])["status"] == "done"
        response = s.client.get(
            "/api/v1" + path + "/download", headers={"Authorization": "Bearer " + s.owner["token"]}
        )
        assert response.status_code == 200 and len(response.content) > 20
    assert s.api("GET", "/me/notifications/unread-count", token=s.editor["token"])["count"] > 0
    s.api("POST", "/me/notifications/read", {"all": True}, s.editor["token"])
    assert s.api("GET", "/me/notifications/unread-count", token=s.editor["token"])["count"] == 0
    assert s.api("GET", s.base + "/audit-logs", token=s.owner["token"])
    s.api("GET", "/admin/settings", token=s.owner["token"], status=403)
    notification_emails()
    assert send_one_email()


def test_snapshot_checkpoints_and_token_refresh(scenario):
    s = scenario
    variant = s.published_post["variants"][0]["variant_id"]
    with transaction() as db:
        db.execute(
            "UPDATE post_variants SET published_at=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 2 HOUR) WHERE variant_id=%s",
            (variant,),
        )
        db.execute(
            "UPDATE social_account_tokens SET expires_at=DATE_ADD(UTC_TIMESTAMP(),INTERVAL 30 MINUTE),last_refreshed_at=NULL WHERE social_account_id=%s",
            (s.channel_id,),
        )
    sync_post_snapshots()
    sync_post_snapshots()
    refresh_tokens()
    with transaction() as db:
        assert len(db.all("SELECT * FROM post_metric_snapshots WHERE variant_id=%s", (variant,))) == 1
        auth = db.one("SELECT * FROM social_account_tokens WHERE social_account_id=%s", (s.channel_id,))
        assert decrypt(auth["access_token_enc"]) == "mock-refreshed"


def test_password_reset_and_legacy_express_bcrypt(scenario):
    s = scenario
    s.api("POST", "/auth/forgot-password", {"email": s.approver["email"]})
    with transaction() as db:
        mail = db.one(
            "SELECT * FROM email_outbox WHERE recipient=%s ORDER BY email_id DESC LIMIT 1",
            (s.approver["email"],),
        )
    raw = parse_qs(urlparse(decrypt(mail["body_enc"])).query)["token"][0]
    body = {"token": raw, "password": "NewPassword123!"}
    s.api("POST", "/auth/reset-password", body)
    s.api("POST", "/auth/reset-password", body, status=422)
    s.api("GET", "/me", token=s.approver["token"], status=401)
    fixture = json.loads((Path(__file__).parent / "fixtures" / "express_crypto.json").read_text())
    email = f"legacy-{uuid.uuid4()}@example.com"
    with transaction() as db:
        db.insert(
            "users",
            {
                "email": email,
                "password_hash": fixture["bcrypt_hash"],
                "full_name": "Legacy",
                "status": "active",
                "email_verified_at": now(),
            },
        )
    assert s.api("POST", "/auth/login", {"email": email, "password": fixture["password"]})["access_token"]


def test_signed_webhook_and_deduplication(scenario, monkeypatch):
    s = scenario
    monkeypatch.setenv("META_APP_SECRET", "test-webhook-secret")
    raw = json.dumps({"object": "page", "entry": [{"id": "mock-facebook"}]}).encode()
    path = "/api/v1/webhooks/meta"
    assert s.client.post(path, content=raw, headers={"Content-Type": "application/json"}).status_code == 403
    signature = "sha256=" + hmac.new(b"test-webhook-secret", raw, hashlib.sha256).hexdigest()
    for _ in range(2):
        assert (
            s.client.post(
                path,
                content=raw,
                headers={"Content-Type": "application/json", "x-hub-signature-256": signature},
            ).status_code
            == 200
        )
    with transaction() as db:
        assert len(db.all("SELECT * FROM webhook_receipts")) == 1


def test_instagram_connection_stores_linked_page(scenario):
    s = scenario
    page_id = s.connect()
    instagram_id = s.connect("instagram")
    assert s.connect("instagram") == instagram_id
    accounts = s.api("GET", s.base + "/social-accounts", token=s.owner["token"])
    instagram = next(account for account in accounts if account["social_account_id"] == instagram_id)
    assert instagram["platform"] == "instagram"
    assert instagram["linked_account_id"] == page_id
