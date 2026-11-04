"""Giữ nguyên version 001/002; DB đã migrate bằng Node sẽ được nhận diện và bỏ qua."""

import re
from app.config import ROOT
from app.db import connect
import os


def statements(sql):
    # Plain DDL/DML only; respect quoted strings and SQL comments when splitting.
    parts = []
    buffer = []
    quote = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote:
            buffer.append(char)
            if char == "\\" and index + 1 < len(sql):
                index += 1
                buffer.append(sql[index])
            elif char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 1
                    buffer.append(sql[index])
                else:
                    quote = None
        elif char in ("'", '"', "`"):
            quote = char
            buffer.append(char)
        elif sql[index : index + 2] == "--" and (index + 2 == len(sql) or sql[index + 2].isspace()):
            index = sql.find("\n", index)
            if index < 0:
                break
            buffer.append("\n")
        elif sql[index : index + 2] == "/*":
            end = sql.find("*/", index + 2)
            if end < 0:
                raise ValueError("Unclosed SQL comment")
            index = end + 1
            buffer.append(" ")
        elif char == ";":
            if "".join(buffer).strip():
                parts.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
        index += 1
    if quote:
        raise ValueError("Unclosed SQL string")
    if "".join(buffer).strip():
        parts.append("".join(buffer).strip())
    return parts


def migrate():
    name = os.getenv("DB_NAME", "social_media_mgmt_aligned")
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("Invalid DB_NAME")
    connection = connect(database=False)
    lock_name = f"{name}:migration"
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            connection.select_db(name)
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version VARCHAR(100) PRIMARY KEY,applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
            )
            cursor.execute("SELECT GET_LOCK(%s,30) acquired", (lock_name,))
            if not cursor.fetchone()["acquired"]:
                raise RuntimeError("Migration is already running")
            try:
                versions = [
                    ("001_initial", ROOT / "tai-lieu-da-dong-bo" / "Khoi_tao_co_so_du_lieu_MySQL.sql"),
                    ("002_runtime", ROOT / "database" / "002_runtime.sql"),
                ]
                for version, path in versions:
                    cursor.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (version,))
                    if cursor.fetchone():
                        print(f"Already applied {version}")
                        continue
                    sql = path.read_text(encoding="utf-8-sig")
                    for statement in statements(sql):
                        if re.match(r"^(CREATE DATABASE|USE\s)", statement, re.I):
                            continue
                        try:
                            cursor.execute(statement)
                        except Exception as error:
                            # Do not print SQL values, credentials or provider tokens.
                            raise RuntimeError(
                                f"Migration {version} failed at {statement[:70]} (code={error.args[0]})"
                            ) from None
                    cursor.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (version,))
                    connection.commit()
                    print(f"Applied {version}")
            finally:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
    finally:
        connection.close()


if __name__ == "__main__":
    migrate()
