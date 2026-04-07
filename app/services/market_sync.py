"""Fetch and filter active Polymarket markets (Gamma API) + eligibility heuristics."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings
import aiosqlite

from app.db import database as db
from app.utils.time import isoformat_z, utc_now

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MarketMeta:
    condition_id: str
    market_id: str | None
    title: str
    slug: str | None
    event_slug: str | None
    yes_asset_id: str | None
    no_asset_id: str | None
    category: str | None
    liquidity_usd: float | None
    volume_total_usd: float | None
    volume_24h_usd: float | None
    created_at: datetime | None


class MarketRegistry:
    """In-memory view used by the websocket + pipeline."""

    def __init__(self) -> None:
        self._by_condition: dict[str, MarketMeta] = {}
        self._asset_outcome: dict[str, str] = {}

    def replace(self, markets: list[MarketMeta]) -> None:
        self._by_condition = {m.condition_id.lower(): m for m in markets}
        self._asset_outcome.clear()
        for m in markets:
            if m.yes_asset_id:
                self._asset_outcome[m.yes_asset_id] = "YES"
            if m.no_asset_id:
                self._asset_outcome[m.no_asset_id] = "NO"

    def outcome_for_asset(self, asset_id: str) -> str | None:
        return self._asset_outcome.get(asset_id)

    def meta_for_condition(self, condition_id: str) -> MarketMeta | None:
        return self._by_condition.get(condition_id.lower())

    def subscribed_asset_ids(self, max_assets: int) -> list[str]:
        ids: list[str] = []
        for m in sorted(
            self._by_condition.values(),
            key=lambda x: (x.volume_24h_usd or 0.0),
            reverse=True,
        ):
            for aid in (m.yes_asset_id, m.no_asset_id):
                if aid and aid not in ids:
                    ids.append(aid)
                if len(ids) >= max_assets:
                    return ids
        return ids


def _parse_json_list(s: Any) -> list[Any]:
    if s is None:
        return []
    if isinstance(s, list):
        return s
    if isinstance(s, str):
        try:
            v = json.loads(s)
            return v if isinstance(v, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _parse_dt(s: Any) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _gamma_market_to_meta(raw: dict[str, Any]) -> MarketMeta | None:
    try:
        cid = str(raw.get("conditionId") or "").lower()
        if not cid.startswith("0x"):
            return None
        tokens = [str(x) for x in _parse_json_list(raw.get("clobTokenIds"))]
        outcomes = [str(x) for x in _parse_json_list(raw.get("outcomes"))]
        yes_id: str | None = None
        no_id: str | None = None
        for i, oc in enumerate(outcomes):
            ocl = oc.lower()
            tid = tokens[i] if i < len(tokens) else None
            if ocl == "yes":
                yes_id = tid
            elif ocl == "no":
                no_id = tid
        if yes_id is None and len(tokens) >= 1:
            yes_id = tokens[0]
        if no_id is None and len(tokens) >= 2:
            no_id = tokens[1]

        vol24 = raw.get("volume24hr")
        if vol24 is None:
            vol24 = raw.get("volume24hrClob")
        liquidity = raw.get("liquidityNum")
        if liquidity is None:
            try:
                liquidity = float(raw.get("liquidity") or 0)
            except (TypeError, ValueError):
                liquidity = None

        vol_total = raw.get("volumeNum")
        if vol_total is None:
            try:
                vol_total = float(raw.get("volume") or 0)
            except (TypeError, ValueError):
                vol_total = None

        ev = raw.get("events") or []
        event_slug = None
        if isinstance(ev, list) and ev:
            event_slug = ev[0].get("slug")

        return MarketMeta(
            condition_id=cid,
            market_id=str(raw.get("id")) if raw.get("id") is not None else None,
            title=str(raw.get("question") or raw.get("title") or cid),
            slug=raw.get("slug"),
            event_slug=event_slug,
            yes_asset_id=yes_id,
            no_asset_id=no_id,
            category=raw.get("category"),
            liquidity_usd=float(liquidity) if liquidity is not None else None,
            volume_total_usd=float(vol_total) if vol_total is not None else None,
            volume_24h_usd=float(vol24) if vol24 is not None else None,
            created_at=_parse_dt(raw.get("createdAt")),
        )
    except (TypeError, ValueError):
        return None


async def sync_markets(
    conn: aiosqlite.Connection,
    client: httpx.AsyncClient,
    settings: Settings,
    registry: MarketRegistry,
) -> int:
    """
    Pull active markets from Gamma, apply filters, persist, refresh registry.
    Returns number of markets kept.
    """
    now = utc_now()
    oldest_allowed_created = now - timedelta(hours=settings.market_min_age_hours)
    kept: list[tuple[MarketMeta, dict[str, Any]]] = []
    offset = 0
    base = f"{settings.gamma_api_base_url.rstrip('/')}/markets"

    pages_fetched = 0
    while True:
        params = {
            "active": "true",
            "closed": "false",
            "limit": settings.market_page_limit,
            "offset": offset,
        }
        r = await client.get(base, params=params, timeout=settings.http_timeout_seconds)
        r.raise_for_status()
        chunk = r.json()
        if not isinstance(chunk, list) or not chunk:
            break

        for raw in chunk:
            m = _gamma_market_to_meta(raw)
            if m is None:
                continue
            if m.created_at is not None:
                c = m.created_at
                if c.tzinfo is None:
                    c = c.replace(tzinfo=timezone.utc)
                if c > oldest_allowed_created.astimezone(timezone.utc):
                    continue

            if (m.liquidity_usd or 0) < settings.market_min_liquidity_usd:
                continue
            if (m.volume_24h_usd or 0) < settings.market_min_recent_volume_usd and (
                m.volume_total_usd or 0
            ) < settings.market_min_recent_volume_usd * 10:
                # allow very high total vol markets even if 24h is stale
                continue

            kept.append((m, raw))

        pages_fetched += 1
        offset += len(chunk)
        if len(chunk) < settings.market_page_limit:
            break
        if pages_fetched >= settings.market_max_pages:
            logger.info(
                "market sync page cap reached (%s pages, %s rows); continuing with partial universe",
                pages_fetched,
                offset,
            )
            break

    # De-dupe by condition id (keep highest 24h volume)
    best: dict[str, tuple[MarketMeta, dict[str, Any]]] = {}
    for m, raw in kept:
        cur = best.get(m.condition_id)
        if cur is None or (m.volume_24h_usd or 0) > (cur[0].volume_24h_usd or 0):
            best[m.condition_id] = (m, raw)

    ordered = sorted(best.values(), key=lambda x: (x[0].volume_24h_usd or 0), reverse=True)
    final_list = [x[0] for x in ordered]
    registry.replace(final_list)

    now_iso = isoformat_z(now)
    upsert_sql = """
        INSERT INTO markets (
          condition_id, market_id, title, slug, event_slug,
          yes_asset_id, no_asset_id, category, active,
          liquidity_usd, volume_total_usd, volume_24h_usd,
          created_at, first_seen_at, updated_at, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)
        ON CONFLICT(condition_id) DO UPDATE SET
          market_id=excluded.market_id,
          title=excluded.title,
          slug=excluded.slug,
          event_slug=excluded.event_slug,
          yes_asset_id=excluded.yes_asset_id,
          no_asset_id=excluded.no_asset_id,
          category=excluded.category,
          liquidity_usd=excluded.liquidity_usd,
          volume_total_usd=excluded.volume_total_usd,
          volume_24h_usd=excluded.volume_24h_usd,
          created_at=excluded.created_at,
          first_seen_at=markets.first_seen_at,
          updated_at=excluded.updated_at,
          raw_json=excluded.raw_json
    """
    rows = [
        (
            m.condition_id,
            m.market_id,
            m.title,
            m.slug,
            m.event_slug,
            m.yes_asset_id,
            m.no_asset_id,
            m.category,
            m.liquidity_usd,
            m.volume_total_usd,
            m.volume_24h_usd,
            m.created_at.isoformat() if m.created_at else None,
            now_iso,
            now_iso,
            json.dumps(raw, default=str),
        )
        for m, raw in ordered
    ]
    if rows:
        await db.executemany(conn, upsert_sql, rows)

    logger.info("market sync complete: %s markets eligible", len(final_list))
    return len(final_list)
