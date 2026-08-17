"""数据访问层：单 Repo 类封装全部 SQL（个人项目规模，不拆多 repo 类）。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from app.storage.db import DB


class Repo:
    def __init__(self, db: DB):
        self.db = db

    # ---- items ----

    def insert_items(self, raw_items: list[dict]) -> list[int]:
        """批量插入（UNIQUE(source, external_id) 去重），返回新插入的 id 列表。"""
        new_ids: list[int] = []
        for it in raw_items:
            row_id = self.db.insert_returning(
                """INSERT OR IGNORE INTO items
                   (source, external_id, kind, title, summary, url, authors, published_at, extra_json)
                   VALUES (?,?,?,?,?,?,?,?,?) RETURNING id""",
                (
                    it["source"], it["external_id"], it.get("kind", "news"),
                    it["title"], it.get("summary"), it.get("url"),
                    json.dumps(it.get("authors", []), ensure_ascii=False),
                    it.get("published_at"),
                    json.dumps(it.get("extra", {}), ensure_ascii=False),
                ),
            )
            if row_id:
                new_ids.append(row_id)
        return new_ids

    def get_item(self, item_id: int) -> dict | None:
        row = self.db.query_one("SELECT * FROM items WHERE id=?", (item_id,))
        return dict(row) if row else None

    def get_items(self, item_ids: list[int]) -> list[dict]:
        if not item_ids:
            return []
        q = ",".join("?" * len(item_ids))
        return [dict(r) for r in self.db.query(f"SELECT * FROM items WHERE id IN ({q})", tuple(item_ids))]

    def recent_items(self, kind: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM items"
        params: tuple = ()
        if kind:
            sql += " WHERE kind=?"
            params = (kind,)
        sql += " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in self.db.query(sql, params + (limit,))]

    # ---- bot_items ----

    def create_bot_items(self, bot: str, item_ids: list[int]) -> int:
        """为 bot 建立待处理条目（status='new'），返回新建数。"""
        n = 0
        for iid in item_ids:
            if self.db.insert_returning(
                "INSERT OR IGNORE INTO bot_items (bot, item_id) VALUES (?,?) RETURNING id",
                (bot, iid),
            ):
                n += 1
        return n

    def get_bot_items(self, bot: str, status: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT bi.*, i.title, i.url, i.summary, i.kind, i.source, i.external_id FROM bot_items bi JOIN items i ON i.id=bi.item_id WHERE bi.bot=?"
        params: list = [bot]
        if status:
            sql += " AND bi.status=?"
            params.append(status)
        sql += " ORDER BY bi.id ASC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.db.query(sql, tuple(params))]

    def update_bot_item(self, bot_item_id: int, **fields: object) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(
            f"UPDATE bot_items SET {sets} WHERE id=?", tuple(fields.values()) + (bot_item_id,)
        )

    def count_bot_items_by_status(self, bot: str, status: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) c FROM bot_items WHERE bot=? AND status=?", (bot, status)
        )
        return int(row["c"]) if row else 0

    # ---- pushed_log ----

    def log_push(self, bot: str, item_id: int, channel: str, message_id: str | None,
                 push_type: str) -> None:
        self.db.execute(
            "INSERT INTO pushed_log (bot, item_id, channel, message_id, push_type) VALUES (?,?,?,?,?)",
            (bot, item_id, channel, message_id, push_type),
        )

    def is_pushed(self, bot: str, item_id: int) -> bool:
        row = self.db.query_one(
            "SELECT 1 FROM pushed_log WHERE bot=? AND item_id=?", (bot, item_id)
        )
        return row is not None

    def pushed_since(self, bot: str, since_hours: int) -> list[dict]:
        rows = self.db.query(
            """SELECT pl.*, i.title, i.url FROM pushed_log pl
               JOIN items i ON i.id=pl.item_id
               WHERE pl.bot=? AND pl.pushed_at >= datetime('now','localtime', ?)
               ORDER BY pl.id DESC""",
            (bot, f"-{since_hours} hours"),
        )
        return [dict(r) for r in rows]

    # ---- subscriptions ----

    def list_subscriptions(self, bot: str) -> list[dict]:
        return [
            dict(r) for r in self.db.query(
                "SELECT * FROM subscriptions WHERE bot=? AND enabled=1 ORDER BY id", (bot,)
            )
        ]

    def add_subscription(self, bot: str, kind: str, value: str, extra: dict | None = None) -> bool:
        return bool(self.db.insert_returning(
            "INSERT OR IGNORE INTO subscriptions (bot, kind, value, extra_json) VALUES (?,?,?,?) RETURNING id",
            (bot, kind, value, json.dumps(extra or {}, ensure_ascii=False)),
        ))

    def remove_subscription(self, bot: str, sub_id: int) -> bool:
        cur = self.db.execute("DELETE FROM subscriptions WHERE bot=? AND id=?", (bot, sub_id))
        return cur.rowcount > 0

    # ---- command_log ----

    def log_command(self, bot: str, user_id: str, chat_id: str, command: str,
                    args: str, status: str, result_summary: str = "") -> None:
        self.db.execute(
            """INSERT INTO command_log (bot, user_id, chat_id, command, args, status, result_summary)
               VALUES (?,?,?,?,?,?,?)""",
            (bot, user_id, chat_id, command, args[:500], status, result_summary[:500]),
        )

    # ---- confirmations ----

    def create_confirmation(self, bot: str, user_id: str, command: str, args: str,
                            minutes: int = 10) -> str:
        token = uuid.uuid4().hex
        expires = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        self.db.execute(
            """INSERT INTO confirmations (token, bot, user_id, command, args, expires_at)
               VALUES (?,?,?,?,?,?)""",
            (token, bot, user_id, command, args, expires),
        )
        return token

    def consume_confirmation(self, token: str, user_id: str) -> dict | None:
        """校验并消费确认令牌（pending、未过期、同一用户），返回确认记录或 None。"""
        row = self.db.query_one(
            "SELECT * FROM confirmations WHERE token=? AND user_id=?", (token, user_id)
        )
        if not row or row["status"] != "pending":
            return None
        if row["expires_at"] < datetime.now().isoformat(timespec="seconds"):
            self.db.execute("UPDATE confirmations SET status='expired' WHERE token=?", (token,))
            return None
        self.db.execute("UPDATE confirmations SET status='confirmed' WHERE token=?", (token,))
        rec = dict(row)
        rec["status"] = "confirmed"
        return rec

    def cancel_confirmation(self, token: str, user_id: str) -> bool:
        cur = self.db.execute(
            "UPDATE confirmations SET status='cancelled' WHERE token=? AND user_id=? AND status='pending'",
            (token, user_id),
        )
        return cur.rowcount > 0

    # ---- event_dedup ----

    def claim_event(self, event_id: str) -> bool:
        """首次见到返回 True（并记录），重复返回 False。"""
        return bool(self.db.insert_returning(
            "INSERT OR IGNORE INTO event_dedup (event_id) VALUES (?) RETURNING event_id",
            (event_id,),
        ))

    # ---- kv ----

    def kv_get(self, key: str) -> str | None:
        row = self.db.query_one("SELECT value FROM kv_store WHERE key=?", (key,))
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO kv_store (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
