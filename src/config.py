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

    # Local operator surface
    dashboard_token: str = ""
    dashboard_rate_limit_per_minute: int = 120
    kill_switch_redis_key: str = "risk:kill_switch"

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
    trade_expiry_hours: int = 72
    theoretical_equity_usd: Decimal = Decimal("10000")
    max_account_leverage: Decimal = Decimal("10")
    max_stop_risk_pct: Decimal = Decimal("0.05")
    max_equity_drawdown_pct: Decimal = Decimal("0.10")
    concentration_reduce_at: int = 2
    concentration_block_at: int = 4

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
