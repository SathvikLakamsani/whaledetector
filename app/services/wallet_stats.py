"""30-day closed-position stats via Polymarket Data API (cached in SQLite)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
import httpx

from app.config import Settings
from app.db import database as db
from app.models import WalletPerformance30d
from app.utils.time import isoformat_z, utc_now


def _ts_to_dt(ts: int) -> datetime:
    if ts > 10**11:
        ts = ts // 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc)


async def load_cached_wallet_stats(
    conn: aiosqlite.Connection,
    wallet: str,
    settings: Settings,
) -> WalletPerformance30d | None:
    row = await db.fetch_one(
        conn,
        "SELECT * FROM wallet_stats WHERE wallet = ?",
        (wallet.lower(),),
    )
    if row is None:
        return None
    refreshed = datetime.fromisoformat(str(row["last_refreshed_at"]))
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=timezone.utc)
    age = utc_now() - refreshed
    if age > timedelta(minutes=settings.wallet_stats_cache_minutes):
        return None
    return WalletPerformance30d(
        wallet=row["wallet"],
        wins=int(row["wins_30d"]),
        closed_positions=int(row["closed_positions_30d"]),
        win_rate=float(row["win_rate_30d"]),
        realized_pnl_usd=float(row["realized_pnl_30d"]),
        as_of=refreshed,
    )


async def fetch_wallet_performance_30d(
    client: httpx.AsyncClient,
    settings: Settings,
    wallet: str,
) -> WalletPerformance30d:
    url = f"{settings.data_api_base_url.rstrip('/')}/closed-positions"
    cutoff = utc_now() - timedelta(days=30)
    wins = 0
    total = 0
    realized = 0.0
    offset = 0
    limit = settings.closed_positions_page_limit

    while True:
        r = await client.get(
            url,
            params={
                "user": wallet,
                "limit": limit,
                "offset": offset,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
            },
            timeout=settings.http_timeout_seconds,
        )
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            break

        stop = False
        for row in rows:
            try:
                ts = int(row["timestamp"])
            except (KeyError, TypeError, ValueError):
                continue
            closed_at = _ts_to_dt(ts)
            if closed_at < cutoff:
                stop = True
                break
            total += 1
            pnl = float(row.get("realizedPnl") or 0.0)
            realized += pnl
            if pnl > 0:
                wins += 1

        if stop or len(rows) < limit:
            break
        offset += limit

    win_rate = (wins / total) if total > 0 else 0.0
    return WalletPerformance30d(
        wallet=wallet.lower(),
        wins=wins,
        closed_positions=total,
        win_rate=win_rate,
        realized_pnl_usd=realized,
    )


async def persist_wallet_stats(conn: aiosqlite.Connection, perf: WalletPerformance30d) -> None:
    await db.execute(
        conn,
        """
        INSERT INTO wallet_stats (
          wallet, wins_30d, closed_positions_30d, win_rate_30d, realized_pnl_30d, last_refreshed_at
        ) VALUES (?,?,?,?,?,?)
        ON CONFLICT(wallet) DO UPDATE SET
          wins_30d=excluded.wins_30d,
          closed_positions_30d=excluded.closed_positions_30d,
          win_rate_30d=excluded.win_rate_30d,
          realized_pnl_30d=excluded.realized_pnl_30d,
          last_refreshed_at=excluded.last_refreshed_at
        """,
        (
            perf.wallet,
            perf.wins,
            perf.closed_positions,
            perf.win_rate,
            perf.realized_pnl_usd,
            isoformat_z(utc_now()),
        ),
    )


async def get_wallet_performance(
    conn: aiosqlite.Connection,
    client: httpx.AsyncClient,
    settings: Settings,
    wallet: str,
) -> WalletPerformance30d:
    cached = await load_cached_wallet_stats(conn, wallet, settings)
    if cached is not None:
        return cached
    perf = await fetch_wallet_performance_30d(client, settings, wallet)
    await persist_wallet_stats(conn, perf)
    return perf


async def get_wallet_performance_with_lock(
    conn: aiosqlite.Connection,
    client: httpx.AsyncClient,
    settings: Settings,
    wallet: str,
    db_lock: asyncio.Lock,
) -> WalletPerformance30d:
    """
    Cache-aware wallet stats without holding `db_lock` during HTTP (rate-limit friendly).
    """
    async with db_lock:
        cached = await load_cached_wallet_stats(conn, wallet, settings)
    if cached is not None:
        return cached

    perf = await fetch_wallet_performance_30d(client, settings, wallet)

    async with db_lock:
        cached2 = await load_cached_wallet_stats(conn, wallet, settings)
        if cached2 is not None:
            return cached2
        await persist_wallet_stats(conn, perf)
    return perf
