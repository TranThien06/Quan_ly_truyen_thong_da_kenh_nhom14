import json
from datetime import timedelta
from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from app.common import Input, Id, Pagination, now, ok
from app.db import transaction
from app.dependencies import current_user, membership, workspace
from app.errors import check
from app.events import audit, change_status, notify
from app.security import token, hash_token, encrypt, decrypt
from app import social

router = APIRouter(tags=["Kênh và OAuth"])
Platform = Literal["facebook", "instagram", "tiktok"]


class SelectionInput(Input):
    connection_token: str = Field(min_length=1, max_length=100)
    external_account_ids: list[str] = Field(min_length=1, max_length=100)


def account(db, workspace_id, account_id, lock=False):
    row = db.one(
        "SELECT a.*,p.code FROM social_accounts a JOIN platforms p ON p.platform_id=a.platform_id WHERE a.workspace_id=%s AND a.social_account_id=%s"
        + (" FOR UPDATE" if lock else ""),
        (workspace_id, account_id),
    )
    check(row, 404, "CHANNEL_NOT_FOUND", "Không tìm thấy kênh")
    return row


def credentials(db, account_id):
    row = db.one("SELECT * FROM social_account_tokens WHERE social_account_id=%s", (account_id,))
    check(row, 409, "TOKEN_MISSING", "Cần kết nối lại kênh")
    return {
        "access_token": decrypt(row["access_token_enc"]),
        "refresh_token": decrypt(row["refresh_token_enc"]),
    }


@router.get("/platforms")
def platforms(user=Depends(current_user)):
    with transaction() as db:
        return ok(db.all("SELECT * FROM platforms WHERE is_active=1"))


@router.get("/platforms/{platform}/capabilities")
def capabilities(platform: Platform, user=Depends(current_user)):
    return ok(social.capabilities(platform))


@router.get("/workspaces/{workspace_id}/social-accounts", dependencies=[Depends(workspace())])
def list_accounts(workspace_id: Id, paging: Pagination = Depends()):
    with transaction() as db:
        return ok(
            db.all(
                "SELECT a.*,p.code platform FROM social_accounts a JOIN platforms p ON p.platform_id=a.platform_id WHERE a.workspace_id=%s ORDER BY a.social_account_id LIMIT %s OFFSET %s",
                (workspace_id, *paging.sql),
            )
        )


@router.post(
    "/workspaces/{workspace_id}/social-accounts/oauth/complete-selection",
    dependencies=[Depends(workspace("channel.manage"))],
)
def complete_selection(workspace_id: Id, data: SelectionInput, request: Request):
    with transaction() as db:
        row = db.one(
            "SELECT * FROM oauth_connections WHERE connection_hash=%s AND workspace_id=%s AND user_id=%s AND expires_at>UTC_TIMESTAMP() FOR UPDATE",
            (hash_token(data.connection_token), workspace_id, request.state.user["user_id"]),
        )
        check(row, 422, "INVALID_CONNECTION", "Kết nối tạm không hợp lệ hoặc hết hạn")
        membership(db, workspace_id, request.state.user["user_id"], "channel.manage")
        available = json.loads(decrypt(row["accounts_enc"]))
        results = []
        for external_id in dict.fromkeys(data.external_account_ids):
            item = next((a for a in available if a["external_account_id"] == external_id), None)
            check(item, 422, "INVALID_ACCOUNT", "Tài khoản không thuộc kết quả OAuth")
            platform = db.one("SELECT platform_id FROM platforms WHERE code=%s", (item["platform"],))
            linked_id = None
            if item["platform"] == "instagram":
                linked = db.one(
                    """SELECT a.social_account_id FROM social_accounts a JOIN platforms p ON p.platform_id=a.platform_id
                    WHERE a.workspace_id=%s AND a.external_account_id=%s AND p.code='facebook'
                    AND a.account_type='page' AND a.status='connected' FOR UPDATE""",
                    (workspace_id, item.get("linked_page_external_id")),
                )
                check(linked, 422, "LINK_PAGE_FIRST", "Kết nối Facebook Page liên kết trước")
                linked_id = linked["social_account_id"]
            db.execute(
                """INSERT INTO social_accounts(workspace_id,platform_id,external_account_id,account_name,account_type,avatar_url,linked_account_id,connected_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE account_name=VALUES(account_name),avatar_url=VALUES(avatar_url),linked_account_id=VALUES(linked_account_id),status='connected',status_message=NULL,disconnected_at=NULL""",
                (
                    workspace_id,
                    platform["platform_id"],
                    external_id,
                    item["account_name"],
                    item["account_type"],
                    item.get("avatar_url"),
                    linked_id,
                    request.state.user["user_id"],
                ),
            )
            saved = db.one(
                "SELECT social_account_id FROM social_accounts WHERE workspace_id=%s AND platform_id=%s AND external_account_id=%s",
                (workspace_id, platform["platform_id"], external_id),
            )
            expires = now() + timedelta(seconds=item["expires_in"]) if item.get("expires_in") else None
            db.execute(
                """INSERT INTO social_account_tokens(social_account_id,access_token_enc,refresh_token_enc,expires_at) VALUES (%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE access_token_enc=VALUES(access_token_enc),refresh_token_enc=VALUES(refresh_token_enc),expires_at=VALUES(expires_at),refresh_fail_count=0""",
                (
                    saved["social_account_id"],
                    encrypt(item["access_token"]),
                    encrypt(item.get("refresh_token")),
                    expires,
                ),
            )
            audit(db, request, "channel.connect", "social_account", saved["social_account_id"])
            results.append(saved)
        db.execute("DELETE FROM oauth_connections WHERE connection_hash=%s", (row["connection_hash"],))
    return ok(results)


