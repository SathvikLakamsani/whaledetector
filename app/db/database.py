"""SQLite access (async) and schema bootstrap."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def connect(db_path: str) -> aiosqlite.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


async def init_schema(conn: aiosqlite.Connection) -> None:
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    await conn.executescript(sql)
    await conn.commit()
    logger.info("database schema ensured at %s", _SCHEMA_PATH)


async def fetch_one(
    conn: aiosqlite.Connection, sql: str, params: Sequence[Any] | None = None
) -> aiosqlite.Row | None:
    params = params or []
    async with conn.execute(sql, params) as cur:
        row = await cur.fetchone()
    return row


async def fetch_all(
    conn: aiosqlite.Connection, sql: str, params: Sequence[Any] | None = None
) -> list[aiosqlite.Row]:
    params = params or []
    async with conn.execute(sql, params) as cur:
        rows = await cur.fetchall()
    return list(rows)


async def execute(
    conn: aiosqlite.Connection, sql: str, params: Sequence[Any] | None = None
) -> aiosqlite.Cursor:
    params = params or []
    cur = await conn.execute(sql, params)
    await conn.commit()
    return cur


async def executemany(
    conn: aiosqlite.Connection, sql: str, params_seq: Iterable[Sequence[Any]]
) -> None:
    await conn.executemany(sql, params_seq)
    await conn.commit()


async def vacuum_old_fills(
    conn: aiosqlite.Connection, lookback_days: int, table: str = "recent_market_fills"
) -> None:
    cutoff_ms = lookback_days * 86_400_000
    # Use relative cutoff from max ts to avoid clock skew issues
    row = await fetch_one(conn, f"SELECT MAX(ts_ms) AS m FROM {table}")
    if not row or row["m"] is None:
        return
    max_ts = int(row["m"])
    boundary = max_ts - cutoff_ms
    await execute(conn, f"DELETE FROM {table} WHERE ts_ms < ?", (boundary,))
