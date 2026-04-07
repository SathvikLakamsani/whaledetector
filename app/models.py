"""Typed domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class RawFillEvent:
    """Parsed Polymarket `last_trade_price` websocket message."""

    condition_id: str
    asset_id: str
    side: Side
    price: float
    size: float
    notional_usd: float
    ts_ms: int
    raw: dict[str, Any]
    dedupe_key: str


@dataclass(frozen=True, slots=True)
class TradeEvent:
    """Executed trade with wallet (after REST resolution)."""

    wallet: str
    condition_id: str
    asset_id: str
    side: Side
    outcome: str
    price: float
    size: float
    notional_usd: float
    ts_ms: int
    tx_hash: str | None
    dedupe_key: str
    raw_ws: dict[str, Any]


@dataclass(slots=True)
class WalletPerformance30d:
    wallet: str
    wins: int
    closed_positions: int
    win_rate: float
    realized_pnl_usd: float
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def record_display(self) -> str:
        if self.closed_positions <= 0:
            return "0 / 0 profitable (n/a)"
        pct = 100.0 * self.win_rate
        return f"{self.wins} / {self.closed_positions} profitable ({pct:.1f}%)"


@dataclass(slots=True)
class RelativityStats:
    percentile: float
    ratio_to_median: float
    ratio_to_p90: float
    sample_count: int


@dataclass(slots=True)
class ScoreBreakdown:
    relativity_score: float
    amount_score: float
    success_score: float
    composite: float


@dataclass(slots=True)
class MergedWhaleTrade:
    wallet: str
    condition_id: str
    asset_id: str
    side: Side
    outcome: str
    title: str
    slug: str | None
    event_slug: str | None
    total_notional_usd: float
    weighted_avg_price: float
    fill_count: int
    start_ts_ms: int
    end_ts_ms: int
    trades: list[TradeEvent] = field(default_factory=list)


@dataclass(slots=True)
class AlertContext:
    merged: MergedWhaleTrade
    relativity: RelativityStats
    score: ScoreBreakdown
    severity_label: str
    wallet_perf: WalletPerformance30d
