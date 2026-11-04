"""Adapter mạng xã hội. Mock không gọi mạng và luôn trả URL có nhãn demo."""

import base64
import hashlib
import os
from urllib.parse import urlencode
import httpx
from app.config import settings
from app.errors import check
from app.modules.media import signed_url


class ProviderError(Exception):
    def __init__(self, code, retryable=False, ambiguous=False, retry_after=60):
        super().__init__(code)
        self.code, self.retryable, self.ambiguous, self.retry_after = code, retryable, ambiguous, retry_after


def call(url, *, method="GET", access_token=None, params=None, data=None, payload=None, publishing=False):
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    try:
        response = httpx.request(
            method, url, params=params, data=data, json=payload, headers=headers, timeout=25
        )
    except httpx.RequestError:
        raise ProviderError("PROVIDER_NETWORK", retryable=not publishing, ambiguous=publishing) from None
    try:
        result = response.json()
    except ValueError:
        result = {}
    error = result.get("error")
    code = str(error.get("code")) if isinstance(error, dict) else str(error or "")
    if not response.is_success or (error and code != "ok"):
        rate = response.status_code == 429 or code in ("4", "17", "32", "613")
        expired = response.status_code == 401 or code == "190"
        retry_after = response.headers.get("retry-after", "60")
        raise ProviderError(
            "TOKEN_EXPIRED" if expired else "RATE_LIMIT" if rate else "PROVIDER_REJECTED",
            retryable=rate or (not publishing and response.status_code >= 500),
            ambiguous=publishing and response.status_code >= 500,
            retry_after=int(retry_after) if retry_after.isdigit() else 60,
        )
    return result


def graph_base():
    import re

    version = os.getenv("META_API_VERSION", "")
    check(
        re.fullmatch(r"v\d+\.\d+", version), 503, "META_NOT_CONFIGURED", "Cần META_API_VERSION đã được duyệt"
    )
    return f"https://graph.facebook.com/{version}"


def graph(path, access_token, params=None, method="GET", publishing=False):
    return call(
        f"{graph_base()}/{path}",
        method=method,
        access_token=access_token,
        **({"params": params} if method == "GET" else {"data": params}),
        publishing=publishing,
    )


def graph_list(path, access_token, params):
    rows = []
    params = dict(params)
    for _ in range(100):
        result = graph(path, access_token, params)
        rows.extend(result.get("data", []))
        if not result.get("paging", {}).get("next"):
            return rows
        cursor = result.get("paging", {}).get("cursors", {}).get("after")
        check(cursor, 502, "PAGINATION_CURSOR_MISSING", "Nền tảng chưa trả cursor")
        params["after"] = cursor
    raise ProviderError("SYNC_TOO_LARGE")


def tiktok(path, access_token, payload=None):
    return call(
        f"https://open.tiktokapis.com/v2/{path}",
        method="POST" if payload else "GET",
        access_token=access_token,
        payload=payload,
        publishing=bool(payload and "init" in path),
    )


def demo_id(variant_id):
    return "demo_" + hashlib.sha256(str(variant_id).encode()).hexdigest()[:20]


def capabilities(platform):
    return {
        "platform": platform,
        "mode": settings.social_mode,
        "publish": True,
        "metrics": True,
        "inbox": platform == "facebook",
        "formats": ["video"] if platform == "tiktok" else ["text", "image", "video"],
        "live_verified": False,
    }


def authorize_url(platform, state, verifier):
    redirect = f"{settings.app_url}/api/v1/oauth/{platform}/callback"
    if settings.social_mode == "mock":
        return f"{redirect}?{urlencode({'state': state, 'code': 'mock'})}"
    if platform == "tiktok":
        check(os.getenv("TIKTOK_CLIENT_KEY"), 503, "NOT_CONFIGURED", "Thiếu TIKTOK_CLIENT_KEY")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params = {
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "response_type": "code",
            "scope": "user.info.basic,user.info.stats,video.publish,video.list",
            "redirect_uri": redirect,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params)
    graph_base()
    check(os.getenv("META_APP_ID"), 503, "NOT_CONFIGURED", "Thiếu META_APP_ID")
    params = {
        "client_id": os.environ["META_APP_ID"],
        "redirect_uri": redirect,
        "state": state,
        "response_type": "code",
        "scope": "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish,instagram_manage_insights,pages_messaging,pages_manage_engagement,pages_read_user_content",
    }
    return f"https://www.facebook.com/{os.environ['META_API_VERSION']}/dialog/oauth?{urlencode(params)}"


