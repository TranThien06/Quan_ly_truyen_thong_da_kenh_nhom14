import json
import os
import smtplib
import ssl
import uuid
from datetime import timedelta
from email.message import EmailMessage
from app.common import now
from app.config import settings
from app.db import transaction
from app.security import encrypt, decrypt


def notification_emails():
    with transaction() as db:
        rows = db.all("""SELECT n.*,u.email FROM notifications n JOIN users u ON u.user_id=n.user_id
            WHERE n.email_status='pending' AND n.email_claim_token IS NULL AND u.status='active'
            AND (n.workspace_id IS NULL OR EXISTS (SELECT 1 FROM workspace_members m JOIN workspaces w ON w.workspace_id=m.workspace_id
            WHERE m.workspace_id=n.workspace_id AND m.user_id=n.user_id AND m.status='active' AND w.deleted_at IS NULL))
            ORDER BY n.notification_id LIMIT 20 FOR UPDATE SKIP LOCKED""")
        for item in rows:
            content = f"{item['title']}\n{settings.frontend_url}/workspaces/{item['workspace_id']}/{item['entity_type']}/{item['entity_id']}"
            email_id = db.insert(
                "email_outbox",
                {"recipient": item["email"], "subject": item["title"], "body_enc": encrypt(content)},
            )
            db.execute(
                "UPDATE notifications SET email_claim_token=%s WHERE notification_id=%s",
                (f"outbox:{email_id}", item["notification_id"]),
            )


def send_one_email():
    with transaction() as db:
        db.execute(
            "UPDATE email_outbox SET status='pending',claim_token=NULL WHERE status='sending' AND lease_expires_at<UTC_TIMESTAMP()"
        )
        item = db.one(
            "SELECT * FROM email_outbox WHERE status='pending' AND next_attempt_at<=UTC_TIMESTAMP() ORDER BY email_id LIMIT 1 FOR UPDATE SKIP LOCKED"
        )
        if not item:
            return False
        claim = str(uuid.uuid4())
        db.execute(
            "UPDATE email_outbox SET status='sending',claim_token=%s,attempt_count=attempt_count+1,lease_expires_at=DATE_ADD(UTC_TIMESTAMP(),INTERVAL 120 SECOND) WHERE email_id=%s",
            (claim, item["email_id"]),
        )
    try:
        message = {
            "from": os.getenv("MAIL_FROM", "team14@example.com"),
            "to": item["recipient"],
            "subject": item["subject"],
            "text": decrypt(item["body_enc"]),
            "messageId": f"<team14-{item['email_id']}@local.invalid>",
        }
        if settings.mail_mode == "file":
            directory = settings.storage / "mail"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{item['email_id']}.json").write_text(
                json.dumps(message, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            email = EmailMessage()
            for key, value in (
                ("From", message["from"]),
                ("To", message["to"]),
                ("Subject", message["subject"]),
                ("Message-ID", message["messageId"]),
            ):
                email[key] = value
            email.set_content(message["text"])
            port = int(os.getenv("SMTP_PORT", "587"))
            factory = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
            with factory(os.getenv("SMTP_HOST"), port, timeout=30) as smtp:
                if port != 465:
                    smtp.starttls(context=ssl.create_default_context())
                smtp.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", ""))
                smtp.send_message(email)
        with transaction() as db:
            changed = db.execute(
                "UPDATE email_outbox SET status='sent',body_enc=%s,lease_expires_at=NULL WHERE email_id=%s AND claim_token=%s AND status='sending'",
                (encrypt("Delivered; content removed"), item["email_id"], claim),
            )
            if changed:
                db.execute(
                    "UPDATE notifications SET email_status='sent',email_sent_at=UTC_TIMESTAMP(),email_attempt_count=%s WHERE email_claim_token=%s",
                    (item["attempt_count"] + 1, f"outbox:{item['email_id']}"),
                )
    except Exception:
        with transaction() as db:
            status = "failed" if item["attempt_count"] + 1 >= 4 else "pending"
            changed = db.execute(
                "UPDATE email_outbox SET status=%s,last_error='EMAIL_FAILED',next_attempt_at=%s,lease_expires_at=NULL WHERE email_id=%s AND claim_token=%s AND status='sending'",
                (status, now() + timedelta(seconds=60 * 2 ** item["attempt_count"]), item["email_id"], claim),
            )
            if changed:
                db.execute(
                    "UPDATE notifications SET email_status=%s,email_attempt_count=%s,email_last_error='EMAIL_FAILED' WHERE email_claim_token=%s",
                    (
                        "failed" if status == "failed" else "pending",
                        item["attempt_count"] + 1,
                        f"outbox:{item['email_id']}",
                    ),
                )
    return True
