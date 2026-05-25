"""Execution price adjustments and accounting shared by backtest/live simulation."""

from dataclasses import dataclass
from decimal import Decimal


def adjust_entry(
    direction: str,
    price: Decimal,
    spread: Decimal,
    slippage: Decimal,
) -> Decimal:
    """Return the execution entry price after half-spread and slippage."""

    adjustment = spread / Decimal("2") + slippage
    return price + adjustment if direction == "BUY" else price - adjustment


def adjust_exit(
    direction: str,
    price: Decimal,
    spread: Decimal,
    slippage: Decimal,
) -> Decimal:
    """Return the execution exit price after half-spread and slippage."""

    adjustment = spread / Decimal("2") + slippage
    return price - adjustment if direction == "BUY" else price + adjustment


@dataclass(frozen=True)
class TradeAccounting:
    """Quantized account P&L for one theoretical trade close."""

    pnl_usd: Decimal
    pnl_pct: Decimal
    adjusted_entry: Decimal
    adjusted_exit: Decimal


def calculate_trade_accounting(
    *,
    direction: str,
    entry_price: Decimal,
    exit_price: Decimal,
    tp1_price: Decimal,
    size_lots: Decimal,
    equity_base: Decimal,
    contract_size: Decimal,
    spread_usd: Decimal,
    slippage_usd: Decimal,
    close_reason: str,
    original_status: str,
) -> TradeAccounting:
    """Return cost-adjusted, quantized USD and account-return P&L.

    Direct SL and pre-TP1 expiry close the full position. Other exits blend the
    realized TP1 half with the final half, matching signal-mode theoretical
    tracking.
    """
    direction_sign = Decimal("1") if direction == "BUY" else Decimal("-1")
    adjusted_entry = adjust_entry(direction, entry_price, spread_usd, slippage_usd)
    adjusted_exit = adjust_exit(direction, exit_price, spread_usd, slippage_usd)
    adjusted_tp1 = adjust_exit(direction, tp1_price, spread_usd, slippage_usd)

    full_final_usd = (
        (adjusted_exit - adjusted_entry)
        * direction_sign
        * size_lots
        * contract_size
    )
    tp1_usd = (
        (adjusted_tp1 - adjusted_entry)
        * direction_sign
        * size_lots
        * Decimal("0.5")
        * contract_size
    )
    final_half_usd = (
        (adjusted_exit - adjusted_entry)
        * direction_sign
        * size_lots
        * Decimal("0.5")
        * contract_size
    )

    normalized_reason = close_reason.upper()
    normalized_status = original_status.upper()
    pnl_usd = (
        full_final_usd
        if normalized_reason == "SL"
        or (normalized_reason == "EXPIRED" and normalized_status == "OPEN")
        else tp1_usd + final_half_usd
    ).quantize(Decimal("0.01"))

    if equity_base <= Decimal("0"):
        equity_base = Decimal("1")
    pnl_pct = (pnl_usd / equity_base).quantize(Decimal("0.00001"))

    return TradeAccounting(
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        adjusted_entry=adjusted_entry,
        adjusted_exit=adjusted_exit,
    )
