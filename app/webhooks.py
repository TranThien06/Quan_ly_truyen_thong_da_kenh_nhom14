import hashlib
import hmac
import os
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool
from app.db import transaction
from app.errors import check
from app.security import hash_token

router = APIRouter(tags=["Webhook"])


@router.get("/webhooks/meta", response_class=PlainTextResponse)
def verify(request: Request):
    query = request.query_params
    expected = os.getenv("META_WEBHOOK_VERIFY_TOKEN")
    check(
        expected
        and query.get("hub.mode") == "subscribe"
        and hmac.compare_digest(query.get("hub.verify_token", ""), expected),
        403,
        "WEBHOOK_REJECTED",
        "Webhook không hợp lệ",
    )
    return query.get("hub.challenge", "")


def record_webhook(payload, digest):
    with transaction() as db:
        if not db.execute("INSERT IGNORE INTO webhook_receipts(payload_hash) VALUES (%s)", (digest,)):
            return
        for entry in payload.get("entry", []):
            if isinstance(entry.get("id"), str):
                db.execute(
                    "UPDATE social_accounts a JOIN platforms p ON p.platform_id=a.platform_id SET a.last_inbox_sync_at=NULL WHERE p.code='facebook' AND a.external_account_id=%s AND a.status='connected'",
                    (entry["id"],),
                )


@router.post("/webhooks/meta")
async def receive(request: Request):
    raw = await request.body()
    secret = os.getenv("META_APP_SECRET", "")
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    supplied = request.headers.get("x-hub-signature-256", "")
    check(secret and hmac.compare_digest(supplied, expected), 403, "WEBHOOK_REJECTED", "Thiếu chữ ký hợp lệ")
    import json

    try:
        payload = json.loads(raw)
        check(
            isinstance(payload, dict) and isinstance(payload.get("entry", []), list),
            422,
            "INVALID_WEBHOOK",
            "Webhook không hợp lệ",
        )
    except ValueError:
        check(False, 422, "INVALID_WEBHOOK", "Webhook không phải JSON")
    await run_in_threadpool(record_webhook, payload, hash_token(raw))
    return {"received": True}
