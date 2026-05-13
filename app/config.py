"""Environment-driven configuration (pydantic-settings)."""

from __future__ import annotations

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    discord_webhook_url: str = ""
    discord_webhook_username: str = "Polymarket Whale Alerts"
    discord_webhook_avatar_url: str = ""

    database_path: str = "./polymarket_whale_alerts.db"

    min_whale_usd: float = 15_000.0
    merge_window_seconds: float = 45.0

    market_min_age_hours: float = 24.0
    market_min_liquidity_usd: float = 25_000.0
    market_min_recent_volume_usd: float = 50_000.0
    market_min_recent_fills: int = 50
    market_sync_interval_seconds: float = 300.0
    market_page_limit: int = 150
    market_max_pages: int = 20
    max_subscribed_assets: int = 400

    relativity_lookback_days: int = 7

    scoring_weight_relativity: float = 0.55
    scoring_weight_amount: float = 0.30
    scoring_weight_success: float = 0.15

    amount_score_ref_min_usd: float = 10_000.0
    amount_score_ref_max_usd: float = 500_000.0

    severity_very_large_min_score: float = 0.62
    severity_extreme_min_score: float = 0.82

    wallet_stats_cache_minutes: int = 15
    closed_positions_page_limit: int = 50

    ws_ping_interval_seconds: float = 10.0
    ws_reconnect_max_seconds: float = 120.0
    ws_trades_batch_size: int = 80
    trades_poll_interval_seconds: float = 2.0
    trades_poll_limit: int = 500
    max_polled_trade_age_seconds: int = 3600

    http_timeout_seconds: float = 30.0
    data_api_base_url: str = "https://data-api.polymarket.com"
    gamma_api_base_url: str = "https://gamma-api.polymarket.com"

    log_level: str = "INFO"

    trade_match_time_tolerance_seconds: float = 5.0
    trade_match_size_tolerance_ratio: float = 0.02

    @field_validator("scoring_weight_relativity", "scoring_weight_amount", "scoring_weight_success")
    @classmethod
    def weights_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("scoring weights must be non-negative")
        return v

    @field_validator(
        "min_whale_usd",
        "market_min_liquidity_usd",
        "market_min_recent_volume_usd",
        "market_min_recent_fills",
        "amount_score_ref_min_usd",
        "severity_very_large_min_score",
        "severity_extreme_min_score",
    )
    @classmethod
    def enforce_operator_minimums(cls, v: float | int, info: ValidationInfo) -> float | int:
        """
        Hard safety floors so production settings can't be loosened accidentally.
        These match the minimums requested in `.env`.
        """
        floors: dict[str, float] = {
            "min_whale_usd": 15_000.0,
            "market_min_liquidity_usd": 25_000.0,
            "market_min_recent_volume_usd": 50_000.0,
            "market_min_recent_fills": 50.0,
            "amount_score_ref_min_usd": 10_000.0,
            "severity_very_large_min_score": 0.62,
            "severity_extreme_min_score": 0.82,
        }
        floor = floors[info.field_name]
        if float(v) < floor:
            raise ValueError(f"{info.field_name} must be >= {floor}")
        return v

    def normalized_scoring_weights(self) -> tuple[float, float, float]:
        a, b, c = (
            self.scoring_weight_relativity,
            self.scoring_weight_amount,
            self.scoring_weight_success,
        )
        s = a + b + c
        if s <= 0:
            return (1 / 3, 1 / 3, 1 / 3)
        return (a / s, b / s, c / s)


def load_settings() -> Settings:
    return Settings()
