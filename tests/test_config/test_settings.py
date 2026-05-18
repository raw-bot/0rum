"""Tests for src/config.py Settings — focused on Phase 6 additions (theoretical_equity_usd).

Uses pytest's monkeypatch fixture to set env vars cleanly.
"""

from decimal import Decimal

import pytest

from src.config import Settings


def test_theoretical_equity_default():
    """Settings() with no env override returns Decimal('10000')."""
    s = Settings()
    assert s.theoretical_equity_usd == Decimal("10000")
    assert isinstance(s.theoretical_equity_usd, Decimal)


def test_theoretical_equity_env_override(monkeypatch):
    """THEORETICAL_EQUITY_USD env var is parsed to Decimal by pydantic-settings."""
    monkeypatch.setenv("THEORETICAL_EQUITY_USD", "20000")
    # Read Settings() directly to keep this test independent of get_settings() cache behavior.
    s = Settings()
    assert s.theoretical_equity_usd == Decimal("20000")
    assert isinstance(s.theoretical_equity_usd, Decimal)


def test_theoretical_equity_decimal_arithmetic_works():
    """Decimal × Decimal(str(float)) avoids float imprecision (Pitfall 5 invariant).

    The sizer (Plan 04) computes: equity * Decimal(str(risk_pct)).
    This test locks that pattern as the project convention.
    """
    equity = Decimal("10000")
    risk_pct = 0.01
    risk_amount = equity * Decimal(str(risk_pct))
    assert risk_amount == Decimal("100.00")
