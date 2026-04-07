"""
Merge repeated fills (same wallet, market, side) within a short window.

Designed for unit tests: pure functions separate from asyncio / DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Hashable, TypeVar

from app.models import MergedWhaleTrade, Side, TradeEvent

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class MergeKey:
    wallet: str
    condition_id: str
    asset_id: str
    side: Side


def merge_key_for_trade(t: TradeEvent) -> MergeKey:
    return MergeKey(
        wallet=t.wallet.lower(),
        condition_id=t.condition_id.lower(),
        asset_id=t.asset_id,
        side=t.side,
    )


def weighted_average_price(trades: list[TradeEvent]) -> float:
    num = sum(t.price * t.size for t in trades)
    den = sum(t.size for t in trades)
    if den <= 0:
        return trades[0].price if trades else 0.0
    return num / den


def build_merged_trade(
    trades: list[TradeEvent],
    *,
    title: str,
    slug: str | None,
    event_slug: str | None,
) -> MergedWhaleTrade:
    if not trades:
        raise ValueError("trades must be non-empty")
    trades = sorted(trades, key=lambda x: x.ts_ms)
    first, last = trades[0], trades[-1]
    total_notional = sum(t.notional_usd for t in trades)
    wap = weighted_average_price(trades)
    return MergedWhaleTrade(
        wallet=first.wallet,
        condition_id=first.condition_id,
        asset_id=first.asset_id,
        side=first.side,
        outcome=first.outcome,
        title=title,
        slug=slug,
        event_slug=event_slug,
        total_notional_usd=total_notional,
        weighted_avg_price=wap,
        fill_count=len(trades),
        start_ts_ms=first.ts_ms,
        end_ts_ms=last.ts_ms,
        trades=list(trades),
    )


class FillMerger:
    """
    Buffers trades per MergeKey. When `close_if_idle` finds a group whose last
    trade is older than `window_ms`, it returns a merged bundle and clears it.
    """

    def __init__(self, window_ms: int) -> None:
        self.window_ms = window_ms
        self._groups: dict[MergeKey, list[TradeEvent]] = {}

    def add(self, trade: TradeEvent) -> None:
        k = merge_key_for_trade(trade)
        self._groups.setdefault(k, []).append(trade)

    def close_if_idle(self, now_ms: int) -> list[list[TradeEvent]]:
        """Return lists of trades for groups that have been idle >= window."""
        ready: list[list[TradeEvent]] = []
        to_del: list[MergeKey] = []
        for k, buf in self._groups.items():
            if not buf:
                continue
            last_ts = max(t.ts_ms for t in buf)
            if now_ms - last_ts >= self.window_ms:
                ready.append(sorted(buf, key=lambda x: x.ts_ms))
                to_del.append(k)
        for k in to_del:
            del self._groups[k]
        return ready

    def peek_idle_groups(self, now_ms: int) -> list[MergeKey]:
        keys: list[MergeKey] = []
        for k, buf in self._groups.items():
            if not buf:
                continue
            last_ts = max(t.ts_ms for t in buf)
            if now_ms - last_ts >= self.window_ms:
                keys.append(k)
        return keys


def group_sorted_by_key(
    items: list[T],
    key: Callable[[T], Hashable],
) -> dict[Hashable, list[T]]:
    out: dict[Hashable, list[T]] = {}
    for it in items:
        out.setdefault(key(it), []).append(it)
    return out
