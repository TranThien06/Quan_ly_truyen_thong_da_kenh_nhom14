"""Test chỉ đọc cấu hình .env.test; tuyệt đối không nạp .env production."""

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
os.environ["ENV_FILE"] = str(ROOT / ".env.test")
if (ROOT / ".env.test").exists():
    load_dotenv(ROOT / ".env.test", override=True)
os.environ["APP_ENV"] = "test"


def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true", help="Run tests against isolated MySQL")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--integration"):
        import pytest

        for item in items:
            if "integration" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="Use --integration with a separate MySQL test DB"))
