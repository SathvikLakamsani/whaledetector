"""Ranking: market relativity, size, wallet success -> composite score + severity."""

from __future__ import annotations

import math
from statistics import median
from typing import Sequence

import aiosqlite

from app.config import Settings
from app.db import database as db
from app.models import RelativityStats, ScoreBreakdown, WalletPerformance30d


def _percentile_rank(value: float, samples: Sequence[float]) -> float:
    if not samples:
        return 50.0
    below = sum(1 for x in samples if x < value)
    return 100.0 * below / len(samples)


def _quantile_sorted(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = (len(sorted_vals) - 1) * q
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    w = idx - lo
    return sorted_vals[lo] * (1 - w) + sorted_vals[hi] * w


async def load_market_notionals(
    conn: aiosqlite.Connection,
    condition_id: str,
    lookback_days: int,
) -> list[float]:
    cutoff_ms = lookback_days * 86_400_000
    row = await db.fetch_one(
        conn, "SELECT MAX(ts_ms) AS m FROM recent_market_fills WHERE condition_id = ?", (condition_id,)
    )
    if not row or row["m"] is None:
        return []
    max_ts = int(row["m"])
    boundary = max_ts - cutoff_ms
    rows = await db.fetch_all(
        conn,
        """
        SELECT notional_usd FROM recent_market_fills
        WHERE condition_id = ? AND ts_ms >= ?
        """,
        (condition_id, boundary),
    )
    return [float(r["notional_usd"]) for r in rows]


def compute_relativity_stats(notional: float, samples: list[float]) -> RelativityStats:
    s = sorted(samples)
    percentile = _percentile_rank(notional, s)
    med = median(s) if s else 0.0
    p90 = _quantile_sorted(s, 0.90) if s else 0.0
    ratio_med = (notional / med) if med > 0 else 1.0
    ratio_p90 = (notional / p90) if p90 > 0 else 1.0
    return RelativityStats(
        percentile=percentile,
        ratio_to_median=ratio_med,
        ratio_to_p90=ratio_p90,
        sample_count=len(s),
    )


def relativity_component(stats: RelativityStats) -> float:
    """Map stats to [0,1], emphasizing percentile with p90 ratio as tie-breaker."""
    p = max(0.0, min(1.0, stats.percentile / 100.0))
    r = stats.ratio_to_p90
    ratio_term = math.log1p(max(0.0, r - 1.0)) / math.log1p(50.0)
    ratio_term = max(0.0, min(1.0, ratio_term))
    return 0.65 * p + 0.35 * ratio_term


def amount_component(notional: float, settings: Settings) -> float:
    lo = max(settings.amount_score_ref_min_usd, 1.0)
    hi = max(settings.amount_score_ref_max_usd, lo * 2)
    if notional <= lo:
        return 0.0
    if notional >= hi:
        return 1.0
    return (math.log10(notional) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))


def success_component(perf: WalletPerformance30d, settings: Settings) -> float:
    # Win rate dominates; realized PnL adds a bounded bump.
    wr = max(0.0, min(1.0, perf.win_rate))
    pnl = perf.realized_pnl_usd
    pnl_term = math.tanh(pnl / 25_000.0) * 0.5 + 0.5  # in (0,1)
    return 0.75 * wr + 0.25 * pnl_term


def assign_severity(score: float, settings: Settings) -> str:
    if score >= settings.severity_extreme_min_score:
        return "Extreme Whale Trade"
    if score >= settings.severity_very_large_min_score:
        return "Very Large Executed Trade"
    return "Large Executed Trade"


def build_score(
    rel: float,
    amt: float,
    succ: float,
    settings: Settings,
) -> ScoreBreakdown:
    wr, wa, ws = settings.normalized_scoring_weights()
    comp = wr * rel + wa * amt + ws * succ
    return ScoreBreakdown(
        relativity_score=rel,
        amount_score=amt,
        success_score=succ,
        composite=comp,
    )


async def score_merged_trade(
    conn: aiosqlite.Connection,
    settings: Settings,
    *,
    total_notional_usd: float,
    condition_id: str,
    perf: WalletPerformance30d,
) -> tuple[ScoreBreakdown, RelativityStats, str]:
    samples = await load_market_notionals(conn, condition_id, settings.relativity_lookback_days)
    stats = compute_relativity_stats(total_notional_usd, samples)
    if stats.sample_count < settings.market_min_recent_fills:
        # Not enough local history yet — neutral-ish relativity so amount can still surface whales.
        rel = 0.45
    else:
        rel = relativity_component(stats)

    amt = amount_component(total_notional_usd, settings)
    succ = success_component(perf, settings)
    breakdown = build_score(rel, amt, succ, settings)
    sev = assign_severity(breakdown.composite, settings)
    return breakdown, stats, sev
