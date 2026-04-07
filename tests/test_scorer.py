from app.config import Settings
from app.models import RelativityStats, WalletPerformance30d
from app.services.scorer import (
    amount_component,
    assign_severity,
    build_score,
    compute_relativity_stats,
    relativity_component,
    success_component,
)


def test_amount_component_bounds() -> None:
    s = Settings()
    assert amount_component(5_000, s) == 0.0
    assert amount_component(500_000, s) >= 0.99


def test_relativity_component_monotone() -> None:
    low = RelativityStats(percentile=10.0, ratio_to_median=1.0, ratio_to_p90=1.0, sample_count=100)
    high = RelativityStats(percentile=95.0, ratio_to_median=10.0, ratio_to_p90=8.0, sample_count=100)
    assert relativity_component(high) > relativity_component(low)


def test_success_component_respects_win_rate() -> None:
    s = Settings()
    bad = WalletPerformance30d("0x", 0, 10, 0.0, -100.0)
    good = WalletPerformance30d("0x", 9, 10, 0.9, 10_000.0)
    assert success_component(good, s) > success_component(bad, s)


def test_assign_severity_bands() -> None:
    s = Settings(severity_very_large_min_score=0.55, severity_extreme_min_score=0.75)
    assert assign_severity(0.40, s) == "Large Executed Trade"
    assert assign_severity(0.60, s) == "Very Large Executed Trade"
    assert assign_severity(0.90, s) == "Extreme Whale Trade"


def test_build_score_weights_normalize() -> None:
    s = Settings(
        scoring_weight_relativity=2.0,
        scoring_weight_amount=2.0,
        scoring_weight_success=2.0,
    )
    b = build_score(1.0, 0.0, 0.0, s)
    assert abs(b.composite - (1 / 3)) < 1e-6


def test_percentile_rank() -> None:
    samples = [1.0, 2.0, 3.0, 4.0]
    st = compute_relativity_stats(3.0, samples)
    assert st.percentile == 50.0
