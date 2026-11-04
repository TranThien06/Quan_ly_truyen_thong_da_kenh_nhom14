"""Đọc .env một lần. Không thay khóa mã hóa khi chuyển từ Node.js."""

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(os.environ.get("ENV_FILE", ROOT / ".env"), override=False)


class Settings:
    env = os.getenv("APP_ENV", os.getenv("NODE_ENV", "development"))
    port = int(os.getenv("PORT", "3000"))
    app_url = os.getenv("APP_URL", "http://localhost:3000").rstrip("/")
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    storage = Path(os.getenv("STORAGE_DIR", "storage")).resolve()
    social_mode = os.getenv("SOCIAL_MODE", "mock")
    mail_mode = os.getenv("MAIL_MODE", "file")
    max_upload_bytes = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024


settings = Settings()


def validate_config():
    from app.security import encryption_key

    encryption_key()
    if settings.env == "production":
        if settings.social_mode != "live" or settings.mail_mode != "smtp":
            raise RuntimeError("Production cần SOCIAL_MODE=live và MAIL_MODE=smtp")
        if not settings.app_url.startswith("https://"):
            raise RuntimeError("Production cần APP_URL HTTPS")