def exchange_code(platform, code, verifier):
    if settings.social_mode == "mock":
        return [
            {
                "external_account_id": f"mock-{platform}",
                "account_name": f"DEMO {platform}",
                "account_type": "page"
                if platform == "facebook"
                else "business"
                if platform == "instagram"
                else "creator",
                "platform": platform,
                "access_token": "mock-token",
                "refresh_token": "mock-refresh",
                "expires_in": 86400,
            }
        ]
    redirect = f"{settings.app_url}/api/v1/oauth/{platform}/callback"
    if platform == "tiktok":
        result = call(
            "https://open.tiktokapis.com/v2/oauth/token/",
            method="POST",
            data={
                "client_key": os.getenv("TIKTOK_CLIENT_KEY"),
                "client_secret": os.getenv("TIKTOK_CLIENT_SECRET"),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect,
                "code_verifier": verifier,
            },
        )
        user = tiktok("user/info/?fields=open_id,display_name,avatar_url", result["access_token"])["data"][
            "user"
        ]
        return [
            {
                **result,
                "external_account_id": user["open_id"],
                "account_name": user["display_name"],
                "avatar_url": user.get("avatar_url"),
                "account_type": "creator",
                "platform": platform,
            }
        ]
    result = call(
        f"{graph_base()}/oauth/access_token",
        params={
            "client_id": os.getenv("META_APP_ID"),
            "client_secret": os.getenv("META_APP_SECRET"),
            "code": code,
            "redirect_uri": redirect,
        },
    )
    pages = graph_list(
        "me/accounts",
        result["access_token"],
        {
            "fields": "id,name,access_token,instagram_business_account{id,username,profile_picture_url}",
            "limit": 100,
        },
    )
    accounts = []
    for page in pages:
        if platform == "facebook":
            accounts.append(
                {
                    "external_account_id": page["id"],
                    "account_name": page["name"],
                    "account_type": "page",
                    "platform": platform,
                    "access_token": page["access_token"],
                }
            )
        elif page.get("instagram_business_account"):
            user = page["instagram_business_account"]
            accounts.append(
                {
                    "external_account_id": user["id"],
                    "account_name": user["username"],
                    "avatar_url": user.get("profile_picture_url"),
                    "account_type": "business",
                    "platform": platform,
                    "access_token": page["access_token"],
                    "linked_page_external_id": page["id"],
                }
            )
    return accounts


def publish_remote(account, variant, media, caption, access_token, progress, save_progress):
    from app.db import json_value

    options = variant["platform_options"] or {}
    if settings.social_mode == "mock":
        if options.get("mock_failure") == "temporary":
            raise ProviderError("RATE_LIMIT", retryable=True, retry_after=1)
        if options.get("mock_failure") == "ambiguous":
            raise ProviderError("PROVIDER_NETWORK", ambiguous=True)
        external_id = demo_id(variant["variant_id"])
        return {
            "external_post_id": external_id,
            "external_post_url": f"https://example.com/demo/{account['code']}/{external_id}",
        }
    account_id = account["external_account_id"]
    if account["code"] == "facebook":
        if not media:
            result = graph(f"{account_id}/feed", access_token, {"message": caption}, "POST", True)
        elif len(media) == 1 and media[0]["media_type"] == "video":
            result = graph(
                f"{account_id}/videos",
                access_token,
                {"file_url": signed_url(media[0]), "description": caption},
                "POST",
                True,
            )
        else:
            check(
                all(m["media_type"] == "image" for m in media),
                422,
                "UNSUPPORTED_FORMAT",
                "Không trộn ảnh và video",
            )
            attachments = [
                {
                    "media_fbid": graph(
                        f"{account_id}/photos",
                        access_token,
                        {"url": signed_url(m), "published": "false"},
                        "POST",
                    )["id"]
                }
                for m in media
            ]
            result = graph(
                f"{account_id}/feed",
                access_token,
                {"message": caption, "attached_media": json_value(attachments)},
                "POST",
                True,
            )
        external_id = result.get("post_id") or result.get("id")
        return {
            "external_post_id": external_id,
            "external_post_url": f"https://www.facebook.com/{external_id}",
        }
    if account["code"] == "instagram":
        check(len(media) == 1, 422, "UNSUPPORTED_FORMAT", "Live hiện hỗ trợ một ảnh hoặc một Reel")
        item = media[0]
        payload = {
            "caption": caption,
            **(
                {"media_type": "REELS", "video_url": signed_url(item)}
                if item["media_type"] == "video"
                else {"image_url": signed_url(item)}
            ),
        }
        container_id = progress.get("container_id")
        if not container_id:
            container_id = graph(f"{account_id}/media", access_token, payload, "POST")["id"]
            save_progress({"container_id": container_id})
        status = graph(container_id, access_token, {"fields": "status_code"})["status_code"]
        if status in ("ERROR", "EXPIRED"):
            raise ProviderError("CONTAINER_FAILED")
        if status != "FINISHED":
            return {"pending": True}
        published = graph(
            f"{account_id}/media_publish", access_token, {"creation_id": container_id}, "POST", True
        )
        url = None
        try:
            url = graph(published["id"], access_token, {"fields": "permalink"}).get("permalink")
        except ProviderError:
            pass  # Đã có ID thành công: không gửi lại chỉ vì đọc permalink lỗi.
        return {"external_post_id": published["id"], "external_post_url": url}
    check(
        options.get("privacy_level"), 422, "PRIVACY_REQUIRED", "TikTok cần privacy_level do người dùng chọn"
    )
    publish_id = progress.get("publish_id")
    if not publish_id:
        result = tiktok(
            "post/publish/video/init/",
            access_token,
            {
                "post_info": {
                    "title": caption,
                    "privacy_level": options["privacy_level"],
                    "disable_comment": bool(options.get("disable_comment")),
                    "disable_duet": bool(options.get("disable_duet")),
                    "disable_stitch": bool(options.get("disable_stitch")),
                },
                "source_info": {"source": "PULL_FROM_URL", "video_url": signed_url(media[0])},
            },
        )
        publish_id = result["data"]["publish_id"]
        save_progress({"publish_id": publish_id})
    status = tiktok("post/publish/status/fetch/", access_token, {"publish_id": publish_id})["data"]
    if status.get("status") == "FAILED":
        raise ProviderError("TIKTOK_PUBLISH_FAILED")
    ids = status.get("publicaly_available_post_id") or []
    if status.get("status") != "PUBLISH_COMPLETE" or not ids:
        return {"pending": True}
    return {"external_post_id": ids[0], "external_post_url": None}


