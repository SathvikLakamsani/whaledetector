-- polymarket-whale-alerts — SQLite schema

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS markets (
    condition_id TEXT PRIMARY KEY,
    market_id TEXT,
    title TEXT NOT NULL,
    slug TEXT,
    event_slug TEXT,
    yes_asset_id TEXT,
    no_asset_id TEXT,
    category TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    liquidity_usd REAL,
    volume_total_usd REAL,
    volume_24h_usd REAL,
    created_at TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_markets_active ON markets(active);

-- Every observed last_trade_price (for market-relative stats). No wallet required.
CREATE TABLE IF NOT EXISTS recent_market_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    notional_usd REAL NOT NULL,
    ts_ms INTEGER NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_recent_fills_market_ts
    ON recent_market_fills(condition_id, ts_ms DESC);

-- Executed trades we persisted (whale path + optional audit)
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    tx_hash TEXT,
    wallet TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    side TEXT NOT NULL,
    outcome TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    notional_usd REAL NOT NULL,
    ts_ms INTEGER NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_condition_ts ON trades(condition_id, ts_ms DESC);
CREATE INDEX IF NOT EXISTS idx_trades_wallet ON trades(wallet);

CREATE TABLE IF NOT EXISTS merged_trade_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_uuid TEXT NOT NULL UNIQUE,
    wallet TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    side TEXT NOT NULL,
    outcome TEXT NOT NULL,
    start_ts_ms INTEGER NOT NULL,
    end_ts_ms INTEGER NOT NULL,
    fill_count INTEGER NOT NULL,
    total_notional_usd REAL NOT NULL,
    weighted_avg_price REAL NOT NULL,
    final_score REAL,
    severity_label TEXT,
    alerted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wallet_stats (
    wallet TEXT PRIMARY KEY,
    wins_30d INTEGER NOT NULL,
    closed_positions_30d INTEGER NOT NULL,
    win_rate_30d REAL NOT NULL,
    realized_pnl_30d REAL NOT NULL,
    last_refreshed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merged_group_id INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    destination TEXT NOT NULL DEFAULT 'discord',
    payload_json TEXT NOT NULL,
    FOREIGN KEY (merged_group_id) REFERENCES merged_trade_groups(id)
);

CREATE INDEX IF NOT EXISTS idx_alerts_group ON alerts(merged_group_id);
