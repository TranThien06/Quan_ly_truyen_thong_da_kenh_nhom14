import logging
from datetime import timedelta
from app.common import now
from app.config import settings
from app.db import transaction, json_value
from app.events import notify_managers
from app.modules.channels import credentials
from app.security import encrypt
from app.social import refresh_remote, account_metrics, post_metrics, inbox_remote, ProviderError


def refresh_tokens():
    with transaction() as db:
        candidates = db.all("""SELECT a.social_account_id FROM social_accounts a JOIN social_account_tokens t ON t.social_account_id=a.social_account_id
            WHERE a.status='connected' AND t.expires_at<DATE_ADD(UTC_TIMESTAMP(),INTERVAL 1 HOUR)
            AND (t.last_refreshed_at IS NULL OR t.last_refreshed_at<DATE_SUB(UTC_TIMESTAMP(),INTERVAL 10 MINUTE)) LIMIT 20""")
    for candidate in candidates:
        with transaction() as db:
            account = db.one(
                "SELECT a.*,p.code FROM social_accounts a JOIN platforms p ON p.platform_id=a.platform_id WHERE a.social_account_id=%s FOR UPDATE",
                (candidate["social_account_id"],),
            )
            current = db.one(
                "SELECT * FROM social_account_tokens WHERE social_account_id=%s FOR UPDATE",
                (account["social_account_id"],),
            )
            if (
                not current
                or account["status"] != "connected"
                or (
                    current["last_refreshed_at"]
                    and current["last_refreshed_at"] > now() - timedelta(minutes=10)
                )
            ):
                continue
            db.execute(
                "UPDATE social_account_tokens SET last_refreshed_at=UTC_TIMESTAMP() WHERE social_account_id=%s",
                (account["social_account_id"],),
            )
            try:
                auth = credentials(db, account["social_account_id"])
                fresh = refresh_remote(account, auth)
                db.execute(
                    "UPDATE social_account_tokens SET access_token_enc=%s,refresh_token_enc=%s,expires_at=%s,refresh_fail_count=0 WHERE social_account_id=%s",
                    (
                        encrypt(fresh["access_token"]),
                        encrypt(fresh.get("refresh_token") or auth["refresh_token"]),
                        now() + timedelta(seconds=fresh["expires_in"]),
                        account["social_account_id"],
                    ),
                )
            except ProviderError as error:
                db.execute(
                    "UPDATE social_account_tokens SET refresh_fail_count=refresh_fail_count+1 WHERE social_account_id=%s",
                    (account["social_account_id"],),
                )
                if current["refresh_fail_count"] >= 2 or error.code == "RECONNECT_REQUIRED":
                    db.execute(
                        "UPDATE social_accounts SET status='expired',status_message='RECONNECT_REQUIRED' WHERE social_account_id=%s",
                        (account["social_account_id"],),
                    )
                    notify_managers(
                        db,
                        account["workspace_id"],
                        "token_expired",
                        "Cần kết nối lại kênh",
                        "social_account",
                        account["social_account_id"],
                    )


def sync_account(db, account):
    auth = credentials(db, account["social_account_id"])
    metrics = account_metrics(account, auth["access_token"])
    yesterday = db.one(
        "SELECT followers_count FROM account_daily_metrics WHERE social_account_id=%s AND metric_date=DATE_SUB(UTC_DATE(),INTERVAL 1 DAY)",
        (account["social_account_id"],),
    )
    gain = (
        metrics["followers_count"] - yesterday["followers_count"]
        if metrics.get("followers_count") is not None
        and yesterday
        and yesterday["followers_count"] is not None
        else None
    )
    db.execute(
        """INSERT INTO account_daily_metrics(social_account_id,metric_date,followers_count,followers_gain,impressions,reach,engagements,extra_metrics)
        VALUES (%s,UTC_DATE(),%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE followers_count=VALUES(followers_count),followers_gain=VALUES(followers_gain),
        impressions=VALUES(impressions),reach=VALUES(reach),engagements=VALUES(engagements),extra_metrics=VALUES(extra_metrics),synced_at=UTC_TIMESTAMP()""",
        (
            account["social_account_id"],
            metrics.get("followers_count"),
            gain,
            metrics.get("impressions"),
            metrics.get("reach"),
            metrics.get("engagements"),
            json_value(metrics.get("extra_metrics", {})),
        ),
    )