def verify_remote_post(account, variant, external_id, access_token):
    from app.common import utc

    if settings.social_mode == "mock":
        check(
            external_id == demo_id(variant["variant_id"]),
            422,
            "POST_NOT_VERIFIED",
            "Mã demo không khớp variant",
        )
        return {"url": f"https://example.com/demo/{account['code']}/{external_id}"}
    if account["code"] == "facebook":
        item = graph(external_id, access_token, {"fields": "id,from,permalink_url,created_time"})
        check(
            item.get("from", {}).get("id") == account["external_account_id"],
            422,
            "WRONG_ACCOUNT",
            "Bài không thuộc tài khoản kênh",
        )
        return {"url": item.get("permalink_url"), "published_at": utc(item["created_time"])}
    if account["code"] == "instagram":
        owned = graph_list(
            f"{account['external_account_id']}/media",
            access_token,
            {"fields": "id,permalink,timestamp", "limit": 100},
        )
        item = next((p for p in owned if p["id"] == external_id), None)
        check(item, 422, "POST_NOT_VERIFIED", "Không tìm thấy bài trong tài khoản")
        return {"url": item.get("permalink"), "published_at": utc(item["timestamp"])}
    from datetime import datetime, timezone

    result = tiktok(
        "video/query/?fields=id,create_time,share_url",
        access_token,
        {"filters": {"video_ids": [external_id]}},
    )
    item = next((p for p in result.get("data", {}).get("videos", []) if p["id"] == external_id), None)
    check(item, 422, "POST_NOT_VERIFIED", "Không tìm thấy video trong tài khoản")
    return {
        "url": item.get("share_url"),
        "published_at": datetime.fromtimestamp(item["create_time"], timezone.utc).replace(tzinfo=None),
    }


def refresh_remote(account, credentials):
    if settings.social_mode == "mock":
        return {"access_token": "mock-refreshed", "refresh_token": "mock-refresh", "expires_in": 86400}
    if account["code"] == "tiktok":
        return call(
            "https://open.tiktokapis.com/v2/oauth/token/",
            method="POST",
            data={
                "client_key": os.getenv("TIKTOK_CLIENT_KEY"),
                "client_secret": os.getenv("TIKTOK_CLIENT_SECRET"),
                "grant_type": "refresh_token",
                "refresh_token": credentials["refresh_token"],
            },
        )
    raise ProviderError("RECONNECT_REQUIRED")


def account_metrics(account, access_token):
    if settings.social_mode == "mock":
        return {
            "followers_count": 100,
            "impressions": 500,
            "reach": 300,
            "engagements": 40,
            "extra_metrics": {"source": "mock"},
        }
    if account["code"] == "tiktok":
        user = tiktok("user/info/?fields=follower_count,likes_count,video_count", access_token)["data"][
            "user"
        ]
        return {
            "followers_count": user.get("follower_count"),
            "extra_metrics": {"likes_count": user.get("likes_count"), "video_count": user.get("video_count")},
        }
    data = graph(account["external_account_id"], access_token, {"fields": "followers_count"})
    return {
        "followers_count": data.get("followers_count"),
        "extra_metrics": {"source": "provider", "note": "Chỉ số chưa cung cấp giữ NULL"},
    }


