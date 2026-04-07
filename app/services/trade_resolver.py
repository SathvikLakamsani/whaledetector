"""Resolve taker wallet for a fill using the public Data API `/trades` stream."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.models import RawFillEvent, Side, TradeEvent

logger = logging.getLogger(__name__)


def _api_ts_to_ms(ts: int) -> int:
    if ts > 10**11:
        return ts
    return ts * 1000


def _pick_matching_trade(
    rows: list[dict[str, Any]],
    fill: RawFillEvent,
    *,
    time_tol_s: float,
    size_ratio: float,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = float("inf")
    target_ms = fill.ts_ms
    for row in rows:
        try:
            if str(row.get("asset")) != fill.asset_id:
                continue
            if Side(str(row.get("side")).upper()) != fill.side:
                continue
            api_ms = _api_ts_to_ms(int(row["timestamp"]))
            dt_ms = abs(api_ms - target_ms)
            if dt_ms > time_tol_s * 1000:
                continue
            sz = float(row["size"])
            pr = float(row["price"])
            if abs(sz - fill.size) > max(fill.size * size_ratio, 1e-6):
                continue
            if abs(pr - fill.price) > max(fill.price * 0.02, 1e-6):
                continue
            score = dt_ms + abs(sz - fill.size) * 1e3
            if score < best_score:
                best_score = score
                best = row
        except (KeyError, TypeError, ValueError):
            continue
    return best


async def resolve_wallet_for_fill(
    client: httpx.AsyncClient,
    settings: Settings,
    fill: RawFillEvent,
    outcome: str,
) -> TradeEvent | None:
    url = f"{settings.data_api_base_url.rstrip('/')}/trades"
    params = {"market": fill.condition_id, "limit": 100}
    try:
        r = await client.get(url, params=params, timeout=settings.http_timeout_seconds)
        r.raise_for_status()
        rows = r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("trades lookup failed for %s: %s", fill.condition_id, e)
        return None

    if not isinstance(rows, list):
        return None

    row = _pick_matching_trade(
        rows,
        fill,
        time_tol_s=settings.trade_match_time_tolerance_seconds,
        size_ratio=settings.trade_match_size_tolerance_ratio,
    )
    if row is None:
        logger.info(
            "no matching /trades row for fill asset=%s side=%s size=%s price=%s ts_ms=%s",
            fill.asset_id[:16],
            fill.side,
            fill.size,
            fill.price,
            fill.ts_ms,
        )
        return None

    wallet = str(row.get("proxyWallet") or "").lower()
    if not wallet.startswith("0x"):
        return None

    tx = row.get("transactionHash")
    tx_hash = str(tx) if tx else None
    dedupe_key = tx_hash or f"{wallet}|{fill.dedupe_key}"

    return TradeEvent(
        wallet=wallet,
        condition_id=fill.condition_id,
        asset_id=fill.asset_id,
        side=fill.side,
        outcome=outcome,
        price=fill.price,
        size=fill.size,
        notional_usd=fill.notional_usd,
        ts_ms=fill.ts_ms,
        tx_hash=tx_hash,
        dedupe_key=dedupe_key,
        raw_ws=fill.raw,
    )
