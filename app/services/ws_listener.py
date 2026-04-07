"""Polymarket CLOB market websocket client (executed trades via `last_trade_price`)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
from collections.abc import Awaitable, Callable
from typing import Any

import certifi
import websockets

from app.config import Settings

logger = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

OnMessage = Callable[[dict[str, Any]], Awaitable[None]]


def _subscription_payload(asset_ids: list[str], *, subscribe: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "assets_ids": asset_ids,
        "type": "market",
        "custom_feature_enabled": True,
    }
    if subscribe:
        body["operation"] = "subscribe"
    return body


async def _send_initial_subscription(ws: Any, batch: list[str]) -> None:
    msg = json.dumps(_subscription_payload(batch, subscribe=False))
    await ws.send(msg)


async def _subscribe_more(ws: Any, batch: list[str]) -> None:
    if not batch:
        return
    msg = json.dumps(_subscription_payload(batch, subscribe=True))
    await ws.send(msg)


async def run_market_ws(
    settings: Settings,
    asset_ids: list[str],
    on_message: OnMessage,
) -> None:
    """
    Long-running loop with exponential backoff reconnect.

    Subscribes in batches of `ws_trades_batch_size`. Caller should refresh `asset_ids`
    by restarting this task when the subscription universe changes materially.
    """
    backoff = 1.0
    max_back = settings.ws_reconnect_max_seconds
    batch_size = max(1, settings.ws_trades_batch_size)
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    while True:
        try:
            logger.info("connecting market websocket %s", WS_URL)
            async with websockets.connect(
                WS_URL,
                ping_interval=None,
                close_timeout=5,
                max_size=2**23,
                ssl=ssl_context,
            ) as ws:
                backoff = 1.0
                if not asset_ids:
                    logger.warning("no asset ids to subscribe; sleeping")
                    await asyncio.sleep(10)
                    continue

                first = asset_ids[:batch_size]
                await _send_initial_subscription(ws, first)
                rest = asset_ids[batch_size:]
                for i in range(0, len(rest), batch_size):
                    await _subscribe_more(ws, rest[i : i + batch_size])

                ping_task = asyncio.create_task(_ping_loop(ws, settings.ws_ping_interval_seconds))
                try:
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        s = str(raw).strip()
                        if s == "PONG":
                            continue
                        if s == "PING":
                            await ws.send("PING")
                            continue
                        try:
                            payload = json.loads(s)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(payload, dict):
                            await on_message(payload)
                finally:
                    ping_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await ping_task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("websocket error; reconnecting in %.1fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(max_back, max(1.0, backoff * 2))


async def _ping_loop(ws: Any, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.send("PING")
        except Exception:
            return