def post_metrics(account, variant, access_token):
    if settings.social_mode == "mock":
        return {"likes": 20, "comments": 3, "shares": 2, "views": 100, "extra_metrics": {"source": "mock"}}
    if account["code"] == "tiktok":
        result = tiktok(
            "video/query/?fields=id,view_count,like_count,comment_count,share_count",
            access_token,
            {"filters": {"video_ids": [variant["external_post_id"]]}},
        )
        item = (result.get("data", {}).get("videos") or [{}])[0]
        return {
            "views": item.get("view_count"),
            "likes": item.get("like_count"),
            "comments": item.get("comment_count"),
            "shares": item.get("share_count"),
        }
    if account["code"] == "instagram":
        item = graph(variant["external_post_id"], access_token, {"fields": "like_count,comments_count"})
        return {"likes": item.get("like_count"), "comments": item.get("comments_count")}
    item = graph(
        variant["external_post_id"],
        access_token,
        {"fields": "shares,comments.limit(0).summary(true),reactions.limit(0).summary(true)"},
    )
    return {
        "likes": item.get("reactions", {}).get("summary", {}).get("total_count"),
        "comments": item.get("comments", {}).get("summary", {}).get("total_count"),
        "shares": item.get("shares", {}).get("count"),
    }


def inbox_remote(account, access_token, variants):
    from app.common import utc

    if settings.social_mode == "mock":
        return [
            {
                "external_id": f"thread-{account['social_account_id']}",
                "conversation_type": "direct_message",
                "participant_name": "Khách demo",
                "messages": [
                    {
                        "external_message_id": f"message-{account['social_account_id']}",
                        "content": "Xin chào nhóm 14",
                        "sent_at": "2026-01-01 00:00:00",
                        "sender_name": "Khách demo",
                    }
                ],
            }
        ]
    check(account["code"] == "facebook", 422, "UNSUPPORTED_CAPABILITY", "Chưa bật inbox live cho kênh này")
    account_id = account["external_account_id"]

    def messages(items):
        return [
            {
                "external_message_id": m["id"],
                "content": m.get("message", ""),
                "sender_name": m.get("from", {}).get("name"),
                "direction": "outbound" if m.get("from", {}).get("id") == account_id else "inbound",
                "sent_at": utc(m["created_time"]),
            }
            for m in items
        ]

    threads = []
    for thread in graph_list(
        f"{account_id}/conversations", access_token, {"fields": "id,participants", "limit": 100}
    ):
        participant = next(
            (p for p in thread.get("participants", {}).get("data", []) if p["id"] != account_id), {}
        )
        items = graph_list(
            f"{thread['id']}/messages", access_token, {"fields": "id,message,from,created_time", "limit": 100}
        )
        threads.append(
            {
                "external_id": thread["id"],
                "conversation_type": "direct_message",
                "participant_name": participant.get("name"),
                "participant_external_id": participant.get("id"),
                "messages": messages(items),
            }
        )
    for variant in variants:
        for comment in graph_list(
            f"{variant['external_post_id']}/comments",
            access_token,
            {"fields": "id,message,from,created_time", "limit": 100},
        ):
            replies = graph_list(
                f"{comment['id']}/comments",
                access_token,
                {"fields": "id,message,from,created_time", "limit": 100},
            )
            threads.append(
                {
                    "external_id": comment["id"],
                    "external_post_id": variant["external_post_id"],
                    "conversation_type": "comment_thread",
                    "participant_name": comment.get("from", {}).get("name"),
                    "participant_external_id": comment.get("from", {}).get("id"),
                    "messages": messages([comment, *replies]),
                }
            )
    return threads


def reply_remote(account, conversation, content, access_token, request_id):
    from app.db import json_value

    if settings.social_mode == "mock":
        return {"external_message_id": f"reply-{request_id}"}
    check(account["code"] == "facebook", 422, "UNSUPPORTED_CAPABILITY", "Chưa hỗ trợ trả lời kênh này")
    if conversation["conversation_type"] == "comment_thread":
        result = graph(
            f"{conversation['external_id']}/comments", access_token, {"message": content}, "POST", True
        )
        return {"external_message_id": result["id"]}
    check(conversation["participant_external_id"], 422, "MISSING_RECIPIENT", "Chưa có mã người nhận")
    result = graph(
        f"{account['external_account_id']}/messages",
        access_token,
        {
            "recipient": json_value({"id": conversation["participant_external_id"]}),
            "message": json_value({"text": content}),
            "messaging_type": "RESPONSE",
        },
        "POST",
        True,
    )
    return {"external_message_id": result["message_id"]}
