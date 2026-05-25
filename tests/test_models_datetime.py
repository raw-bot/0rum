"""Schema contract tests for timezone-aware ORM datetime columns."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String

from src.backtesting.historical_loader import HistoricalCandleRecord, _row_dict
from src.models.candle import Candle
from src.models.regime import MarketRegimeORM
from src.models.signal import ApprovedSignalORM, CandidateSignalORM
from src.models.trade import TradeORM


def test_runtime_datetime_columns_are_timezone_aware():
    """Python UTC datetimes must bind cleanly to PostgreSQL timestamptz columns."""
    columns = [
        MarketRegimeORM.__table__.c.timestamp,
        MarketRegimeORM.__table__.c.created_at,
        CandidateSignalORM.__table__.c.created_at,
        ApprovedSignalORM.__table__.c.created_at,
        TradeORM.__table__.c.opened_at,
        TradeORM.__table__.c.closed_at,
        TradeORM.__table__.c.expired_at,
    ]

    for column in columns:
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True, column.name


def test_trade_risk_accounting_columns_are_nullable_decimals():
    """Risk accounting values are persisted as fixed-precision nullable decimals."""
    expected_columns = {
        "equity_at_open": (12, 2),
        "notional_usd": (14, 2),
        "risk_amount_usd": (12, 2),
    }

    for column_name, (precision, scale) in expected_columns.items():
        column = TradeORM.__table__.c[column_name]

        assert isinstance(column.type, Numeric)
        assert column.type.precision == precision
        assert column.type.scale == scale
        assert column.nullable is True


def test_trade_expiry_index_exists_on_expired_at():
    """The ORM metadata mirrors the migration index for expired theoretical trades."""
    indexes_by_name = {index.name: index for index in TradeORM.__table__.indexes}

    index = indexes_by_name["idx_trades_expired_at"]

    assert [column.name for column in index.columns] == ["expired_at"]


def test_candle_source_provenance_columns_are_nullable_strings():
    """Candle provenance must survive DB readback for optimizer source gating."""
    source_kind = Candle.__table__.c["source_kind"]
    research_source = Candle.__table__.c["research_source"]

    assert isinstance(source_kind.type, String)
    assert source_kind.type.length == 20
    assert source_kind.nullable is True
    assert isinstance(research_source.type, String)
    assert research_source.type.length == 30
    assert research_source.nullable is True


def test_historical_loader_insert_payload_includes_dukascopy_provenance():
    """Dukascopy import rows must persist research provenance into candles."""
    record = HistoricalCandleRecord(
        instrument="XAUUSD",
        timeframe="H1",
        timestamp=datetime(2026, 5, 1, tzinfo=timezone.utc),
        open=Decimal("2300.00000"),
        high=Decimal("2310.00000"),
        low=Decimal("2290.00000"),
        close=Decimal("2305.00000"),
        volume=100,
    )

    row = _row_dict(record)

    assert row["source_kind"] == "research"
    assert row["research_source"] == "dukascopy"
