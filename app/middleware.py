"""Giới hạn cả request có Content-Length và upload truyền theo từng chunk."""

from starlette.exceptions import HTTPException
from app.config import settings


class RequestSizeLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        upload = headers.get(b"content-type", b"").startswith(b"multipart/")
        maximum = settings.max_upload_bytes + 1024 * 1024 if upload else 1024 * 1024
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > maximum:
                    raise HTTPException(413, "Request vượt giới hạn dung lượng")
            return message

        await self.app(scope, limited_receive, send)
