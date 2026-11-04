"""Chạy riêng: python -m app.worker. Không khởi động worker trong HTTP process."""

import logging
import signal
import threading
import time
from app.config import settings, validate_config
from app.db import transaction
from app.jobs.publishing import queue_due_schedules, recover_expired_jobs, publish_one
from app.jobs.mail import notification_emails, send_one_email
from app.jobs.reports import export_one_report
from app.jobs.sync import refresh_tokens, sync_accounts, sync_post_snapshots


def maintenance():
    with transaction() as db:
        db.execute(
            "UPDATE workspace_invitations SET status='expired' WHERE status='pending' AND expires_at<=UTC_TIMESTAMP()"
        )
        for table, condition in (
            ("oauth_states", "expires_at<DATE_SUB(UTC_TIMESTAMP(),INTERVAL 1 DAY)"),
            ("oauth_connections", "expires_at<UTC_TIMESTAMP()"),
            ("webhook_receipts", "received_at<DATE_SUB(UTC_TIMESTAMP(),INTERVAL 7 DAY)"),
            ("auth_action_tokens", "expires_at<DATE_SUB(UTC_TIMESTAMP(),INTERVAL 7 DAY)"),
            ("auth_sessions", "expires_at<DATE_SUB(UTC_TIMESTAMP(),INTERVAL 7 DAY)"),
        ):
            db.execute(f"DELETE FROM {table} WHERE {condition}")


def tick(sync=True):
    recover_expired_jobs()
    queue_due_schedules()
    for _ in range(20):
        if not publish_one():
            break
    notification_emails()
    for _ in range(20):
        if not send_one_email():
            break
    for _ in range(5):
        if not export_one_report():
            break
    if sync:
        maintenance()
        refresh_tokens()
        sync_accounts()
        sync_post_snapshots()


def main():
    validate_config()
    stopped = threading.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stopped.set())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("worker=started social_mode=%s", settings.social_mode)
    last_sync = 0
    while not stopped.is_set():
        try:
            sync = time.monotonic() - last_sync > 60
            tick(sync)
            if sync:
                last_sync = time.monotonic()
        except Exception as error:
            logging.error("worker=failed code=%s", type(error).__name__)
        stopped.wait(3)


if __name__ == "__main__":
    main()
