"""Normalize websocket payloads into internal fill events."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.models import RawFillEvent, Side

logger = logging.getLogger(__name__)


def _stable_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()


def parse_last_trade_price(payload: dict[str, Any]) -> RawFillEvent | None:
    """
    Parse `last_trade_price` from the market websocket.

    Docs: https://docs.polymarket.com/market-data/websocket/market-channel
    """
    if payload.get("event_type") != "last_trade_price":
        return None
    try:
        condition_id = str(payload["market"]).lower()
        asset_id = str(payload["asset_id"])
        side = Side(str(payload["side"]).upper())
        price = float(payload["price"])
        size = float(payload["size"])
        ts_raw = payload["timestamp"]
        ts_ms = _coerce_ts_ms(ts_raw)
    except (KeyError, TypeError, ValueError) as e:
        logger.debug("skip last_trade_price parse error: %s payload=%s", e, payload)
        return None

    if price <= 0 or size <= 0 or not condition_id.startswith("0x"):
        return None

    notional = size * price
    dedupe = _stable_hash(condition_id, asset_id, side.value, f"{price:.12g}", f"{size:.12g}", str(ts_ms))
    return RawFillEvent(
        condition_id=condition_id,
        asset_id=asset_id,
        side=side,
        price=price,
        size=size,
        notional_usd=notional,
        ts_ms=ts_ms,
        raw=payload,
        dedupe_key=dedupe,
    )


def _coerce_ts_ms(ts_raw: Any) -> int:
    ts = int(str(ts_raw))
    # Heuristic: Polymarket WS examples use ms; Data API trades use seconds.
    if ts < 10**11:
        return ts * 1000
    return ts


def loads_json_message(data: str) -> dict[str, Any] | None:
    s = data.strip()
    if s in ("PONG", "pong"):
        return None
    if s == "PING":
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        logger.debug("non-json ws message: %s", s[:200])
        return None
