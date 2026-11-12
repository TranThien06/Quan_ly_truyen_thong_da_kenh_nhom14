"""File chính của FastAPI: chạy bằng python -m app.server hoặc uvicorn app.main:app."""

from collections import defaultdict, deque
from contextlib import asynccontextmanager
import logging
import threading
import time
import uuid
from pathlib import Path
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
import pymysql
from app.config import settings, validate_config
from app.db import transaction
from app.errors import ApiError
from app.social import ProviderError
from app.middleware import RequestSizeLimit
from app.modules import (
    auth,
    workspaces,
    channels,
    posts,
    schedules,
    media,
    reports,
    inbox,
    notifications,
    admin,
)


def error_response(request, status, code, message, details=None):
    error = {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}
    if details:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status)


@asynccontextmanager
async def lifespan(app):
    validate_config()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    docs_url = (
        f"http://localhost:{settings.port}/docs"
        if settings.env != "production"
        else f"{settings.app_url}/docs"
    )
    logging.getLogger("uvicorn.error").info("Swagger UI: %s", docs_url)
    yield


def create_app():
    app = FastAPI(
        title="Team 14 Social Media API",
        version="2.0.0",
        lifespan=lifespan,
        description="Python + FastAPI + MySQL. Mặc định mock; xem README trước khi bật live.",
    )
    app.add_middleware(RequestSizeLimit)
    frontend = Path(__file__).parent / "frontend"
    app.mount("/assets", StaticFiles(directory=frontend), name="frontend")

    @app.get("/", include_in_schema=False)
    @app.get("/accept-invitation", include_in_schema=False)
    def workspace_ui():
        return FileResponse(frontend / "index.html")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        allow_credentials=False,
    )
    buckets = defaultdict(deque)
    lock = threading.Lock()

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = str(uuid.uuid4())
        length = request.headers.get("content-length")
        maximum = (
            settings.max_upload_bytes + 1024 * 1024
            if request.headers.get("content-type", "").startswith("multipart/")
            else 1024 * 1024
        )
        if length and (not length.isdigit() or int(length) > maximum):
            return error_response(request, 413, "BODY_TOO_LARGE", "Request vượt giới hạn dung lượng")
        if request.url.path.startswith("/api/"):
            ip = request.client.host if request.client else "unknown"
            auth_path = request.url.path.startswith("/api/v1/auth/")
            category = "auth" if auth_path else "api"
            window, limit = (900, 40) if auth_path else (60, 500)
            current = time.monotonic()
            with lock:
                # Bound stale entries rather than keeping an IP map forever.
                for key in list(buckets):
                    if not buckets[key] or current - buckets[key][-1] > 900:
                        del buckets[key]
                entries = buckets[(ip, category)]
                while entries and entries[0] <= current - window:
                    entries.popleft()
                denied = len(entries) >= limit
                if not denied:
                    entries.append(current)
            if denied:
                response = error_response(request, 429, "RATE_LIMIT", "Quá nhiều yêu cầu, thử lại sau")
                response.headers["Retry-After"] = str(window)
                return response
        response = await call_next(request)
        if "/oauth/" in request.url.path:
            response.headers["Cache-Control"] = "no-store"
        response.headers.update(
            {
                "X-Request-Id": request.state.request_id,
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
            }
        )
        return response

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return error_response(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [{k: err[k] for k in ("loc", "msg", "type")} for err in exc.errors()]
        return error_response(request, 422, "VALIDATION_ERROR", "Dữ liệu không hợp lệ", details)

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return error_response(request, 502, exc.code, "Nền tảng từ chối hoặc chưa phản hồi; xem mã lỗi")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, "HTTP_ERROR", str(exc.detail))

    @app.exception_handler(pymysql.MySQLError)
    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        code = exc.args[0] if isinstance(exc, pymysql.MySQLError) and exc.args else type(exc).__name__
        if code == 1062:
            return error_response(request, 409, "CONFLICT", "Dữ liệu hoặc tác vụ đã tồn tại")
        if code in (1451, 1452):
            return error_response(
                request, 409, "REFERENCE_CONFLICT", "Dữ liệu đang được tham chiếu hoặc không tồn tại"
            )
        logging.error("request_id=%s code=%s", getattr(request.state, "request_id", None), code)
        return error_response(request, 500, "INTERNAL_ERROR", "Lỗi hệ thống")

    @app.get("/health", tags=["Hệ thống"])
    def health():
        return {"status": "ok", "social_mode": settings.social_mode}

    @app.get("/ready", tags=["Hệ thống"])
    def ready():
        with transaction() as db:
            db.one("SELECT 1")
        return {"status": "ready"}

    for module in (auth, workspaces, channels, posts, schedules, media, reports, inbox, notifications, admin):
        app.include_router(module.router, prefix="/api/v1")
    from app.webhooks import router as webhooks

    app.include_router(webhooks, prefix="/api/v1")
    return app


app = create_app()