@router.post(
    "/workspaces/{workspace_id}/social-accounts/oauth/{platform}",
    dependencies=[Depends(workspace("channel.manage"))],
)
def start_oauth(workspace_id: Id, platform: Platform, request: Request):
    raw, verifier = token(), token()
    url = social.authorize_url(platform, raw, verifier)
    with transaction() as db:
        db.insert(
            "oauth_states",
            {
                "state_hash": hash_token(raw),
                "workspace_id": workspace_id,
                "user_id": request.state.user["user_id"],
                "platform_code": platform,
                "verifier_enc": encrypt(verifier),
                "expires_at": now() + timedelta(minutes=10),
            },
        )
    return ok({"authorization_url": url})


@router.get("/oauth/{platform}/callback")
def callback(
    platform: Platform,
    state: str = Query(min_length=1, max_length=100),
    code: str | None = Query(default=None, min_length=1, max_length=2048),
    error: str | None = Query(default=None, max_length=100),
):
    check(error or code, 422, "INVALID_OAUTH_CALLBACK", "Callback thiếu mã ủy quyền")
    with transaction() as db:
        row = db.one(
            "SELECT * FROM oauth_states WHERE state_hash=%s AND platform_code=%s AND consumed_at IS NULL AND expires_at>UTC_TIMESTAMP() FOR UPDATE",
            (hash_token(state), platform),
        )
        check(row, 422, "INVALID_OAUTH_STATE", "OAuth state hết hạn hoặc đã dùng")
        membership(db, row["workspace_id"], row["user_id"], "channel.manage")
        db.execute(
            "UPDATE oauth_states SET consumed_at=UTC_TIMESTAMP() WHERE state_hash=%s", (row["state_hash"],)
        )
    if platform == "tiktok":
        check(
            error not in ("invalid_scope", "scope_not_authorized"),
            403,
            "TIKTOK_PERMISSIONS_MISSING",
            "TikTok từ chối quyền truy cập được yêu cầu",
        )
        check(not error, 400, "OAUTH_DENIED", "Yêu cầu cấp quyền TikTok bị từ chối hoặc hủy")
    else:
        check(not error, 400, "OAUTH_DENIED", "Yêu cầu cấp quyền OAuth bị từ chối hoặc hủy")
    try:
        accounts = social.exchange_code(platform, code, decrypt(row["verifier_enc"]))
    except social.ProviderError as exc:
        check(
            platform != "tiktok" or exc.code != "PROVIDER_SCOPE_MISSING",
            403,
            "TIKTOK_PERMISSIONS_MISSING",
            "TikTok chưa cấp quyền truy cập được yêu cầu",
        )
        raise
    raw = token()
    with transaction() as db:
        db.insert(
            "oauth_connections",
            {
                "connection_hash": hash_token(raw),
                "workspace_id": row["workspace_id"],
                "user_id": row["user_id"],
                "accounts_enc": encrypt(json.dumps(accounts)),
                "expires_at": now() + timedelta(minutes=10),
            },
        )
    return ok(
        {
            "connection_token": raw,
            "workspace_id": row["workspace_id"],
            "accounts": [
                {
                    "external_account_id": a["external_account_id"],
                    "account_name": a["account_name"],
                    "platform": a["platform"],
                    "linked_page_external_id": a.get("linked_page_external_id"),
                }
                for a in accounts
            ],
        }
    )


