"""表结构 DDL + 初始化。"""
from __future__ import annotations

from app.storage.db import DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'paper',
    title         TEXT NOT NULL,
    summary       TEXT,
    url           TEXT,
    authors       TEXT,
    published_at  TEXT,
    extra_json    TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);

CREATE TABLE IF NOT EXISTS bot_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bot         TEXT NOT NULL,
    item_id     INTEGER NOT NULL,
    score       REAL,
    digest_zh   TEXT,
    tags        TEXT,
    alphaxiv_md TEXT,
    status      TEXT NOT NULL DEFAULT 'new',
    retry_count INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(bot, item_id)
);
CREATE INDEX IF NOT EXISTS idx_bot_items_status ON bot_items(bot, status);

CREATE TABLE IF NOT EXISTS pushed_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bot         TEXT NOT NULL,
    item_id     INTEGER NOT NULL,
    channel     TEXT NOT NULL,
    message_id  TEXT,
    push_type   TEXT NOT NULL DEFAULT 'instant',
    pushed_at   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bot         TEXT NOT NULL,
    kind        TEXT NOT NULL,
    value       TEXT NOT NULL,
    extra_json  TEXT,
    enabled     INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(bot, kind, value)
);

CREATE TABLE IF NOT EXISTS command_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bot         TEXT,
    user_id     TEXT,
    chat_id     TEXT,
    command     TEXT,
    args        TEXT,
    status      TEXT,
    result_summary TEXT,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS confirmations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT UNIQUE NOT NULL,
    bot         TEXT,
    user_id     TEXT NOT NULL,
    command     TEXT,
    args        TEXT,
    status      TEXT DEFAULT 'pending',
    expires_at  TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS event_dedup (
    event_id    TEXT PRIMARY KEY,
    received_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS kv_store (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_db(db: DB) -> None:
    db.conn().executescript(SCHEMA)
    db.conn().commit()
