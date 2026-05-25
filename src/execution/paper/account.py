"""Internal paper account state calculations.

This module is deliberately isolated from broker execution. Demo/live broker
adapters should expose comparable account state later without depending on the
paper implementation.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Protocol

from src.market.instruments import get_instrument_spec

XAUUSD_CONTRACT_SIZE = get_instrument_spec("XAUUSD").contract_size


class PaperTradeLike(Protocol):
    """Minimal trade shape needed by paper account calculations."""

    status: str
    direction: str
    entry_price: Decimal
    sl_price: Decimal
    tp1_price: Decimal
    trailing_stop_price: Decimal | None
    size_lots: Decimal
    pnl_pct: Decimal | None
    pnl: Decimal | None


@dataclass(frozen=True)
class PaperAccountState:
    """Current internal paper account state."""

    cash_balance: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    equity: Decimal
    open_positions: int
    closed_trades_missing_pnl: int = 0


@dataclass(frozen=True)
class PaperExposureState:
    """Paper exposure and risk metrics derived from open positions."""

    notional_exposure: Decimal
    stop_risk: Decimal
    total_lots: Decimal
    exposure_multiple: Decimal
    stop_risk_pct: Decimal


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _trade_notional(trade: PaperTradeLike) -> Decimal:
    return (
        Decimal(str(trade.entry_price))
        * Decimal(str(trade.size_lots))
        * XAUUSD_CONTRACT_SIZE
    )


def _trade_stop_risk(trade: PaperTradeLike) -> Decimal:
    stop_price = (
        Decimal(str(trade.trailing_stop_price))
        if trade.status == "TP1_HIT" and trade.trailing_stop_price is not None
        else Decimal(str(trade.sl_price))
    )
    return (
        abs(Decimal(str(trade.entry_price)) - stop_price)
        * Decimal(str(trade.size_lots))
        * XAUUSD_CONTRACT_SIZE
    )


def _closed_trade_pnl(trade: PaperTradeLike) -> Decimal:
    if trade.pnl is not None:
        return Decimal(str(trade.pnl))
    return Decimal("0")


def _open_trade_unrealized(trade: PaperTradeLike, mark_price: Decimal) -> Decimal:
    entry = Decimal(str(trade.entry_price))
    size_lots = Decimal(str(trade.size_lots))
    direction_sign = Decimal("1") if trade.direction == "BUY" else Decimal("-1")

    if trade.status == "TP1_HIT":
        closed_size = size_lots * Decimal("0.5")
        open_size = size_lots * Decimal("0.5")
        tp1 = Decimal(str(trade.tp1_price))
        realized_leg = (
            (tp1 - entry) * direction_sign * closed_size * XAUUSD_CONTRACT_SIZE
        )
        open_leg = (
            (mark_price - entry) * direction_sign * open_size * XAUUSD_CONTRACT_SIZE
        )
        return realized_leg + open_leg

    return (
        (mark_price - entry)
        * direction_sign
        * size_lots
        * XAUUSD_CONTRACT_SIZE
    )


def compute_paper_account_state(
    *,
    starting_balance: Decimal,
    trades: Iterable[PaperTradeLike],
    mark_price: Decimal | None,
) -> PaperAccountState:
    """Compute account cash/equity from persisted paper trades and a mark price."""

    realized_pnl = Decimal("0")
    unrealized_pnl = Decimal("0")
    open_positions = 0
    closed_trades_missing_pnl = 0

    for trade in trades:
        if trade.status in {"CLOSED", "STOPPED"}:
            if trade.pnl is None:
                closed_trades_missing_pnl += 1
            realized_pnl += _closed_trade_pnl(trade)
        elif trade.status in {"OPEN", "TP1_HIT"}:
            open_positions += 1
            if mark_price is not None:
                unrealized_pnl += _open_trade_unrealized(trade, Decimal(str(mark_price)))

    cash_balance = Decimal(str(starting_balance)) + realized_pnl
    equity = cash_balance + unrealized_pnl

    return PaperAccountState(
        cash_balance=_money(cash_balance),
        realized_pnl=_money(realized_pnl),
        unrealized_pnl=_money(unrealized_pnl),
        equity=_money(equity),
        open_positions=open_positions,
        closed_trades_missing_pnl=closed_trades_missing_pnl,
    )


def compute_paper_exposure_state(
    *,
    starting_balance: Decimal,
    trades: Iterable[PaperTradeLike],
) -> PaperExposureState:
    """Compute notionals and stop risk from open paper positions."""

    notional = Decimal("0")
    stop_risk = Decimal("0")
    total_lots = Decimal("0")

    for trade in trades:
        if trade.status not in {"OPEN", "TP1_HIT"}:
            continue
        notional += _trade_notional(trade)
        stop_risk += _trade_stop_risk(trade)
        total_lots += Decimal(str(trade.size_lots))

    starting_balance = Decimal(str(starting_balance))
    exposure_multiple = notional / starting_balance if starting_balance > 0 else Decimal("0")
    stop_risk_pct = stop_risk / starting_balance if starting_balance > 0 else Decimal("0")

    return PaperExposureState(
        notional_exposure=_money(notional),
        stop_risk=_money(stop_risk),
        total_lots=total_lots.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        exposure_multiple=exposure_multiple.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        stop_risk_pct=stop_risk_pct.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
    )
