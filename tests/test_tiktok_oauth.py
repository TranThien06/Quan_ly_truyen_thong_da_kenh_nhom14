from urllib.parse import parse_qs, urlsplit

import pytest

from app import social
from app.errors import ApiError


@pytest.mark.parametrize("granted", ["user.info.basic", "video.list"])
def test_tiktok_web_exchange(monkeypatch, granted):
    monkeypatch.setattr(social.settings, "social_mode", "live")
    monkeypatch.setattr(social.settings, "app_url", "https://api.example.com")
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "secret")
    monkeypatch.delenv("TIKTOK_EXTRA_SCOPES", raising=False)
    redirect = "https://api.example.com/api/v1/oauth/tiktok/callback"
    query = parse_qs(urlsplit(social.authorize_url("tiktok", "state", "verifier")).query)
    assert query["scope"] == ["user.info.basic"] and query["redirect_uri"] == [redirect]

    def provider(url, **kwargs):
        if url.endswith("/oauth/token/"):
            assert kwargs["method"] == "POST"
            assert kwargs["data"] == {
                "client_key": "key",
                "client_secret": "secret",
                "code": "code",
                "grant_type": "authorization_code",
                "redirect_uri": redirect,
            }
            return {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 86400,
                "open_id": "id",
                "scope": granted,
            }
        assert granted == "user.info.basic" and kwargs["access_token"] == "access"
        return {"data": {"user": {"open_id": "id", "display_name": "Dũng", "avatar_url": "avatar"}}}

    monkeypatch.setattr(social, "call", provider)
    if granted == "user.info.basic":
        (channel,) = social.exchange_code("tiktok", "code", "verifier")
        assert channel["external_account_id"] == "id" and channel["account_name"] == "Dũng"
        assert channel["avatar_url"] == "avatar" and channel["refresh_token"] == "refresh"
    else:
        with pytest.raises(ApiError) as exc:
            social.exchange_code("tiktok", "code", "verifier")
        assert exc.value.status == 403 and exc.value.code == "TIKTOK_PERMISSIONS_MISSING"
