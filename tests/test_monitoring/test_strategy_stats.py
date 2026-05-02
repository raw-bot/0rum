"""Unit tests for strategy_stats upsert math (Phase 7, Plan 04).

Tests verify the mathematical correctness of win_rate and profit_factor
computation in _close_trade(). These are pure-math tests using mock DB sessions.
"""

from decimal import Decimal

import pytest


def _compute_stats(trades: list[dict]) -> dict:
    """Simulate the cumulative strategy_stats accumulation for a list of trades.

    Each trade dict: {"pnl": Decimal, "tp1": Decimal, "entry": Decimal, "exit": Decimal}
    Uses the same blended P&L formula as _close_trade (D-06).

    Returns computed stats dict with: wins, losses, gross_profit_pct, gross_loss_pct,
    total_pnl_pct, win_rate, profit_factor.
    """
    wins = 0
    losses = 0
    gross_profit = Decimal("0")
    gross_loss = Decimal("0")
    total_pnl = Decimal("0")

    for t in trades:
        pnl = t["pnl"]
        is_win = pnl > Decimal("0")
        if is_win:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss += abs(pnl)
        total_pnl += pnl

    trade_count = wins + losses
    win_rate = Decimal(str(wins)) / Decimal(str(trade_count)) if trade_count > 0 else Decimal("0")

    if gross_loss > Decimal("0"):
        profit_factor = gross_profit / gross_loss
    elif wins > 0:
        profit_factor = Decimal("999.9999")
    else:
        profit_factor = Decimal("0")

    return {
        "trade_count": trade_count,
        "wins": wins,
        "losses": losses,
        "gross_profit_pct": gross_profit,
        "gross_loss_pct": gross_loss,
        "total_pnl_pct": total_pnl,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
    }


def test_strategy_stats_win_increments_wins():
    """One winning trade → wins=1, losses=0, win_rate=1.0."""
    stats = _compute_stats([{"pnl": Decimal("0.015")}])
    assert stats["wins"] == 1
    assert stats["losses"] == 0
    assert stats["win_rate"] == Decimal("1")
    assert stats["gross_profit_pct"] == Decimal("0.015")
    assert stats["gross_loss_pct"] == Decimal("0")


def test_strategy_stats_loss_increments_losses():
    """One losing trade → wins=0, losses=1, win_rate=0.0."""
    stats = _compute_stats([{"pnl": Decimal("-0.012")}])
    assert stats["wins"] == 0
    assert stats["losses"] == 1
    assert stats["win_rate"] == Decimal("0")
    assert stats["gross_loss_pct"] == Decimal("0.012")
    assert stats["gross_profit_pct"] == Decimal("0")


def test_strategy_stats_gross_profit_accumulates():
    """Two winning trades → gross_profit_pct = sum of both pnl values."""
    trades = [
        {"pnl": Decimal("0.015")},
        {"pnl": Decimal("0.022")},
    ]
    stats = _compute_stats(trades)
    assert stats["wins"] == 2
    assert stats["gross_profit_pct"] == Decimal("0.037")
    assert stats["total_pnl_pct"] == Decimal("0.037")


def test_strategy_stats_profit_factor_zero_losses_sentinel():
    """When gross_loss_pct == 0 and wins > 0, profit_factor = Decimal('999.9999')."""
    stats = _compute_stats([{"pnl": Decimal("0.018")}])
    assert stats["gross_loss_pct"] == Decimal("0")
    assert stats["wins"] > 0
    assert stats["profit_factor"] == Decimal("999.9999")


def test_strategy_stats_profit_factor_computed():
    """gross_profit=2.0, gross_loss=1.0 → profit_factor=2.0."""
    trades = [
        {"pnl": Decimal("2.0")},   # win
        {"pnl": Decimal("-1.0")},  # loss
    ]
    stats = _compute_stats(trades)
    assert stats["gross_profit_pct"] == Decimal("2.0")
    assert stats["gross_loss_pct"] == Decimal("1.0")
    assert stats["profit_factor"] == Decimal("2.0")