@router.post(
    "/workspaces/{workspace_id}/social-accounts/{account_id}/disconnect",
    dependencies=[Depends(workspace("channel.manage"))],
)
def disconnect(workspace_id: Id, account_id: Id, request: Request):
    with transaction() as db:
        posts = db.all(
            "SELECT DISTINCT p.post_id FROM posts p JOIN post_variants v ON v.post_id=p.post_id WHERE v.workspace_id=%s AND v.social_account_id=%s ORDER BY p.post_id",
            (workspace_id, account_id),
        )
        for p in posts:
            db.one("SELECT post_id FROM posts WHERE post_id=%s FOR UPDATE", (p["post_id"],))
        account(db, workspace_id, account_id, True)
        check(
            not db.one(
                "SELECT 1 FROM publish_jobs j JOIN post_variants v ON v.variant_id=j.variant_id WHERE v.social_account_id=%s AND j.status='running'",
                (account_id,),
            ),
            409,
            "PUBLISH_STARTED",
            "Đang gửi bài, thử lại sau",
        )
        db.execute("DELETE FROM social_account_tokens WHERE social_account_id=%s", (account_id,))
        db.execute(
            "UPDATE social_accounts SET status='disconnected',disconnected_at=UTC_TIMESTAMP() WHERE social_account_id=%s",
            (account_id,),
        )
        db.execute(
            "UPDATE schedules s JOIN post_variants v ON v.variant_id=s.variant_id SET s.status='cancelled',s.cancelled_at=UTC_TIMESTAMP() WHERE v.social_account_id=%s AND s.status IN ('pending','queued')",
            (account_id,),
        )
        db.execute(
            "UPDATE publish_jobs j JOIN post_variants v ON v.variant_id=j.variant_id SET j.status='failed',j.error_code='CHANNEL_DISCONNECTED',j.finished_at=UTC_TIMESTAMP() WHERE v.social_account_id=%s AND j.status='queued'",
            (account_id,),
        )
        db.execute(
            "UPDATE post_variants SET status='cancelled' WHERE social_account_id=%s AND status IN ('draft','scheduled','publishing')",
            (account_id,),
        )
        for item in posts:
            post = db.one("SELECT * FROM posts WHERE post_id=%s", (item["post_id"],))
            states = [
                v["status"]
                for v in db.all("SELECT status FROM post_variants WHERE post_id=%s", (post["post_id"],))
            ]
            if all(s == "cancelled" for s in states):
                change_status(db, post, "cancelled", request.state.user["user_id"], "Ngắt kết nối kênh")
            elif "published" in states and not any(s in ("publishing", "scheduled") for s in states):
                change_status(db, post, "partially_failed", request.state.user["user_id"])
            notify(
                db,
                workspace_id,
                post["created_by"],
                "channel_disconnected",
                "Kênh ngắt kết nối; lịch chưa đăng đã hủy",
                "post",
                post["post_id"],
            )
        audit(db, request, "channel.disconnect", "social_account", account_id)
    return ok({"status": "disconnected"})


@router.post(
    "/workspaces/{workspace_id}/social-accounts/{account_id}/sync/{kind}",
    status_code=202,
    dependencies=[Depends(workspace("channel.manage"))],
)
def request_sync(workspace_id: Id, account_id: Id, kind: Literal["metrics", "inbox"], request: Request):
    with transaction() as db:
        row = account(db, workspace_id, account_id, True)
        check(row["status"] == "connected", 409, "CHANNEL_UNAVAILABLE", "Kênh chưa kết nối")
        column = "last_metrics_sync_at" if kind == "metrics" else "last_inbox_sync_at"
        db.execute(f"UPDATE social_accounts SET {column}=NULL WHERE social_account_id=%s", (account_id,))
        audit(db, request, "channel.sync_request", "social_account", account_id, new={"kind": kind})
    return ok({"status": "pending", "kind": kind})
