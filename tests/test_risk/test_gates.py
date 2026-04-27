"""Tests for src/risk/gates.py — async DB-read functions against TradeORM.

Per D-01, TradeORM is read-only and may be empty (Phase 7 populates it).
Per Pitfall 4, gates accept an explicit session — never open AsyncSessionLocal internally.
The _mock_session helper from tests/test_risk/helpers.py builds a MagicMock that returns a
configured scalar_one().
"""

from decimal import Decimal

import pytest

from src.risk.gates import (
    count_same_direction_open,
    evaluate_daily_loss,
    evaluate_max_positions,
    get_daily_pnl_pct,
    get_open_positions,
)
from tests.test_risk.helpers import _mock_session


# RISK-01: daily loss gate


@pytest.mark.asyncio
async def test_daily_loss_passes_when_empty():
    # Empty TradeORM coalesces SUM to 0 (Pitfall 3).
    session = _mock_session(Decimal("0"))
    passed, pnl = await evaluate_daily_loss(session, daily_loss_limit=-0.03)
    assert passed is True
    assert pnl == 0.0


@pytest.mark.asyncio
async def test_daily_loss_blocks_at_threshold():
    # A3 lock: daily_pnl_pct == -0.03 trips the gate (passed uses >, so -0.03 is not > -0.03).
    session = _mock_session(Decimal("-0.03"))
    passed, pnl = await evaluate_daily_loss(session, daily_loss_limit=-0.03)
    assert passed is False
    assert pnl == pytest.approx(-0.03)


@pytest.mark.asyncio
async def test_daily_loss_blocks_below_threshold():
    session = _mock_session(Decimal("-0.05"))
    passed, pnl = await evaluate_daily_loss(session, daily_loss_limit=-0.03)
    assert passed is False
    assert pnl == pytest.approx(-0.05)


# RISK-02: max positions gate


@pytest.mark.asyncio
async def test_max_positions_passes_when_empty():
    session = _mock_session(0)
    passed, count = await evaluate_max_positions(session, max_positions=5)
    assert passed is True
    assert count == 0


@pytest.mark.asyncio
async def test_max_positions_passes_below_threshold():
    session = _mock_session(4)
    passed, count = await evaluate_max_positions(session, max_positions=5)
    assert passed is True
    assert count == 4


@pytest.mark.asyncio
async def test_max_positions_blocks_at_threshold():
    # A4 lock: open_count == 5 trips the gate (passed uses <, so 5 is not < 5).
    session = _mock_session(5)
    passed, count = await evaluate_max_positions(session, max_positions=5)
    assert passed is False
    assert count == 5


# RISK-03: concentration count (threshold lives in the sizer, D-04)


@pytest.mark.asyncio
async def test_count_same_direction_open_returns_int_count():
    session = _mock_session(3)
    result = await count_same_direction_open(session, direction="BUY")
    assert result == 3


@pytest.mark.asyncio
async def test_count_same_direction_open_zero_when_empty():
    session = _mock_session(0)
    result = await count_same_direction_open(session, direction="SELL")
    assert result == 0


# Health helpers (thin wrappers for /health endpoint, Plan 09)


@pytest.mark.asyncio
async def test_get_open_positions_and_daily_pnl_helpers():
    # These thin wrappers feed the /health endpoint (Plan 09).
    session_count = _mock_session(0)
    open_pos = await get_open_positions(session_count)
    assert open_pos == 0

    session_pnl = _mock_session(Decimal("0"))
    daily_pnl = await get_daily_pnl_pct(session_pnl)
    assert daily_pnl == 0.0
