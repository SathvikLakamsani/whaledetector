"""Human-readable formatting for alerts."""

from __future__ import annotations

from app.models import Side


def shorten_wallet(addr: str, prefix: int = 6, suffix: int = 4) -> str:
    a = addr.strip()
    if len(a) <= prefix + suffix + 3:
        return a
    return f"{a[:prefix]}...{a[-suffix:]}"


def format_usd(amount: float, *, signed: bool = False) -> str:
    if signed and amount > 0:
        return f"+${amount:,.0f}"
    if signed and amount < 0:
        return f"-${abs(amount):,.0f}"
    return f"${amount:,.0f}"


def format_price_cents(price: float) -> str:
    cents = price * 100.0
    if abs(cents - round(cents)) < 1e-6:
        return f"{int(round(cents))}¢"
    return f"{cents:.1f}¢"


def trade_summary_line(
    *,
    wallet_short: str,
    side: Side,
    notional: float,
    outcome: str,
    price: float,
    market_title: str,
) -> str:
    verb = "bought" if side == Side.BUY else "sold"
    usd = format_usd(notional, signed=False)
    oc = outcome.upper() if outcome.lower() in ("yes", "no") else outcome
    px = format_price_cents(price)
    return (
        f"Wallet `{wallet_short}` {verb} **{usd} {oc}** at **{px}** "
        f"on **[{market_title}](https://polymarket.com)**."
    )


def relativity_line(percentile: float) -> str:
    p = max(0.0, min(100.0, percentile))
    return f"**{p:.0f}th percentile** among recent fills"
