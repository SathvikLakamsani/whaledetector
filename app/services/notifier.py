"""Discord webhook notifications (read-only outbound HTTP)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import Settings
from app.models import AlertContext, Side
from app.utils.formatters import format_price_cents, format_usd, relativity_line, shorten_wallet
from app.utils.links import polygon_address_url, polymarket_market_url
from app.utils.time import isoformat_z, ms_to_datetime

logger = logging.getLogger(__name__)

_SEVERITY_COLORS = {
    "Large Executed Trade": 0x3498DB,
    "Very Large Executed Trade": 0xF1C40F,
    "Extreme Whale Trade": 0xE74C3C,
}


def _embed_for_alert(ctx: AlertContext) -> dict[str, Any]:
    m = ctx.merged
    wallet_short = shorten_wallet(m.wallet)
    market_url = polymarket_market_url(m.slug, m.event_slug)
    title_text = m.title
    if market_url:
        title_linked = f"[{title_text}]({market_url})"
    else:
        title_linked = title_text

    verb = "bought" if m.side == Side.BUY else "sold"
    oc = m.outcome.upper() if m.outcome.lower() in ("yes", "no") else m.outcome
    core = (
        f"Wallet `{wallet_short}` {verb} **{format_usd(m.total_notional_usd)} {oc}** "
        f"at **{format_price_cents(m.weighted_avg_price)}** on **{title_linked}**."
    )

    fields = [
        {"name": "Market-relative size", "value": relativity_line(ctx.relativity.percentile), "inline": False},
        {
            "name": "30d closed-position record",
            "value": f"**{ctx.wallet_perf.record_display()}**",
            "inline": True,
        },
        {
            "name": "30d realized PnL",
            "value": f"**{format_usd(ctx.wallet_perf.realized_pnl_usd, signed=True)}**",
            "inline": True,
        },
        {"name": "Merged fills", "value": str(m.fill_count), "inline": True},
        {
            "name": "Wallet (full)",
            "value": f"`{m.wallet}`\n<{polygon_address_url(m.wallet)}>",
            "inline": False,
        },
    ]

    embed: dict[str, Any] = {
        "title": ctx.severity_label,
        "description": core,
        "color": _SEVERITY_COLORS.get(ctx.severity_label, 0x95A5A6),
        "fields": fields,
        "timestamp": isoformat_z(ms_to_datetime(m.end_ts_ms)),
    }
    if market_url:
        embed["url"] = market_url
    return embed


async def send_discord_alert(settings: Settings, ctx: AlertContext) -> bool:
    url = (settings.discord_webhook_url or "").strip()
    if not url or "REPLACE_ME" in url:
        logger.warning("DISCORD_WEBHOOK_URL missing or placeholder; skipping alert")
        return False

    payload: dict[str, Any] = {
        "embeds": [_embed_for_alert(ctx)],
        "allowed_mentions": {"parse": []},
    }
    if settings.discord_webhook_username:
        payload["username"] = settings.discord_webhook_username
    if settings.discord_webhook_avatar_url:
        payload["avatar_url"] = settings.discord_webhook_avatar_url

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=settings.http_timeout_seconds)
            if r.status_code >= 400:
                logger.error("discord webhook failed %s: %s", r.status_code, r.text[:500])
                return False
    except httpx.HTTPError as e:
        logger.error("discord webhook error: %s", e)
        return False

    logger.info("discord alert sent for wallet=%s market=%s", ctx.merged.wallet[:10], ctx.merged.condition_id[:10])
    return True


def snapshot_payload(ctx: AlertContext) -> str:
    return json.dumps(
        {
            "severity": ctx.severity_label,
            "wallet": ctx.merged.wallet,
            "market": ctx.merged.title,
            "composite": ctx.score.composite,
        },
        default=str,
    )
