"""Paper broker — the ONE place fills, positions, cash and equity are computed.

Pure logic: no file I/O, no network, no clock. `paper_engine` owns persistence
and data fetching and calls this. Keeping it pure is what makes the whole money
path unit-testable offline.

Account model (single shared account for every strategy):
  * `balance_usd` is REALIZED cash: it moves only on fees and closed-trade PnL.
  * each open `Position` is keyed by `strategy_id`, so strategies never share or
    fight over a book — one strategy can hold same-direction tranches only.
  * `equity = balance_usd + sum(unrealized PnL of open positions)`.

Sizing is fixed-fractional on risk, faithful to the validated shadow model
(`scripts/portfolio_shadow`): a position is sized so that price moving one
`atr_risk` (typically 2*ATR) against the entry equals `risk_pct` of the equity
snapshot passed in at entry. That preserves each trade's R, so per-strategy
attribution stays meaningful. This is spot-style paper with no leverage/margin
check and no cash lock-up — the account tracks a PnL stream, not settlement.

Intents map to actions by comparing against the strategy's current position:
  LONG/SHORT while flat -> open
  EXIT while holding    -> close
  anything else        -> no-op (hold / no-trade), returns None
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from math import isfinite

DEFAULT_FEE_RT = 0.001  # round-trip fee fraction, split half on entry, half on exit


@dataclass
class Position:
    strategy_id: str
    symbol: str
    side: str  # "long" or "short"
    qty: float
    entry_px: float
    notional_usd: float
    risk_pct: float
    atr_risk: float
    position_id: str = ""
    risk_distance: float = 0.0
    opened_ts: object = None
    entry_reason: str = ""
    exit_policy: str = "strategy_signal"
    monitor_timeframe: str | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    sl_basis: str = ""
    reward_risk_ratio: float | None = None
    last_monitor_candle_ts: object = None
    dynamic_exit: dict | None = None

    def unrealized_usd(self, price: float) -> float:
        direction = 1.0 if self.side == "long" else -1.0
        return direction * self.qty * (price - self.entry_px)

    @property
    def stop_risk_usd(self) -> float:
        return self.qty * self.risk_distance

    @property
    def budget_risk_usd(self) -> float:
        """Historical portfolio budget: price moving one 2×ATR unit."""
        return self.qty * self.atr_risk


@dataclass
class Account:
    balance_usd: float
    positions: dict[str, Position] = field(default_factory=dict)
    processed_candles: dict[str, object] = field(default_factory=dict)
    pending_fills: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = {
            "balance_usd": self.balance_usd,
            "positions": {},
            "processed_candles": self.processed_candles,
        }
        for sid, position in self.positions.items():
            raw = asdict(position)
            if raw.get("dynamic_exit") is None:
                raw.pop("dynamic_exit", None)
            payload["positions"][sid] = raw
        if self.pending_fills:
            payload["pending_fills"] = self.pending_fills
        return payload

    @classmethod
    def from_dict(cls, data: dict | None, *, starting_balance: float) -> "Account":
        if not isinstance(data, dict) or "balance_usd" not in data:
            return cls(balance_usd=float(starting_balance))
        position_fields = {item.name for item in fields(Position)}
        positions = {}
        for position_id, raw in (data.get("positions") or {}).items():
            if not isinstance(raw, dict):
                continue
            normalized = {
                key: value for key, value in raw.items() if key in position_fields
            }
            normalized.setdefault("position_id", position_id)
            normalized.setdefault("side", "long")
            if normalized["side"] not in ("long", "short"):
                raise ValueError(
                    f"unknown position side {normalized['side']!r} for {position_id}"
                )
            if normalized["side"] == "short" and normalized.get("dynamic_exit") is not None:
                raise ValueError(
                    f"short position cannot carry dynamic_exit state for {position_id}"
                )
            normalized.setdefault(
                "risk_distance", normalized.get("atr_risk", 0.0)
            )
            for key in (
                "qty", "entry_px", "notional_usd", "risk_pct", "atr_risk",
                "risk_distance",
            ):
                try:
                    value = float(normalized[key])
                except (KeyError, TypeError, ValueError):
                    raise ValueError(
                        f"position {position_id} has invalid {key}"
                    ) from None
                if not isfinite(value) or value <= 0:
                    raise ValueError(
                        f"position {position_id} has invalid {key}"
                    )
                normalized[key] = value
            for key in ("stop_loss_price", "take_profit_price"):
                if normalized.get(key) is None:
                    continue
                try:
                    value = float(normalized[key])
                except (TypeError, ValueError):
                    raise ValueError(
                        f"position {position_id} has invalid {key}"
                    ) from None
                if not isfinite(value):
                    raise ValueError(
                        f"position {position_id} has invalid {key}"
                    )
                normalized[key] = value
            if normalized["side"] == "short":
                entry = normalized["entry_px"]
                stop = normalized.get("stop_loss_price")
                target = normalized.get("take_profit_price")
                if stop is None or target is None:
                    raise ValueError(
                        f"short position requires a complete bracket for {position_id}"
                    )
                if not (
                    stop > entry > target > 0
                ):
                    raise ValueError(
                        f"invalid short bracket for {position_id}"
                    )
            normalized.setdefault("strategy_id", position_id.split("::t", 1)[0])
            positions[position_id] = Position(**normalized)
        sides_by_strategy: dict[str, str] = {}
        for position in positions.values():
            prior = sides_by_strategy.setdefault(position.strategy_id, position.side)
            if prior != position.side:
                raise ValueError(
                    f"mixed position sides for strategy {position.strategy_id!r}"
                )
        return cls(
            balance_usd=float(data["balance_usd"]),
            positions=positions,
            processed_candles=dict(data.get("processed_candles") or {}),
            pending_fills=[
                dict(item)
                for item in (data.get("pending_fills") or [])
                if isinstance(item, dict)
            ],
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
        self.fee_rt = float(fee_rt)
        if not isfinite(self.fee_rt) or self.fee_rt < 0:
            raise ValueError("fee_rt must be a finite non-negative number")

    def open(
        self,
        account: Account,
        *,
        strategy_id: str,
        position_id: str | None = None,
        symbol: str,
        side: str = "long",
        price: float,
        atr_risk: float,
        risk_distance: float | None = None,
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
        max_leverage: float | None = None,
        dynamic_exit: dict | None = None,
    ) -> dict | None:
        """Open one directional tranche, failing closed on malformed risk data."""
        position_key = position_id or strategy_id
        audited_stop_distance = (
            risk_distance if risk_distance is not None else atr_risk
        )
        if position_key in account.positions:
            return None  # already holding: intent is a hold, not a re-entry
        if side not in ("long", "short"):
            return None
        try:
            price = float(price)
            atr_risk = float(atr_risk)
            risk_pct = float(risk_pct)
            equity_for_sizing = float(equity_for_sizing)
            audited_stop_distance = float(audited_stop_distance)
            stop_loss_price = (
                float(stop_loss_price) if stop_loss_price is not None else None
            )
            take_profit_price = (
                float(take_profit_price) if take_profit_price is not None else None
            )
        except (TypeError, ValueError):
            return None
        if not all(
            isfinite(value)
            for value in (price, atr_risk, risk_pct, equity_for_sizing)
        ):
            return None
        if not isfinite(audited_stop_distance):
            return None
        if stop_loss_price is not None and not isfinite(stop_loss_price):
            return None
        if take_profit_price is not None and not isfinite(take_profit_price):
            return None
        if side == "short" and dynamic_exit is not None:
            return None
        if any(
            position.strategy_id == strategy_id and position.side != side
            for position in account.positions.values()
        ):
            return None
        if not (atr_risk > 0 and price > 0 and risk_pct > 0 and equity_for_sizing > 0):
            return None  # cannot size a valid position -> take no trade
        if audited_stop_distance <= 0:
            return None
        if side == "short" and (
            stop_loss_price is None
            or take_profit_price is None
            or take_profit_price <= 0
        ):
            return None
        if stop_loss_price is not None and (
            (side == "long" and stop_loss_price >= price)
            or (side == "short" and stop_loss_price <= price)
        ):
            return None
        if take_profit_price is not None and (
            (side == "long" and take_profit_price <= price)
            or (side == "short" and take_profit_price >= price)
        ):
            return None
        risk_usd = risk_pct * equity_for_sizing
        qty = risk_usd / atr_risk
        notional = qty * price
        # Notional cap: with a tight stop the risk-based size implies leverage
        # (notional > equity). The cap only ever SHRINKS qty — the SL/TP price
        # levels are untouched, so the trade's thesis survives at lower risk.
        leverage_capped = False
        if max_leverage is not None and max_leverage > 0 and notional > max_leverage * equity_for_sizing:
            qty = (max_leverage * equity_for_sizing) / price
            notional = qty * price
            leverage_capped = True
        fee = notional * self.fee_rt / 2
        account.balance_usd -= fee
        account.positions[position_key] = Position(
            strategy_id=strategy_id, symbol=symbol, side=side, qty=qty,
            entry_px=price, notional_usd=notional, risk_pct=risk_pct,
            atr_risk=atr_risk, position_id=position_key,
            risk_distance=audited_stop_distance, opened_ts=ts, entry_reason=entry_reason,
            exit_policy=exit_policy, monitor_timeframe=monitor_timeframe,
            stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
            sl_basis=sl_basis, reward_risk_ratio=reward_risk_ratio,
            dynamic_exit=dict(dynamic_exit) if dynamic_exit is not None else None,
        )
        fill = {
            "ts": ts, "strategy_id": strategy_id, "position_id": position_key,
            "symbol": symbol, "action": "open",
            "side": side, "price": price, "qty": qty, "notional_usd": notional,
            "fee_usd": fee, "risk_pct": risk_pct, "atr_risk": atr_risk,
            "risk_distance": audited_stop_distance,
            "balance_usd": account.balance_usd, "reason": entry_reason,
            "exit_policy": exit_policy, "monitor_timeframe": monitor_timeframe,
            "stop_loss_price": stop_loss_price, "take_profit_price": take_profit_price,
            "sl_basis": sl_basis, "reward_risk_ratio": reward_risk_ratio,
            "leverage_capped": leverage_capped,
        }
        if dynamic_exit is not None:
            fill["dynamic_exit"] = dict(dynamic_exit)
        return fill

    def close(
        self,
        account: Account,
        *,
        strategy_id: str,
        position_id: str | None = None,
        price: float,
        ts: object = None,
        reason: str = "",
    ) -> dict | None:
        """Close `strategy_id`'s position. No-op (None) if it holds nothing."""
        position_key = position_id or strategy_id
        pos = account.positions.get(position_key)
        if pos is None:
            return None  # nothing to exit: intent is a no-op
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None
        if not isfinite(price) or price <= 0:
            return None
        direction = 1.0 if pos.side == "long" else -1.0
        gross = direction * pos.qty * (price - pos.entry_px)
        fee = pos.qty * price * self.fee_rt / 2
        realized = gross - fee
        account.balance_usd += realized
        r = (
            direction * (price - pos.entry_px) / pos.atr_risk
            if pos.atr_risk else 0.0
        )
        del account.positions[position_key]
        fill = {
            "ts": ts, "strategy_id": pos.strategy_id,
            "position_id": pos.position_id or position_key,
            "symbol": pos.symbol, "action": "close",
            "side": pos.side, "price": price, "qty": pos.qty, "entry_px": pos.entry_px,
            "fee_usd": fee, "realized_pnl_usd": realized, "r": r,
            "balance_usd": account.balance_usd, "reason": reason,
            "exit_policy": pos.exit_policy, "monitor_timeframe": pos.monitor_timeframe,
            "stop_loss_price": pos.stop_loss_price,
            "take_profit_price": pos.take_profit_price,
            "sl_basis": pos.sl_basis,
            "reward_risk_ratio": pos.reward_risk_ratio,
            "risk_distance": pos.risk_distance,
        }
        if pos.dynamic_exit is not None:
            fill["dynamic_exit"] = pos.dynamic_exit
        return fill
