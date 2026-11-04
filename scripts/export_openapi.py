"""Xuất OpenAPI thực của FastAPI. Swagger chạy tự động tại /docs."""

import json
from app.config import ROOT
from app.main import app


def main():
    spec = app.openapi()
    (ROOT / "docs" / "openapi.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Exported {sum(len(v) for v in spec['paths'].values())} operations")


if __name__ == "__main__":
    main()