def sync_inbox(db, account):
    auth = credentials(db, account["social_account_id"])
    variants = db.all(
        "SELECT variant_id,external_post_id FROM post_variants WHERE social_account_id=%s AND status='published' ORDER BY published_at DESC LIMIT 100",
        (account["social_account_id"],),
    )
    threads = inbox_remote(account, auth["access_token"], variants)
    for thread in threads:
        variant_id = None
        if thread.get("external_post_id"):
            row = db.one(
                "SELECT variant_id FROM post_variants WHERE social_account_id=%s AND external_post_id=%s",
                (account["social_account_id"], thread["external_post_id"]),
            )
            variant_id = row["variant_id"] if row else None
        db.execute(
            """INSERT INTO conversations(workspace_id,social_account_id,variant_id,conversation_type,external_id,participant_name,participant_external_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE participant_name=VALUES(participant_name),participant_external_id=VALUES(participant_external_id)""",
            (
                account["workspace_id"],
                account["social_account_id"],
                variant_id,
                thread["conversation_type"],
                thread["external_id"],
                thread.get("participant_name"),
                thread.get("participant_external_id"),
            ),
        )
        conversation = db.one(
            "SELECT conversation_id FROM conversations WHERE social_account_id=%s AND external_id=%s",
            (account["social_account_id"], thread["external_id"]),
        )
        for message in thread["messages"]:
            db.execute(
                """INSERT INTO messages(conversation_id,external_message_id,direction,sender_name,content,sent_at) VALUES (%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE external_message_id=VALUES(external_message_id)""",
                (
                    conversation["conversation_id"],
                    message["external_message_id"],
                    message.get("direction", "inbound"),
                    message.get("sender_name"),
                    message.get("content"),
                    message["sent_at"],
                ),
            )
        db.execute(
            "UPDATE conversations SET last_message_at=(SELECT MAX(sent_at) FROM messages WHERE conversation_id=%s) WHERE conversation_id=%s",
            (conversation["conversation_id"], conversation["conversation_id"]),
        )


def sync_accounts():
    for kind, column, interval in (
        ("metrics", "last_metrics_sync_at", "1 DAY"),
        ("inbox", "last_inbox_sync_at", "5 MINUTE"),
    ):
        with transaction() as db:
            candidates = db.all(
                f"SELECT social_account_id FROM social_accounts WHERE status='connected' AND ({column} IS NULL OR {column}<DATE_SUB(UTC_TIMESTAMP(),INTERVAL {interval})) ORDER BY social_account_id LIMIT 20"
            )
        for candidate in candidates:
            try:
                with transaction() as db:
                    account = db.one(
                        f"SELECT a.*,p.code FROM social_accounts a JOIN platforms p ON p.platform_id=a.platform_id WHERE a.social_account_id=%s AND a.status='connected' AND (a.{column} IS NULL OR a.{column}<DATE_SUB(UTC_TIMESTAMP(),INTERVAL {interval})) FOR UPDATE",
                        (candidate["social_account_id"],),
                    )
                    if not account:
                        continue
                    db.execute(
                        f"UPDATE social_accounts SET {column}=UTC_TIMESTAMP() WHERE social_account_id=%s",
                        (account["social_account_id"],),
                    )
                    if kind == "inbox" and account["code"] != "facebook" and settings.social_mode == "live":
                        continue
                    try:
                        (sync_account if kind == "metrics" else sync_inbox)(db, account)
                    except ProviderError as error:
                        db.execute(
                            "UPDATE social_accounts SET status_message=%s WHERE social_account_id=%s",
                            (error.code, account["social_account_id"]),
                        )
                        if error.code == "TOKEN_EXPIRED":
                            db.execute(
                                "UPDATE social_accounts SET status='expired' WHERE social_account_id=%s",
                                (account["social_account_id"],),
                            )
                            notify_managers(
                                db,
                                account["workspace_id"],
                                "token_expired",
                                "Cần kết nối lại kênh",
                                "social_account",
                                account["social_account_id"],
                            )
            except Exception as error:
                logging.error("task=sync_%s code=%s", kind, type(error).__name__)


def sync_post_snapshots():
    for hours in (1, 24, 168):
        with transaction() as db:
            candidates = db.all(
                "SELECT v.variant_id FROM post_variants v WHERE v.status='published' AND v.published_at<=DATE_SUB(UTC_TIMESTAMP(),INTERVAL %s HOUR) AND NOT EXISTS (SELECT 1 FROM metric_sync_points s WHERE s.variant_id=v.variant_id AND s.checkpoint_hours=%s) LIMIT 20",
                (hours, hours),
            )
        for candidate in candidates:
            try:
                with transaction() as db:
                    variant = db.one(
                        "SELECT * FROM post_variants WHERE variant_id=%s FOR UPDATE",
                        (candidate["variant_id"],),
                    )
                    if db.one(
                        "SELECT 1 FROM metric_sync_points WHERE variant_id=%s AND checkpoint_hours=%s",
                        (variant["variant_id"], hours),
                    ):
                        continue
                    account = db.one(
                        "SELECT a.*,p.code FROM social_accounts a JOIN platforms p ON p.platform_id=a.platform_id WHERE a.social_account_id=%s",
                        (variant["social_account_id"],),
                    )
                    if account["status"] != "connected":
                        continue
                    metrics = post_metrics(
                        account, variant, credentials(db, account["social_account_id"])["access_token"]
                    )
                    db.execute(
                        "INSERT INTO post_metric_snapshots(variant_id,collected_at,views,likes,comments,shares,extra_metrics) VALUES (%s,UTC_TIMESTAMP(),%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE views=VALUES(views),likes=VALUES(likes),comments=VALUES(comments),shares=VALUES(shares),extra_metrics=VALUES(extra_metrics)",
                        (
                            variant["variant_id"],
                            metrics.get("views"),
                            metrics.get("likes"),
                            metrics.get("comments"),
                            metrics.get("shares"),
                            json_value(metrics.get("extra_metrics", {})),
                        ),
                    )
                    db.insert(
                        "metric_sync_points", {"variant_id": variant["variant_id"], "checkpoint_hours": hours}
                    )
            except Exception as error:
                logging.error("task=post_metrics code=%s", type(error).__name__)
