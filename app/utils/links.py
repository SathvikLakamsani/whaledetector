"""Polymarket / explorer URLs."""

from __future__ import annotations


def polymarket_market_url(slug: str | None, event_slug: str | None) -> str | None:
    if slug:
        return f"https://polymarket.com/market/{slug}"
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    return None


def polygon_address_url(address: str) -> str:
    return f"https://polygonscan.com/address/{address}"
