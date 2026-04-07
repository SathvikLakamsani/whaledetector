"""
polymarket-whale-alerts — entrypoint.

Read-only Polymarket listener: websocket executions + Data API enrichment + Discord webhooks.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Any

import aiosqlite
import httpx

from app.config import load_settings
from app.db import database as db
from app.models import AlertContext, Side, TradeEvent
from app.services import market_sync, notifier, trade_normalizer, trade_resolver, wallet_stats, ws_listener
from app.utils.dedupe import FillMerger, build_merged_trade
from app.services.scorer import score_merged_trade
from app.utils.time import isoformat_z, utc_now

logger = logging.getLogger(__name__)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def _insert_recent_fill(conn: aiosqlite.Connection, fill) -> None:
    await db.execute(
        conn,
        """
        INSERT OR IGNORE INTO recent_market_fills (condition_id, asset_id, notional_usd, ts_ms, dedupe_key)
        VALUES (?,?,?,?,?)
        """,
        (
            fill.condition_id,
            fill.asset_id,
            fill.notional_usd,
            fill.ts_ms,
            fill.dedupe_key,
        ),
    )


async def _insert_trade_if_new(conn: aiosqlite.Connection, t: TradeEvent) -> bool:
    cur = await conn.execute(
        """
        INSERT OR IGNORE INTO trades (
          dedupe_key, tx_hash, wallet, condition_id, asset_id, side, outcome,
          price, size, notional_usd, ts_ms, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            t.dedupe_key,
            t.tx_hash,
            t.wallet,
            t.condition_id,
            t.asset_id,
            t.side.value,
            t.outcome,
            t.price,
            t.size,
            t.notional_usd,
            t.ts_ms,
            json.dumps({"ws": t.raw_ws}, default=str),
        ),
    )
    await conn.commit()
    return cur.rowcount == 1


class WhaleApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.registry = market_sync.MarketRegistry()
        self.merger = FillMerger(int(self.settings.merge_window_seconds * 1000))
        self.conn: aiosqlite.Connection | None = None
        self.http: httpx.AsyncClient | None = None
        self._db_lock = asyncio.Lock()
        self._ws_task: asyncio.Task[None] | None = None
        self._seen_polled_trade_keys: dict[str, int] = {}

    async def _restart_ws(self, on_message) -> None:
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        ids = self.registry.subscribed_asset_ids(self.settings.max_subscribed_assets)
        logger.info("websocket subscribing to %s asset ids", len(ids))
        self._ws_task = asyncio.create_task(ws_listener.run_market_ws(self.settings, ids, on_message))

    async def _handle_ws_payload(self, payload: dict[str, Any]) -> None:
        fill = trade_normalizer.parse_last_trade_price(payload)
        if fill is None:
            return
        assert self.conn and self.http
        async with self._db_lock:
            await _insert_recent_fill(self.conn, fill)

        if self.registry.meta_for_condition(fill.condition_id) is None:
            return

        if fill.notional_usd < self.settings.min_whale_usd:
            return

        outcome = self.registry.outcome_for_asset(fill.asset_id)
        if not outcome:
            logger.debug("unknown outcome for asset %s", fill.asset_id[:16])
            return

        trade = await trade_resolver.resolve_wallet_for_fill(self.http, self.settings, fill, outcome)
        if trade is None:
            return

        await self._accept_trade_event(trade)

    async def _accept_trade_event(self, trade: TradeEvent) -> None:
        if trade.notional_usd < self.settings.min_whale_usd:
            return

        async with self._db_lock:
            assert self.conn
            inserted = await _insert_trade_if_new(self.conn, trade)
        if not inserted:
            return

        self.merger.add(trade)

    async def _poll_trades_loop(self) -> None:
        """
        Fallback ingestion path using Data API `/trades` (contains wallet already).

        This complements websocket fills and avoids strict 1:1 fill matching failure.
        """
        assert self.http and self.conn
        url = f"{self.settings.data_api_base_url.rstrip('/')}/trades"
        while True:
            try:
                r = await self.http.get(
                    url,
                    params={"limit": self.settings.trades_poll_limit},
                    timeout=self.settings.http_timeout_seconds,
                )
                r.raise_for_status()
                rows = r.json()
                if isinstance(rows, list):
                    for row in rows:
                        t = self._trade_event_from_polled_row(row)
                        if t is None:
                            continue
                        # Also add to market-relative fill history.
                        fill_key = f"poll|{t.dedupe_key}"
                        async with self._db_lock:
                            await db.execute(
                                self.conn,
                                """
                                INSERT OR IGNORE INTO recent_market_fills
                                (condition_id, asset_id, notional_usd, ts_ms, dedupe_key)
                                VALUES (?,?,?,?,?)
                                """,
                                (t.condition_id, t.asset_id, t.notional_usd, t.ts_ms, fill_key),
                            )
                        await self._accept_trade_event(t)
                self._prune_seen_polled_keys()
            except Exception:
                logger.exception("trades poll loop error")
            await asyncio.sleep(self.settings.trades_poll_interval_seconds)

    def _trade_event_from_polled_row(self, row: dict[str, Any]) -> TradeEvent | None:
        try:
            wallet = str(row.get("proxyWallet") or "").lower()
            condition_id = str(row.get("conditionId") or "").lower()
            asset_id = str(row.get("asset") or "")
            side = Side(str(row.get("side")).upper())
            price = float(row.get("price"))
            size = float(row.get("size"))
            ts = int(row.get("timestamp"))
            ts_ms = ts * 1000 if ts < 10**11 else ts
        except (TypeError, ValueError):
            return None

        if not wallet.startswith("0x") or not condition_id.startswith("0x") or not asset_id:
            return None
        if price <= 0 or size <= 0:
            return None
        now_ms = int(utc_now().timestamp() * 1000)
        if now_ms - ts_ms > self.settings.max_polled_trade_age_seconds * 1000:
            return None

        # Deduplicate polled rows in-memory before hitting SQLite.
        tx_hash = row.get("transactionHash")
        dedupe_basis = (
            f"{tx_hash}|{wallet}|{condition_id}|{asset_id}|{side.value}|"
            f"{price:.8f}|{size:.8f}|{ts_ms}"
        )
        dedupe_key = hashlib.sha256(dedupe_basis.encode("utf-8")).hexdigest()
        if dedupe_key in self._seen_polled_trade_keys:
            return None
        self._seen_polled_trade_keys[dedupe_key] = now_ms

        outcome_raw = row.get("outcome")
        outcome = (
            str(outcome_raw).upper()
            if isinstance(outcome_raw, str) and outcome_raw.strip()
            else (self.registry.outcome_for_asset(asset_id) or "UNKNOWN")
        )

        return TradeEvent(
            wallet=wallet,
            condition_id=condition_id,
            asset_id=asset_id,
            side=side,
            outcome=outcome,
            price=price,
            size=size,
            notional_usd=price * size,
            ts_ms=ts_ms,
            tx_hash=str(tx_hash) if tx_hash else None,
            dedupe_key=dedupe_key,
            raw_ws={"source": "data_api_poll", "row": row},
        )

    def _prune_seen_polled_keys(self) -> None:
        now_ms = int(utc_now().timestamp() * 1000)
        ttl_ms = max(60_000, self.settings.max_polled_trade_age_seconds * 1000 * 4)
        to_del = [k for k, ts in self._seen_polled_trade_keys.items() if now_ms - ts > ttl_ms]
        for k in to_del:
            del self._seen_polled_trade_keys[k]

    async def _flush_merges(self) -> None:
        assert self.conn and self.http
        now_ms = int(utc_now().timestamp() * 1000)
        buffers = self.merger.close_if_idle(now_ms)
        for buf in buffers:
            if not buf:
                continue
            total = sum(t.notional_usd for t in buf)
            if total < self.settings.min_whale_usd:
                continue
            first = buf[0]
            meta = self.registry.meta_for_condition(first.condition_id)
            title = meta.title if meta else first.condition_id
            slug = meta.slug if meta else None
            event_slug = meta.event_slug if meta else None
            merged = build_merged_trade(buf, title=title, slug=slug, event_slug=event_slug)

            perf = await wallet_stats.get_wallet_performance_with_lock(
                self.conn, self.http, self.settings, merged.wallet, self._db_lock
            )
            async with self._db_lock:
                breakdown, rel_stats, severity = await score_merged_trade(
                    self.conn,
                    self.settings,
                    total_notional_usd=merged.total_notional_usd,
                    condition_id=merged.condition_id,
                    perf=perf,
                )

            ctx = AlertContext(
                merged=merged,
                relativity=rel_stats,
                score=breakdown,
                severity_label=severity,
                wallet_perf=perf,
            )

            group_uuid = str(uuid.uuid4())
            async with self._db_lock:
                cur = await self.conn.execute(
                    """
                    INSERT INTO merged_trade_groups (
                      group_uuid, wallet, condition_id, asset_id, side, outcome,
                      start_ts_ms, end_ts_ms, fill_count, total_notional_usd, weighted_avg_price,
                      final_score, severity_label, alerted, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                    """,
                    (
                        group_uuid,
                        merged.wallet,
                        merged.condition_id,
                        merged.asset_id,
                        merged.side.value,
                        merged.outcome,
                        merged.start_ts_ms,
                        merged.end_ts_ms,
                        merged.fill_count,
                        merged.total_notional_usd,
                        merged.weighted_avg_price,
                        breakdown.composite,
                        severity,
                        isoformat_z(utc_now()),
                    ),
                )
                await self.conn.commit()
                mid = int(cur.lastrowid)

            ok = await notifier.send_discord_alert(self.settings, ctx)
            async with self._db_lock:
                await db.execute(
                    self.conn,
                    "UPDATE merged_trade_groups SET alerted=? WHERE id=?",
                    (1 if ok else 0, mid),
                )
                await db.execute(
                    self.conn,
                    """
                    INSERT INTO alerts (merged_group_id, sent_at, severity, destination, payload_json)
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        mid,
                        isoformat_z(utc_now()),
                        severity,
                        "discord",
                        notifier.snapshot_payload(ctx),
                    ),
                )

    async def _merge_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(0.75)
            try:
                await self._flush_merges()
            except Exception:
                logger.exception("merge flush error")

    async def _market_loop(self) -> None:
        assert self.conn and self.http
        while True:
            await asyncio.sleep(self.settings.market_sync_interval_seconds)
            try:
                async with self._db_lock:
                    await market_sync.sync_markets(self.conn, self.http, self.settings, self.registry)
                    await db.vacuum_old_fills(
                        self.conn, self.settings.relativity_lookback_days + 2
                    )
                await self._restart_ws(self._handle_ws_payload)
            except Exception:
                logger.exception("market sync failed")

    async def run(self) -> None:
        _setup_logging(self.settings.log_level)
        self.conn = await db.connect(self.settings.database_path)
        await db.init_schema(self.conn)
        self.http = httpx.AsyncClient(headers={"User-Agent": "polymarket-whale-alerts/0.1"})

        async with self._db_lock:
            await market_sync.sync_markets(self.conn, self.http, self.settings, self.registry)
        await self._restart_ws(self._handle_ws_payload)

        merge_task = asyncio.create_task(self._merge_flush_loop())
        market_task = asyncio.create_task(self._market_loop())
        poll_task = asyncio.create_task(self._poll_trades_loop())
        try:
            await asyncio.gather(merge_task, market_task, poll_task)
        finally:
            merge_task.cancel()
            market_task.cancel()
            poll_task.cancel()
            if self._ws_task:
                self._ws_task.cancel()
            for t in (merge_task, market_task, poll_task, self._ws_task):
                if t:
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
            if self.http:
                await self.http.aclose()
            if self.conn:
                await self.conn.close()


def main() -> None:
    asyncio.run(WhaleApp().run())


if __name__ == "__main__":
    main()
