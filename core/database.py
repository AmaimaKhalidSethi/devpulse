"""
core/database.py
Async SQLite via aiosqlite — API keys (hashed) and audit log.
Schema is created on first startup via init_db().
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from core.logging import get_logger

logger = get_logger(__name__)

_DB_PATH: Path = Path("devpulse.db")


def _set_db_path(path: Path) -> None:
    global _DB_PATH
    _DB_PATH = path


@asynccontextmanager
async def _db():
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        # busy_timeout: aiosqlite does not retry on SQLITE_BUSY by default.
        # Without this, concurrent async writers (audit log + key lookup) can
        # race and raise OperationalError('database is locked') even with WAL.
        # 5000ms gives enough headroom for a burst of concurrent requests.
        await conn.execute("PRAGMA busy_timeout=5000")
        yield conn


async def init_db(db_path: Path | None = None) -> None:
    if db_path:
        _set_db_path(db_path)

    async with _db() as conn:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key_id              TEXT PRIMARY KEY,
                key_hash            TEXT UNIQUE NOT NULL,
                label               TEXT NOT NULL,
                rate_limit_per_minute INTEGER NOT NULL DEFAULT 60,
                created_at          TEXT NOT NULL,
                is_active           INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              TEXT NOT NULL,
                key_id          TEXT,
                tool_name       TEXT,
                success         INTEGER NOT NULL,
                latency_ms      REAL,
                error_summary   TEXT,
                request_ip      TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_audit_ts      ON audit_log(ts);
            CREATE INDEX IF NOT EXISTS idx_audit_tool    ON audit_log(tool_name);
            CREATE INDEX IF NOT EXISTS idx_audit_key     ON audit_log(key_id);
            CREATE INDEX IF NOT EXISTS idx_keys_hash     ON api_keys(key_hash);
        """)
        await conn.commit()
    logger.info("database_ready", extra={"extra": {"path": str(_DB_PATH)}})


# ── API key operations ────────────────────────────────────────────────────────

async def create_api_key(
    key_hash: str,
    label: str,
    rate_limit_per_minute: int = 60,
) -> str:
    key_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with _db() as conn:
        await conn.execute(
            "INSERT INTO api_keys (key_id, key_hash, label, rate_limit_per_minute, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key_id, key_hash, label, rate_limit_per_minute, now),
        )
        await conn.commit()
    return key_id


async def get_key_by_hash(key_hash: str) -> dict | None:
    async with _db() as conn:
        async with conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1", (key_hash,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def list_api_keys() -> list[dict]:
    async with _db() as conn:
        async with conn.execute(
            "SELECT key_id, label, rate_limit_per_minute, created_at, is_active "
            "FROM api_keys ORDER BY created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def revoke_api_key(key_id: str) -> bool:
    async with _db() as conn:
        cur = await conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE key_id = ?", (key_id,)
        )
        await conn.commit()
        return cur.rowcount > 0


# ── Audit log operations ──────────────────────────────────────────────────────

async def log_execution(
    *,
    key_id: str | None,
    tool_name: str,
    success: bool,
    latency_ms: float,
    error_summary: str | None = None,
    request_ip: str | None = None,
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    async with _db() as conn:
        await conn.execute(
            "INSERT INTO audit_log (ts, key_id, tool_name, success, latency_ms, error_summary, request_ip) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, key_id, tool_name, int(success), latency_ms, error_summary, request_ip),
        )
        await conn.commit()


async def get_audit_logs(
    limit: int = 100,
    tool_name: str | None = None,
    key_id: str | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM audit_log"
    params: list[Any] = []
    clauses: list[str] = []

    if tool_name:
        clauses.append("tool_name = ?")
        params.append(tool_name)
    if key_id:
        clauses.append("key_id = ?")
        params.append(key_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    query += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)

    async with _db() as conn:
        async with conn.execute(query, params) as cur:
            return [dict(r) for r in await cur.fetchall()]
