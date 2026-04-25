"""Application configuration loaded from environment variables."""

from enum import Enum

from pydantic_settings import BaseSettings


class ExecutionMode(str, Enum):
    """Execution mode for the trading bot."""

    SIGNAL = "signal"
    AUTO = "auto"


class MarketDataProvider(str, Enum):
    """Supported market-data providers for candle ingestion."""

    BINANCE = "binance"
    IG = "ig"


class Settings(BaseSettings):
    """All application settings loaded from .env."""

    # Broker credentials (Phase 7 — execution engine, not needed for ingestion)
    # broker_api_key: str = ""  # placeholder for future live trading

    market_data_provider: MarketDataProvider = MarketDataProvider.BINANCE

    # IG demo/provider validation (Phase 4.1+)
    ig_api_key: str = ""
    ig_identifier: str = ""
    ig_password: str = ""
    ig_account_id: str = ""
    ig_api_url: str = "https://demo-api.ig.com/gateway/deal"
    ig_xauusd_epic: str = ""

    # IG-light ingestion limits (avoids bulk historical on demo)
    ig_warmup_bars_m15: int = 300   # ~3 days of M15
    ig_warmup_bars_h1: int = 250    # ~10 days of H1 (EMA200 + margin)
    ig_warmup_bars_h4: int = 80     # ~13 days of H4
    ig_warmup_bars_d1: int = 60     # 60 trading days of D1
    ig_max_gap_bars: int = 100      # refuse gap fill > 100 bars per timeframe on IG

    # Database
    database_url: str
    redis_url: str = "redis://redis:6379/0"

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

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

    # Optimizer
    optimizer_interval_hours: int = 24
    backtest_interval_hours: int = 8
    wf_train_months: int = 6
    wf_test_months: int = 2
    lhs_combos: int = 100
    wfe_minimum: float = 0.50

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
