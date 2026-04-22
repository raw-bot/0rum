"""Shared fixtures for Phase 5 backtesting tests.

Sets required environment variables before any src.* import.
All candle fixtures use MagicMock — no real DB dependency.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def make_candle(timestamp, close, high=None, low=None, open_=None):
    """Create a MagicMock candle with float-castable Decimal attributes."""
    c = MagicMock()
    c.timestamp = timestamp
    c.close = Decimal(str(round(close, 5)))
    c.high = Decimal(str(round(high if high is not None else close + 1, 5)))
    c.low = Decimal(str(round(low if low is not None else close - 1, 5)))
    c.open = Decimal(str(round(open_ if open_ is not None else close, 5)))
    c.volume = 1000
    c.complete = True
    return c
