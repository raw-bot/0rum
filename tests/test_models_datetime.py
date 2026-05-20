"""Schema contract tests for timezone-aware ORM datetime columns."""

from sqlalchemy import DateTime

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
    ]

    for column in columns:
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True, column.name
