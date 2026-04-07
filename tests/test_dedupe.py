from app.models import Side, TradeEvent
from app.utils.dedupe import FillMerger, build_merged_trade, merge_key_for_trade, weighted_average_price


def _t(
    *,
    wallet: str = "0xaaa",
    condition: str = "0xccc",
    asset: str = "aid",
    side: Side = Side.BUY,
    outcome: str = "YES",
    price: float = 0.5,
    size: float = 100,
    ts_ms: int = 1_000,
    dedupe: str = "d1",
) -> TradeEvent:
    return TradeEvent(
        wallet=wallet,
        condition_id=condition,
        asset_id=asset,
        side=side,
        outcome=outcome,
        price=price,
        size=size,
        notional_usd=price * size,
        ts_ms=ts_ms,
        tx_hash=None,
        dedupe_key=dedupe,
        raw_ws={},
    )


def test_merge_key_normalizes_wallet_case() -> None:
    a = _t(wallet="0xAbC")
    k = merge_key_for_trade(a)
    assert k.wallet == "0xabc"


def test_weighted_average_price() -> None:
    t1 = _t(price=0.4, size=100, ts_ms=1)
    t2 = _t(price=0.6, size=100, ts_ms=2, dedupe="d2")
    assert weighted_average_price([t1, t2]) == 0.5


def test_fill_merger_closes_after_idle_window() -> None:
    merger = FillMerger(window_ms=1_000)
    merger.add(_t(ts_ms=10_000))
    assert merger.close_if_idle(10_500) == []
    closed = merger.close_if_idle(11_100)
    assert len(closed) == 1
    assert len(closed[0]) == 1


def test_build_merged_trade_sums_notional() -> None:
    t1 = _t(size=50, price=0.5, ts_ms=1, dedupe="a")
    t2 = _t(size=50, price=0.5, ts_ms=2, dedupe="b")
    m = build_merged_trade([t2, t1], title="M", slug="s", event_slug="e")
    assert m.fill_count == 2
    assert m.total_notional_usd == 50.0

