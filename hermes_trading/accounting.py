"""Account-level trade accounting.

Closed trades record `pnl_pct` as a *price* move on a notional that is only a
fraction of the account, plus `fees_usd`/`net_pnl_usd` in USD. Compounding the
raw `pnl_pct` as if the whole balance were invested overstates performance and
ignores fees entirely. Every consumer (score, reflection, dashboard) must go
through these helpers so the system measures one consistent, fee-inclusive,
account-level return per trade.
"""

from __future__ import annotations


def trade_net_usd(trade: dict, balance_before: float) -> float:
    """Net USD result of a closed trade, fees included."""
    net = trade.get("net_pnl_usd")
    if net is not None:
        return float(net)
    pnl_usd = trade.get("pnl_usd")
    if pnl_usd is not None:
        return float(pnl_usd) - float(trade.get("fees_usd", 0.0))
    notional = trade.get("notional_usd")
    if notional is not None:
        return float(trade.get("pnl_pct", 0.0)) * float(notional) - float(trade.get("fees_usd", 0.0))
    # Legacy records carrying only a percentage: treat it as an account-level
    # return so historical data keeps its original meaning.
    return float(trade.get("pnl_pct", 0.0)) * balance_before


def account_returns(trades: list[dict], goal: dict) -> list[float]:
    """Per-trade returns on the running account balance, fees included."""
    balance = float(goal.get("starting_balance_usd", 10000.0))
    returns: list[float] = []
    for trade in trades:
        net = trade_net_usd(trade, balance)
        returns.append(net / balance if balance > 0 else 0.0)
        balance += net
    return returns


def compound_balance(trades: list[dict], goal: dict) -> float:
    """Account balance after replaying all closed trades."""
    balance = float(goal.get("starting_balance_usd", 10000.0))
    for trade in trades:
        balance += trade_net_usd(trade, balance)
    return balance
