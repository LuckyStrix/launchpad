"""SQLite storage. One connection, guarded by a lock; every query here is small and fast."""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS layout_revisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    doc        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS activity (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL
);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def connect(path: Path) -> None:
    global _conn
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA foreign_keys = ON")
    _conn.execute("PRAGMA journal_mode = WAL")
    _conn.executescript(SCHEMA)


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    if _conn is None:
        raise RuntimeError("db.connect() has not been called")
    with _lock:
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise


def query(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with tx() as c:
        return c.execute(sql, args).fetchall()


def execute(sql: str, args: tuple = ()) -> int:
    """Run a write and return the new row id."""
    with tx() as c:
        return c.execute(sql, args).lastrowid


def kv_get(key: str, default: Any = None) -> Any:
    rows = query("SELECT value FROM kv WHERE key = ?", (key,))
    return json.loads(rows[0]["value"]) if rows else default


def kv_set(key: str, value: Any) -> None:
    execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def kv_delete(key: str) -> None:
    execute("DELETE FROM kv WHERE key = ?", (key,))


def log_activity(kind: str, name: str, status: str, detail: str = "") -> int:
    return execute(
        "INSERT INTO activity (kind, name, status, detail) VALUES (?, ?, ?, ?)",
        (kind, name, status, detail[:2000]),
    )


def set_activity_status(activity_id: int, status: str) -> None:
    execute("UPDATE activity SET status = ? WHERE id = ?", (status, activity_id))
