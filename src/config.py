"""Application configuration loaded from environment variables."""

from decimal import Decimal
from enum import Enum
from functools import lru_cache

from pydantic_settings import BaseSettings


class ExecutionMode(str, Enum):
    """Execution mode for the trading bot."""

    SIGNAL = "signal"
    AUTO = "auto"


class MarketDataProvider(str, Enum):
    """Supported market-data providers for candle ingestion."""

    BINANCE = "binance"


class Settings(BaseSettings):
    """All application settings loaded from .env."""

    # Broker credentials (Phase 7 — execution engine, not needed for ingestion)
    # broker_api_key: str = ""  # placeholder for future live trading

    market_data_provider: MarketDataProvider = MarketDataProvider.BINANCE

    # Database
    database_url: str
    redis_url: str = "redis://redis:6379/0"

    # Execution
    execution_mode: ExecutionMode = ExecutionMode.SIGNAL

    # Risk (TOUS FIXÉS — pas dans l'optimizer)
    risk_per_trade: float = 0.01
    daily_loss_limit: float = -0.03
    max_positions: int = 5
    max_signals_per_day: int = 5
    circuit_breaker_stops: int = 8
    circuit_breaker_cooldown_hours: int = 24
    atr_high_vol_percentile: int = 90
    atr_low_vol_percentile: int = 10
    hard_cap_risk: float = 0.02
    theoretical_equity_usd: Decimal = Decimal("10000")

    # Optimizer
    optimizer_interval_hours: int = 24
    backtest_interval_hours: int = 8
    wf_train_months: int = 6
    wf_test_months: int = 2
    lhs_combos: int = 100
    wfe_minimum: float = 0.50

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
