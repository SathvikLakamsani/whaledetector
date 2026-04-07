from app.services.trade_normalizer import parse_last_trade_price


def test_parse_last_trade_price_ok() -> None:
    fill = parse_last_trade_price(
        {
            "event_type": "last_trade_price",
            "market": "0x6a67b9d828d53862160e470329ffea5246f338ecfffdf2cab45211ec578b0347",
            "asset_id": "114122071509644379678018727908709560226618148003371446110114509806601493071694",
            "side": "BUY",
            "price": "0.456",
            "size": "219.217767",
            "timestamp": "1750428146322",
        }
    )
    assert fill is not None
    assert fill.side.value == "BUY"
    assert abs(fill.notional_usd - 219.217767 * 0.456) < 1e-6


def test_parse_ignores_other_events() -> None:
    assert parse_last_trade_price({"event_type": "book"}) is None
