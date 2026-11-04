"""SQL tham số hóa với PyMySQL. Mỗi transaction dùng một connection UTC riêng."""

import json
import os
from contextlib import contextmanager
import pymysql
from app.config import settings  # noqa: F401 -- load .env before reading connection options

JSON_COLUMNS = {
    "platform_options",
    "response_payload",
    "extra_metrics",
    "params",
    "attachments",
    "setting_value",
    "old_values",
    "new_values",
}


def connect(database=True):
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "social_media_mgmt_aligned") if database else None,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        init_command="SET time_zone='+00:00'",
        connect_timeout=10,
        read_timeout=60,
        autocommit=False,
    )


def decode(row):
    if row:
        for key in JSON_COLUMNS & row.keys():
            if isinstance(row[key], str):
                row[key] = json.loads(row[key])
    return row


class Database:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        with self.connection.cursor() as cursor:
            return cursor.execute(sql, params)

    def all(self, sql, params=()):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return [decode(row) for row in cursor.fetchall()]

    def one(self, sql, params=()):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return decode(cursor.fetchone())

    def insert(self, table, values):
        # table/column names come ONLY from constants in source, never request parameters.
        keys = list(values)
        sql = f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join(['%s'] * len(keys))})"
        with self.connection.cursor() as cursor:
            cursor.execute(sql, tuple(values.values()))
            return cursor.lastrowid


@contextmanager
def transaction():
    connection = connect()
    try:
        yield Database(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def json_value(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
