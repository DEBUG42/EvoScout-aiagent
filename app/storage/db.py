"""SQLite 连接管理：每线程独立连接 + WAL + busy_timeout。"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class DB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    def conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self.conn().execute(sql, params)
        self.conn().commit()
        return cur

    def executemany(self, sql: str, params: list[tuple]) -> None:
        self.conn().executemany(sql, params)
        self.conn().commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn().execute(sql, params).fetchone()

    def insert_returning(self, sql: str, params: tuple = ()):
        """INSERT ... RETURNING：仅当真正插入时返回值（OR IGNORE 被忽略返回 None）。"""
        conn = self.conn()
        row = conn.execute(sql, params).fetchone()
        conn.commit()
        return row[0] if row else None

    def close(self) -> None:
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn
