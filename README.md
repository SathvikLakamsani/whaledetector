# polymarket-whale-alerts

Read-only **Polymarket whale trade alert** service for personal use. It listens to **executed** CLOB trades (`last_trade_price` on the public market websocket), filters and ranks large moves, merges split fills, enriches wallets with **30-day closed-position** stats from Polymarket’s Data API, and posts **Discord** webhook embeds.

This project is **informational only**: it does not place orders, sign transactions, or perform any trading actions.

## What it does

1. **Market sync** — Periodically loads active markets from the **Gamma API**, applies liquidity/volume/age gates, and keeps a local SQLite cache (`markets`).
2. **Realtime executions** — Connects to `wss://ws-subscriptions-clob.polymarket.com/ws/market`, subscribes to the most liquid outcome **token IDs**, and processes `last_trade_price` events (matched trades).
3. **Fill history** — Inserts every observed fill notional into `recent_market_fills` (no wallet needed) to compute **market-relative** size (percentile vs recent fills in that market).
4. **Whale path** — For fills with estimated notional ≥ `MIN_WHALE_USD`, resolves the **taker wallet** by matching the websocket fill to a recent row from `GET https://data-api.polymarket.com/trades?market=<conditionId>` (see assumptions below).
5. **Merge** — Combines fills with the same wallet, market, asset, and side inside `MERGE_WINDOW_SECONDS`.
6. **Score & alert** — Computes a weighted score (relativity / amount / wallet success), assigns a severity label, and sends a rich Discord embed.

## Architecture

```text
Gamma API (markets) ──► market_sync.py ──► SQLite + in-memory registry
                                              │
WebSocket (last_trade_price) ◄── ws_listener.py
         │
         ▼
 trade_normalizer.py (RawFillEvent)
         ├─► SQLite recent_market_fills (all fills)
         └─► trade_resolver.py (Data API /trades) ─► TradeEvent (whales)
                 │
                 ▼
         dedupe.FillMerger ─► scorer.py + wallet_stats.py ─► notifier.py ─► Discord webhook
```

## Setup

Requirements: **Python 3.11+**.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — at minimum set DISCORD_WEBHOOK_URL for alerts
python -m app.main
```

## Environment variables

See `.env.example` for the full list. Important ones:

| Variable | Purpose |
|----------|---------|
| `DISCORD_WEBHOOK_URL` | Incoming webhook URL (empty/placeholder skips sends, logs only) |
| `MIN_WHALE_USD` | Minimum estimated notional (USD) to treat as a whale |
| `MERGE_WINDOW_SECONDS` | Idle window before merging buffered fills |
| `MARKET_MIN_AGE_HOURS` | Ignore very new markets |
| `MARKET_MIN_LIQUIDITY_USD` / `MARKET_MIN_RECENT_VOLUME_USD` | Liquidity / activity gates from Gamma |
| `MARKET_MIN_RECENT_FILLS` | Minimum stored fills before full relativity scoring kicks in |
| `SCORING_WEIGHT_*` | Weights for relativity / amount / wallet success (auto-normalized) |
| `SEVERITY_*_MIN_SCORE` | Cutoffs for severity labels on the composite score |
| `WALLET_STATS_CACHE_MINUTES` | SQLite cache TTL for `/closed-positions` aggregates |
| `DATABASE_PATH` | SQLite file location |

## Scoring

For each merged whale trade:

- **Relativity score** — Derived from the trade’s notional vs `recent_market_fills` for the same `condition_id` inside `RELATIVITY_LOOKBACK_DAYS`: percentile rank plus a capped term from the ratio to the **p90** notional. If there are fewer than `MARKET_MIN_RECENT_FILLS` samples, relativity is damped so amount-driven signal can still surface trades early.
- **Amount score** — Log-scaled between `AMOUNT_SCORE_REF_MIN_USD` and `AMOUNT_SCORE_REF_MAX_USD`.
- **Success score** — Mix of **30-day closed-position win rate** and a bounded term from **30-day realized PnL** (`tanh` scaling).

Composite:

`SCORING_WEIGHT_RELATIVITY * rel + SCORING_WEIGHT_AMOUNT * amt + SCORING_WEIGHT_SUCCESS * succ` (weights renormalized to sum to 1).

**Severity labels** (configurable thresholds):

- `Large Executed Trade`
- `Very Large Executed Trade`
- `Extreme Whale Trade`

## Repeated fill merging

`FillMerger` buffers `TradeEvent`s by `(wallet, condition_id, asset_id, side)`. After `MERGE_WINDOW_SECONDS` pass without a new matching fill, the buffer flushes as one **merged** alert:

- **Total notional** = sum of notionals  
- **Weighted average price** = \(\sum p_i s_i / \sum s_i\)  
- **Fill count** preserved for the Discord field

## Wallet 30-day record

`wallet_stats.py` calls `GET https://data-api.polymarket.com/closed-positions` with pagination, filters positions whose `timestamp` falls within the last **30 days**, and computes:

- **Profitable closes** — `realizedPnl > 0`
- **Win rate** — wins / total closes in window
- **Realized PnL** — sum of `realizedPnl` in window

Displayed like: `9 / 14 profitable (64.3%)`.

Results are cached in `wallet_stats` to stay rate-limit friendly; concurrent refreshes use a double-checked lock pattern.

## Assumptions & limitations

- **Wallet on websocket fills** — The public `last_trade_price` message does **not** include the trader address. This MVP resolves the wallet by **matching** the fill to the latest rows from `GET /trades?market=<conditionId>`. If the match fails (latency, collisions, API shape changes), the fill is skipped.
- **Timestamp units** — Websocket timestamps are treated as **milliseconds** when large; Data API trades often use **seconds**. The matcher normalizes both.
- **Notional** — Estimated as `size * price` in USDC terms as provided by Polymarket messages (same convention as typical CLOB displays).
- **Market “fill count” gate** — An earlier design called `/trades` per candidate market during sync; that fans out quickly and hits rate limits. **This build gates markets using Gamma liquidity + volume only.** Tune `MARKET_MIN_RECENT_VOLUME_USD` / `MARKET_MIN_LIQUIDITY_USD` for stricter “active market” focus.
- **Subscription cap** — Only up to `MAX_SUBSCRIBED_ASSETS` token IDs (sorted by 24h volume) are subscribed; very long tails are ignored by design.
- **Alert latency** — Target is “about a minute” end-to-end under normal conditions; Discord + REST matching add jitter.

## Future improvements

- Telegram / dashboard sinks behind a shared `Notifier` interface
- Smarter wallet resolution (builder metadata, user channel read-only with API keys if ever desired)
- Dynamic subscription diffing without full websocket restart
- Backfill `recent_market_fills` via `/trades` for new markets in one batch job

## Development

```bash
pytest
```

## License

Personal project — add a license if you open-source it.
