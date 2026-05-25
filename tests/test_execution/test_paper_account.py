"""Tests for isolated internal paper account calculations."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.execution.paper.account import (
    compute_paper_account_state,
    compute_paper_exposure_state,
)


def make_trade(
    *,
    status: str,
    direction: str = "BUY",
    entry: Decimal = Decimal("100"),
    sl: Decimal = Decimal("90"),
    tp1: Decimal = Decimal("112"),
    trail: Decimal | None = None,
    size: Decimal = Decimal("1.00"),
    pnl_pct: Decimal | None = None,
    pnl: Decimal | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        direction=direction,
        entry_price=entry,
        sl_price=sl,
        tp1_price=tp1,
        trailing_stop_price=trail,
        size_lots=size,
        pnl_pct=pnl_pct,
        pnl=pnl,
    )


def test_paper_account_realized_equity_uses_closed_trade_money_pnl():
    state = compute_paper_account_state(
        starting_balance=Decimal("10000"),
        trades=[make_trade(status="CLOSED", pnl=Decimal("-125.50"))],
        mark_price=Decimal("101"),
    )

    assert state.cash_balance == Decimal("9874.50")
    assert state.realized_pnl == Decimal("-125.50")
    assert state.unrealized_pnl == Decimal("0")
    assert state.equity == Decimal("9874.50")
    assert state.open_positions == 0
    assert state.closed_trades_missing_pnl == 0


def test_paper_account_marks_open_short_unrealized_pnl_to_market():
    state = compute_paper_account_state(
        starting_balance=Decimal("10000"),
        trades=[
            make_trade(
                status="OPEN",
                direction="SELL",
                entry=Decimal("4506.87"),
                size=Decimal("0.10"),
            )
        ],
        mark_price=Decimal("4496.87"),
    )

    assert state.cash_balance == Decimal("10000")
    assert state.realized_pnl == Decimal("0")
    assert state.unrealized_pnl == Decimal("100.00")
    assert state.equity == Decimal("10100.00")
    assert state.open_positions == 1


def test_paper_account_marks_tp1_position_as_half_realized_and_half_open():
    state = compute_paper_account_state(
        starting_balance=Decimal("10000"),
        trades=[
            make_trade(
                status="TP1_HIT",
                direction="BUY",
                entry=Decimal("100"),
                tp1=Decimal("112"),
                size=Decimal("1.00"),
            )
        ],
        mark_price=Decimal("120"),
    )

    assert state.unrealized_pnl == Decimal("1600.00")
    assert state.equity == Decimal("11600.00")
    assert state.open_positions == 1


def test_paper_account_does_not_reconstruct_money_pnl_from_account_return_pct():
    state = compute_paper_account_state(
        starting_balance=Decimal("10000"),
        trades=[
            make_trade(
                status="CLOSED",
                entry=Decimal("100"),
                size=Decimal("1.00"),
                pnl_pct=Decimal("0.10"),
                pnl=None,
            )
        ],
        mark_price=Decimal("120"),
    )

    assert state.realized_pnl == Decimal("0")
    assert state.equity == Decimal("10000.00")
    assert state.closed_trades_missing_pnl == 1


def test_paper_exposure_reports_notional_and_stop_risk():
    exposure = compute_paper_exposure_state(
        starting_balance=Decimal("10000"),
        trades=[
            make_trade(
                status="OPEN",
                direction="SELL",
                entry=Decimal("100"),
                sl=Decimal("105"),
                size=Decimal("1.00"),
            ),
            make_trade(
                status="TP1_HIT",
                direction="SELL",
                entry=Decimal("200"),
                sl=Decimal("210"),
                trail=Decimal("190"),
                size=Decimal("0.50"),
            ),
        ],
    )

    assert exposure.notional_exposure == Decimal("20000.00")
    assert exposure.stop_risk == Decimal("1000.00")
    assert exposure.total_lots == Decimal("1.5000")
    assert exposure.exposure_multiple == Decimal("2.00")
    assert exposure.stop_risk_pct == Decimal("0.1000")
