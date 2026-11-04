"""Tạo .env mới. Không ghi đè .env cũ hoặc thay khóa mã hóa cũ."""

import base64
from pathlib import Path
import secrets


def main():
    root = Path(__file__).resolve().parent.parent
    sample = (root / ".env.example").read_text(encoding="utf-8")
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    try:
        with (root / ".env").open("x", encoding="utf-8") as file:
            file.write(sample.replace("TOKEN_ENCRYPTION_KEY=", "TOKEN_ENCRYPTION_KEY=" + key))
        print("Created .env. Fill MySQL credentials before migrating.")
    except FileExistsError:
        print(".env already exists; preserved without changing any secret.")


if __name__ == "__main__":
    main()
