"""Paper broker — the ONE place fills, positions, cash and equity are computed.

Pure logic: no file I/O, no network, no clock. `paper_engine` owns persistence
and data fetching and calls this. Keeping it pure is what makes the whole money
path unit-testable offline.

Account model (single shared account for every strategy):
  * `balance_usd` is REALIZED cash: it moves only on fees and closed-trade PnL.
  * each open `Position` is keyed by `strategy_id`, so strategies never share or
    fight over a book — one strategy holds at most one position at a time.
  * `equity = balance_usd + sum(unrealized PnL of open positions)`.

Sizing is fixed-fractional on risk, faithful to the validated shadow model
(`scripts/portfolio_shadow`): a position is sized so that price moving one
`atr_risk` (typically 2*ATR) against the entry equals `risk_pct` of the equity
snapshot passed in at entry. That preserves each trade's R, so per-strategy
attribution stays meaningful. This is spot-style paper with no leverage/margin
check and no cash lock-up — the account tracks a PnL stream, not settlement.

Intents map to actions by comparing against the strategy's current position:
  LONG  while flat    -> open
  EXIT  while holding  -> close
  anything else        -> no-op (hold / no-trade), returns None
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

DEFAULT_FEE_RT = 0.001  # round-trip fee fraction, split half on entry, half on exit


@dataclass
class Position:
    strategy_id: str
    symbol: str
    side: str  # "long" (portfolio is long-only for now)
    qty: float
    entry_px: float
    notional_usd: float
    risk_pct: float
    atr_risk: float
    opened_ts: object = None
    entry_reason: str = ""
    exit_policy: str = "strategy_signal"
    monitor_timeframe: str | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    sl_basis: str = ""
    reward_risk_ratio: float | None = None
    last_monitor_candle_ts: object = None

    def unrealized_usd(self, price: float) -> float:
        return self.qty * (price - self.entry_px)  # long-only


@dataclass
class Account:
    balance_usd: float
    positions: dict[str, Position] = field(default_factory=dict)
    processed_candles: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "balance_usd": self.balance_usd,
            "positions": {sid: asdict(p) for sid, p in self.positions.items()},
            "processed_candles": self.processed_candles,
        }

    @classmethod
    def from_dict(cls, data: dict | None, *, starting_balance: float) -> "Account":
        if not isinstance(data, dict) or "balance_usd" not in data:
            return cls(balance_usd=float(starting_balance))
        position_fields = {item.name for item in fields(Position)}
        positions = {
            sid: Position(**{key: value for key, value in p.items() if key in position_fields})
            for sid, p in (data.get("positions") or {}).items()
            if isinstance(p, dict)
        }
        return cls(
            balance_usd=float(data["balance_usd"]),
            positions=positions,
            processed_candles=dict(data.get("processed_candles") or {}),
        )

    def equity(self, prices: dict[str, float]) -> float:
        """Mark-to-market equity. Positions whose symbol has no price this cycle
        are held at cost (0 unrealized) rather than dropped."""
        unreal = 0.0
        for pos in self.positions.values():
            px = prices.get(pos.symbol)
            if px is not None:
                unreal += pos.unrealized_usd(px)
        return self.balance_usd + unreal


class PaperBroker:
    def __init__(self, fee_rt: float = DEFAULT_FEE_RT) -> None:
        self.fee_rt = fee_rt

    def open(
        self,
        account: Account,
        *,
        strategy_id: str,
        symbol: str,
        price: float,
        atr_risk: float,
        risk_pct: float,
        equity_for_sizing: float,
        ts: object = None,
        entry_reason: str = "",
        exit_policy: str = "strategy_signal",
        monitor_timeframe: str | None = None,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
        sl_basis: str = "",
        reward_risk_ratio: float | None = None,
    ) -> dict | None:
        """Open a long for `strategy_id`. No-op (None) if it already holds, if
        the risk basis is unusable, or if inputs are non-positive."""
        if strategy_id in account.positions:
            return None  # already holding: intent is a hold, not a re-entry
        if not (atr_risk > 0 and price > 0 and risk_pct > 0 and equity_for_sizing > 0):
            return None  # cannot size a valid position -> take no trade
        risk_usd = risk_pct * equity_for_sizing
        qty = risk_usd / atr_risk
        notional = qty * price
        fee = notional * self.fee_rt / 2
        account.balance_usd -= fee
        account.positions[strategy_id] = Position(
            strategy_id=strategy_id, symbol=symbol, side="long", qty=qty,
            entry_px=price, notional_usd=notional, risk_pct=risk_pct,
            atr_risk=atr_risk, opened_ts=ts, entry_reason=entry_reason,
            exit_policy=exit_policy, monitor_timeframe=monitor_timeframe,
            stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
            sl_basis=sl_basis, reward_risk_ratio=reward_risk_ratio,
        )
        return {
            "ts": ts, "strategy_id": strategy_id, "symbol": symbol, "action": "open",
            "side": "long", "price": price, "qty": qty, "notional_usd": notional,
            "fee_usd": fee, "risk_pct": risk_pct, "atr_risk": atr_risk,
            "balance_usd": account.balance_usd, "reason": entry_reason,
            "exit_policy": exit_policy, "monitor_timeframe": monitor_timeframe,
            "stop_loss_price": stop_loss_price, "take_profit_price": take_profit_price,
            "sl_basis": sl_basis, "reward_risk_ratio": reward_risk_ratio,
        }

    def close(
        self,
        account: Account,
        *,
        strategy_id: str,
        price: float,
        ts: object = None,
        reason: str = "",
    ) -> dict | None:
        """Close `strategy_id`'s position. No-op (None) if it holds nothing."""
        pos = account.positions.get(strategy_id)
        if pos is None:
            return None  # nothing to exit: intent is a no-op
        gross = pos.qty * (price - pos.entry_px)
        fee = pos.qty * price * self.fee_rt / 2
        realized = gross - fee
        account.balance_usd += realized
        r = (price - pos.entry_px) / pos.atr_risk if pos.atr_risk else 0.0
        del account.positions[strategy_id]
        return {
            "ts": ts, "strategy_id": strategy_id, "symbol": pos.symbol, "action": "close",
            "side": "long", "price": price, "qty": pos.qty, "entry_px": pos.entry_px,
            "fee_usd": fee, "realized_pnl_usd": realized, "r": r,
            "balance_usd": account.balance_usd, "reason": reason,
            "exit_policy": pos.exit_policy, "monitor_timeframe": pos.monitor_timeframe,
            "stop_loss_price": pos.stop_loss_price,
            "take_profit_price": pos.take_profit_price,
            "sl_basis": pos.sl_basis,
            "reward_risk_ratio": pos.reward_risk_ratio,
        }
