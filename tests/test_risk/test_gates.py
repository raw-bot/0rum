"""Tests for src/risk/gates.py — async DB-read functions against TradeORM.

Per D-01, TradeORM is read-only and may be empty (Phase 7 populates it).
Per Pitfall 4, gates accept an explicit session — never open AsyncSessionLocal internally.
The _mock_session helper from tests/test_risk/helpers.py builds a MagicMock that returns a
configured scalar_one().
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.risk.account import (
    get_daily_equity_pnl_pct,
    get_max_drawdown_pct,
    get_open_exposure,
)
from src.risk.gates import (
    count_same_direction_open,
    evaluate_daily_loss,
    evaluate_max_positions,
    get_daily_pnl_pct,
    get_open_positions,
)
from tests.test_risk.helpers import _mock_session


def _mock_daily_pnl_session(
    total_pnl: Decimal,
    equity_base: Decimal = Decimal("10000"),
):
    result = MagicMock()
    result.one = MagicMock(return_value=(total_pnl, equity_base))
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


def _mock_scalars_session(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


def _executed_sql(session) -> str:
    stmt = session.execute.call_args.args[0]
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# RISK-01: daily loss gate


@pytest.mark.asyncio
async def test_daily_loss_passes_when_empty():
    # Empty TradeORM coalesces SUM to 0 (Pitfall 3).
    session = _mock_daily_pnl_session(Decimal("0"))
    passed, pnl = await evaluate_daily_loss(session, daily_loss_limit=-0.03)
    assert passed is True
    assert pnl == Decimal("0")


@pytest.mark.asyncio
async def test_daily_loss_blocks_at_threshold():
    # A3 lock: daily_pnl_pct == -0.03 trips the gate (passed uses >, so -0.03 is not > -0.03).
    session = _mock_daily_pnl_session(Decimal("-300"))
    passed, pnl = await evaluate_daily_loss(session, daily_loss_limit=-0.03)
    assert passed is False
    assert pnl == Decimal("-0.03")


@pytest.mark.asyncio
async def test_daily_loss_blocks_below_threshold():
    session = _mock_daily_pnl_session(Decimal("-500"))
    passed, pnl = await evaluate_daily_loss(session, daily_loss_limit=-0.03)
    assert passed is False
    assert pnl == Decimal("-0.05")


@pytest.mark.asyncio
async def test_daily_loss_uses_closed_money_pnl_over_equity_at_open():
    result = MagicMock()
    result.one = MagicMock(return_value=(Decimal("-310"), Decimal("10000")))
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    daily_pnl_pct = await get_daily_equity_pnl_pct(
        session,
        fallback_equity=Decimal("10000"),
    )
    assert daily_pnl_pct == Decimal("-0.031")

    passed, daily_pnl_pct = await evaluate_daily_loss(
        session,
        daily_loss_limit=Decimal("-0.03"),
        fallback_equity=Decimal("10000"),
    )
    assert daily_pnl_pct == Decimal("-0.031")
    assert passed is False


@pytest.mark.asyncio
async def test_max_drawdown_uses_peak_to_trough_equity_curve():
    session = _mock_scalars_session([Decimal("10000"), Decimal("12000")])
    current_state = SimpleNamespace(equity=Decimal("10800"))

    with patch(
        "src.risk.account.compute_current_paper_state",
        new_callable=AsyncMock,
        return_value=current_state,
    ):
        drawdown = await get_max_drawdown_pct(
            session,
            starting_balance=Decimal("10000"),
        )

    assert drawdown == Decimal("0.10")


@pytest.mark.asyncio
async def test_open_exposure_tp1_buy_uses_half_size_and_downside_stop_risk():
    trade = SimpleNamespace(
        status="TP1_HIT",
        direction="BUY",
        entry_price=Decimal("100"),
        sl_price=Decimal("90"),
        trailing_stop_price=Decimal("110"),
        size_lots=Decimal("2.00"),
        notional_usd=Decimal("20000"),
        risk_amount_usd=Decimal("2000"),
    )
    session = _mock_scalars_session([trade])

    notional, stop_risk = await get_open_exposure(session)

    assert notional == Decimal("10000.000")
    assert stop_risk == Decimal("0.000")


# RISK-02: max positions gate


@pytest.mark.asyncio
async def test_max_positions_passes_when_empty():
    session = _mock_session(0)
    passed, count = await evaluate_max_positions(session, max_positions=5)
    assert passed is True
    assert count == 0
    sql = _executed_sql(session)
    assert "OPEN" in sql
    assert "TP1_HIT" in sql


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
    sql = _executed_sql(session)
    assert "OPEN" in sql
    assert "TP1_HIT" in sql


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
    sql = _executed_sql(session_count)
    assert "OPEN" in sql
    assert "TP1_HIT" in sql

    session_pnl = _mock_daily_pnl_session(Decimal("0"))
    daily_pnl = await get_daily_pnl_pct(session_pnl)
    assert daily_pnl == 0.0
