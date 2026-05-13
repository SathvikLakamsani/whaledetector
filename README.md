# polymarket-whale-alerts

Read-only Polymarket whale trade alert bot in Python. It detects large executed trades, merges repeated fills that are part of the same move, ranks them by market-relative significance, and sends Discord webhook alerts.

This project is informational only. It does not place orders or copy trade.

## Features

- Detects executed trades in near real time
- Filters to active/eligible markets
- Merges repeated fills by wallet/market/side/time window
- Scores alerts with configurable weights:
  - market relativity
  - absolute trade size
  - wallet 30d closed-position performance
- Sends rich Discord embeds with:
  - severity
  - wallet
  - market
  - side/outcome
  - notional and price
  - market-relative size
  - 30d record and realized PnL
- Persists state in SQLite

## How It Works

The bot ingests trades from two sources:

1. WebSocket market feed (`last_trade_price`) for fast execution updates
2. Data API `/trades` poller fallback (includes wallet address directly)

Both paths feed a common pipeline:

1. Normalize trade/fill
2. Apply whale threshold
3. Store fills for market relativity
4. Merge repeated fills into one group
5. Fetch/cache wallet 30d closed-position stats
6. Score and assign severity
7. Send Discord alert and persist alert record

## Project Structure

```text
app/
  main.py
  config.py
  models.py

  services/
    market_sync.py
    ws_listener.py
    trade_normalizer.py
    trade_resolver.py
    wallet_stats.py
    scorer.py
    notifier.py

  db/
    database.py
    schema.sql

  utils/
    dedupe.py
    formatters.py
    links.py
    time.py
```

## Requirements

- Python 3.11+
- Discord webhook URL

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set DISCORD_WEBHOOK_URL in .env
python -m app.main
```

## Configuration

Main runtime values are in `.env`.

Important keys:

- `DISCORD_WEBHOOK_URL`
- `MIN_WHALE_USD` (default 15000)
- `MERGE_WINDOW_SECONDS` (default 45)
- `MARKET_MIN_AGE_HOURS`
- `MARKET_MIN_LIQUIDITY_USD`
- `MARKET_MIN_RECENT_VOLUME_USD`
- `SCORING_WEIGHT_RELATIVITY`
- `SCORING_WEIGHT_AMOUNT`
- `SCORING_WEIGHT_SUCCESS`
- `SEVERITY_VERY_LARGE_MIN_SCORE`
- `SEVERITY_EXTREME_MIN_SCORE`
- `WALLET_STATS_CACHE_MINUTES`
- `TRADES_POLL_INTERVAL_SECONDS`
- `TRADES_POLL_LIMIT`

## Alert Severity

Based on composite score bands:

- Large Executed Trade
- Very Large Executed Trade
- Extreme Whale Trade

The composite score uses weighted relativity, amount, and wallet success.

## Data Stored (SQLite)

- `markets`
- `recent_market_fills`
- `trades`
- `merged_trade_groups`
- `wallet_stats`
- `alerts`

Default DB path: `./polymarket_whale_alerts.db`

## Testing

```bash
pytest -q
```

## Troubleshooting

If you see no Discord alerts:

1. Verify webhook:
   - `DISCORD_WEBHOOK_URL` must be set and valid
2. Confirm process is running:
   - `pgrep -fl "python -m app.main"`
3. Lower threshold temporarily for validation:
   - `MIN_WHALE_USD=50`
   - `MERGE_WINDOW_SECONDS=5`
4. Check logs for:
   - websocket connection errors
   - data-api errors
   - discord webhook HTTP errors
5. Check DB counters quickly:
   - `recent_market_fills` should increase if ingestion works
   - `alerts` should increase when notifications send

## Notes

- WebSocket fills may not always map 1:1 to `/trades`; the poller fallback improves reliability.
- Market sync is capped by page limit settings to avoid startup stalls.
- This bot is designed for personal use and easy extension (Telegram/dashboard can be added later).

## Ownership, IP, and Submission Notes

- This repository is intended to contain only code the owner has rights to submit and assign.
- Do not add employer/internal/proprietary code to this project.
- Do not commit secrets (`.env` is ignored; only `.env.example` is tracked).
- Dependency usage is documented in `THIRD_PARTY_NOTICES.md`.
- License terms are in `LICENSE`.
- A submission checklist is provided in `REPO_SUBMISSION_CHECKLIST.md`.
