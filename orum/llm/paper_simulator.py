"""Pure isolated-margin paper simulation for LLM reference/evolving lanes."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from orum.llm.contracts import ProposedDecision
from orum.llm.leverage import LeverageResult
from orum.llm.paper_contracts import (
    LlmPaperAccount,
    LlmPaperFill,
    LlmPaperPosition,
    LlmPaperTarget,
)


class SimulatorError(ValueError):
    """Raised before mutation when a paper action is mechanically impossible."""


@dataclass(frozen=True, slots=True)
class SimulationResult:
    account: LlmPaperAccount
    fills: tuple[LlmPaperFill, ...]


def _id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:24]}"


def _finite_positive(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise SimulatorError(f"{name} must be positive and finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SimulatorError(f"{name} must be positive and finite") from exc
    if not math.isfinite(result) or result <= 0:
        raise SimulatorError(f"{name} must be positive and finite")
    return result


class LlmPaperSimulator:
    """Deterministic leveraged simulator; contains no persistence or network I/O."""

    def __init__(
        self,
        *,
        fee_rate: float = 0.0005,
        maintenance_margin_rate: float = 0.005,
        allow_stop_beyond_liquidation: bool = False,
    ) -> None:
        if not math.isfinite(fee_rate) or fee_rate < 0:
            raise SimulatorError("fee_rate must be finite and non-negative")
        if not math.isfinite(maintenance_margin_rate) or not 0 <= maintenance_margin_rate < 1:
            raise SimulatorError("maintenance_margin_rate must be in [0, 1)")
        self.fee_rate = float(fee_rate)
        self.maintenance_margin_rate = float(maintenance_margin_rate)
        self.allow_stop_beyond_liquidation = bool(allow_stop_beyond_liquidation)

    def apply_decision(
        self,
        account: LlmPaperAccount,
        decision: ProposedDecision,
        leverage: LeverageResult | None,
        *,
        price: float,
        candle_ts: int,
    ) -> SimulationResult:
        execution_price = _finite_positive(price, "price")
        if decision.lane != account.lane:
            raise SimulatorError("decision lane does not match paper account")
        if decision.decision_id in account.processed_decision_ids:
            raise SimulatorError(f"decision {decision.decision_id} was already processed")
        processed = account.processed_decision_ids + (decision.decision_id,)

        if decision.action == "hold":
            return SimulationResult(replace(account, processed_decision_ids=processed), ())
        if decision.action in {"open_long", "open_short"}:
            return self._open(
                account,
                decision,
                leverage,
                execution_price,
                int(candle_ts),
                processed,
            )
        if decision.action == "add":
            return self._add(
                account,
                decision,
                leverage,
                execution_price,
                int(candle_ts),
                processed,
            )
        if decision.action == "reduce":
            position = account.positions.get(decision.symbol)
            if position is None:
                raise SimulatorError("cannot reduce a missing position")
            if not 0 < decision.equity_fraction < 1:
                raise SimulatorError("reduce requires equity_fraction between 0 and 1")
            updated, fill = self._close_quantity(
                account,
                position,
                qty=position.qty * decision.equity_fraction,
                price=execution_price,
                action="reduce",
                reason="llm_decision",
                decision_id=decision.decision_id,
                candle_ts=int(candle_ts),
                created_at=decision.created_at,
                remaining_targets=position.take_profits,
            )
            return SimulationResult(replace(updated, processed_decision_ids=processed), (fill,))
        if decision.action == "close":
            position = account.positions.get(decision.symbol)
            if position is None:
                raise SimulatorError("cannot close a missing position")
            updated, fill = self._close_quantity(
                account,
                position,
                qty=position.qty,
                price=execution_price,
                action="close",
                reason="llm_decision",
                decision_id=decision.decision_id,
                candle_ts=int(candle_ts),
                created_at=decision.created_at,
                remaining_targets=(),
            )
            return SimulationResult(replace(updated, processed_decision_ids=processed), (fill,))
        raise SimulatorError(f"paper action {decision.action!r} is not installed yet")

    def _add(
        self,
        account: LlmPaperAccount,
        decision: ProposedDecision,
        leverage: LeverageResult | None,
        price: float,
        candle_ts: int,
        processed: tuple[str, ...],
    ) -> SimulationResult:
        current = account.positions.get(decision.symbol)
        if current is None:
            raise SimulatorError("cannot add to a missing position")
        if leverage is None:
            raise SimulatorError("add requires an effective leverage result")
        allocated = account.equity_usd * decision.equity_fraction
        other_margin = math.fsum(
            item.initial_margin_usd
            for item in account.positions.values()
            if item.position_id != current.position_id
        )
        if allocated <= 0 or other_margin + current.initial_margin_usd + allocated > account.equity_usd + 1e-9:
            raise SimulatorError("insufficient isolated paper collateral")
        added_notional = allocated * leverage.paper_effective
        added_qty = added_notional / price
        fee = added_notional * self.fee_rate
        if fee > account.balance_usd:
            raise SimulatorError("add fee exceeds realized paper balance")
        total_qty = current.qty + added_qty
        total_initial_qty = current.initial_qty + added_qty
        combined_entry = (current.qty * current.entry_px + added_qty * price) / total_qty
        total_margin = current.initial_margin_usd + allocated
        total_notional = current.qty * current.entry_px + added_notional
        effective_leverage = total_notional / total_margin
        liquidation = self._liquidation_price(combined_entry, effective_leverage, current.side)
        assert decision.stop_loss is not None
        if not self.allow_stop_beyond_liquidation:
            if current.side == "long" and decision.stop_loss <= liquidation:
                raise SimulatorError("long stop is at or beyond estimated liquidation")
            if current.side == "short" and decision.stop_loss >= liquidation:
                raise SimulatorError("short stop is at or beyond estimated liquidation")
        targets = tuple(
            LlmPaperTarget(
                target_id=_id("tp", decision.decision_id, index),
                price=target.price,
                fraction_remaining=target.fraction,
            )
            for index, target in enumerate(decision.take_profits, start=1)
        )
        combined = replace(
            current,
            qty=total_qty,
            initial_qty=total_initial_qty,
            entry_px=combined_entry,
            mark_px=price,
            notional_usd=total_notional,
            initial_margin_usd=total_margin,
            requested_leverage=decision.requested_leverage,
            effective_leverage=effective_leverage,
            entry_fee_usd=current.entry_fee_usd + fee,
            liquidation_px=liquidation,
            stop_loss=decision.stop_loss,
            take_profits=targets,
            trailing_stop_pct=decision.trailing_stop_pct,
            thesis=decision.thesis,
            invalidation=decision.invalidation,
        )
        balance = account.balance_usd - fee
        fill = self._fill(
            position=combined,
            decision_id=decision.decision_id,
            action="add",
            reason="llm_decision",
            qty=added_qty,
            price=price,
            fee=fee,
            realized=0,
            balance_after=balance,
            candle_ts=candle_ts,
            created_at=decision.created_at,
        )
        return SimulationResult(
            replace(
                account,
                balance_usd=balance,
                positions={**account.positions, decision.symbol: combined},
                processed_decision_ids=processed,
            ),
            (fill,),
        )

    def _open(
        self,
        account: LlmPaperAccount,
        decision: ProposedDecision,
        leverage: LeverageResult | None,
        price: float,
        candle_ts: int,
        processed: tuple[str, ...],
    ) -> SimulationResult:
        if leverage is None:
            raise SimulatorError("entry requires an effective leverage result")
        if decision.symbol in account.positions:
            raise SimulatorError("cannot open a second position for the same symbol")
        equity = account.equity_usd
        allocated = equity * decision.equity_fraction
        existing_margin = math.fsum(item.initial_margin_usd for item in account.positions.values())
        if allocated <= 0 or existing_margin + allocated > equity + 1e-9:
            raise SimulatorError("insufficient isolated paper collateral")
        notional = allocated * leverage.paper_effective
        qty = notional / price
        fee = notional * self.fee_rate
        if fee > account.balance_usd:
            raise SimulatorError("entry fee exceeds realized paper balance")
        side = "long" if decision.action == "open_long" else "short"
        liquidation = self._liquidation_price(price, leverage.paper_effective, side)
        assert decision.stop_loss is not None
        if not self.allow_stop_beyond_liquidation:
            if side == "long" and decision.stop_loss <= liquidation:
                raise SimulatorError("long stop is at or beyond estimated liquidation")
            if side == "short" and decision.stop_loss >= liquidation:
                raise SimulatorError("short stop is at or beyond estimated liquidation")
        position_id = _id("pos", account.lane, decision.decision_id)
        targets = tuple(
            LlmPaperTarget(
                target_id=_id("tp", decision.decision_id, index),
                price=target.price,
                fraction_remaining=target.fraction,
            )
            for index, target in enumerate(decision.take_profits, start=1)
        )
        time_exit_at = (
            None
            if decision.time_exit_minutes is None
            else decision.created_at + timedelta(minutes=decision.time_exit_minutes)
        )
        position = LlmPaperPosition(
            position_id=position_id,
            decision_id=decision.decision_id,
            lane=account.lane,
            symbol=decision.symbol,
            side=side,
            qty=qty,
            initial_qty=qty,
            entry_px=price,
            mark_px=price,
            notional_usd=notional,
            initial_margin_usd=allocated,
            requested_leverage=leverage.requested,
            effective_leverage=leverage.paper_effective,
            entry_fee_usd=fee,
            liquidation_px=liquidation,
            liquidation_formula_version="isolated_v1",
            stop_loss=decision.stop_loss,
            take_profits=targets,
            trailing_stop_pct=decision.trailing_stop_pct,
            time_exit_at=time_exit_at,
            opened_at=decision.created_at,
            thesis=decision.thesis,
            invalidation=decision.invalidation,
        )
        balance = account.balance_usd - fee
        fill = self._fill(
            position=position,
            decision_id=decision.decision_id,
            action="open",
            reason="llm_decision",
            qty=qty,
            price=price,
            fee=fee,
            realized=0,
            balance_after=balance,
            candle_ts=candle_ts,
            created_at=decision.created_at,
        )
        return SimulationResult(
            replace(
                account,
                balance_usd=balance,
                positions={**account.positions, decision.symbol: position},
                processed_decision_ids=processed,
            ),
            (fill,),
        )

    def monitor_candle(self, account: LlmPaperAccount, candle: dict[str, Any]) -> SimulationResult:
        try:
            candle_ts = int(candle["ts"])
            open_price = _finite_positive(candle["open"], "candle open")
            high = _finite_positive(candle["high"], "candle high")
            low = _finite_positive(candle["low"], "candle low")
            close = _finite_positive(candle["close"], "candle close")
        except (KeyError, TypeError, ValueError) as exc:
            raise SimulatorError("candle must contain valid ts/OHLC") from exc
        if low > min(open_price, close) or high < max(open_price, close) or low > high:
            raise SimulatorError("invalid candle geometry")
        current = account
        fills: list[LlmPaperFill] = []
        processed_candles = dict(account.last_processed_candles)
        for symbol, original in tuple(account.positions.items()):
            if processed_candles.get(original.position_id, -1) >= candle_ts:
                continue
            position = replace(original, mark_px=close)
            adverse_action, adverse_price = self._adverse_event(position, high=high, low=low)
            created_at = datetime.fromtimestamp(candle_ts / 1000, tz=UTC)
            if adverse_action is not None:
                current, fill = self._close_quantity(
                    current,
                    position,
                    qty=position.qty,
                    price=adverse_price,
                    action=adverse_action,
                    reason=f"paper_{adverse_action}",
                    decision_id=position.decision_id,
                    candle_ts=candle_ts,
                    created_at=created_at,
                    remaining_targets=(),
                )
                fills.append(fill)
            else:
                current = replace(current, positions={**current.positions, symbol: position})
                ordered_targets = sorted(
                    position.take_profits,
                    key=lambda item: item.price,
                    reverse=position.side == "short",
                )
                for target in ordered_targets:
                    active = current.positions.get(symbol)
                    if active is None or not self._target_touched(active.side, target.price, high, low):
                        continue
                    close_qty = min(active.qty, active.initial_qty * target.fraction_remaining)
                    remaining_targets = tuple(
                        item for item in active.take_profits if item.target_id != target.target_id
                    )
                    current, fill = self._close_quantity(
                        current,
                        active,
                        qty=close_qty,
                        price=target.price,
                        action="take_profit",
                        reason=target.target_id,
                        decision_id=active.decision_id,
                        candle_ts=candle_ts,
                        created_at=created_at,
                        remaining_targets=remaining_targets,
                    )
                    fills.append(fill)
                active = current.positions.get(symbol)
                if active is not None and active.time_exit_at is not None and created_at >= active.time_exit_at:
                    current, fill = self._close_quantity(
                        current,
                        active,
                        qty=active.qty,
                        price=close,
                        action="time_exit",
                        reason="configured_time_exit",
                        decision_id=active.decision_id,
                        candle_ts=candle_ts,
                        created_at=created_at,
                        remaining_targets=(),
                    )
                    fills.append(fill)
            processed_candles[original.position_id] = candle_ts
        current = replace(current, last_processed_candles=processed_candles)
        return SimulationResult(current, tuple(fills))

    def _adverse_event(self, position: LlmPaperPosition, *, high: float, low: float) -> tuple[str | None, float]:
        if position.side == "long":
            if low <= position.liquidation_px:
                return "liquidation", position.liquidation_px
            if position.stop_loss is not None and low <= position.stop_loss:
                return "stop", position.stop_loss
        else:
            if high >= position.liquidation_px:
                return "liquidation", position.liquidation_px
            if position.stop_loss is not None and high >= position.stop_loss:
                return "stop", position.stop_loss
        return None, position.mark_px

    @staticmethod
    def _target_touched(side: str, price: float, high: float, low: float) -> bool:
        return high >= price if side == "long" else low <= price

    def _close_quantity(
        self,
        account: LlmPaperAccount,
        position: LlmPaperPosition,
        *,
        qty: float,
        price: float,
        action: str,
        reason: str,
        decision_id: str,
        candle_ts: int,
        created_at: datetime,
        remaining_targets: tuple[LlmPaperTarget, ...],
    ) -> tuple[LlmPaperAccount, LlmPaperFill]:
        direction = 1 if position.side == "long" else -1
        gross = direction * qty * (price - position.entry_px)
        fee = qty * price * self.fee_rate
        requested_realized = gross - fee
        balance_after = max(0.0, account.balance_usd + requested_realized)
        realized = balance_after - account.balance_usd
        remaining_qty = max(0.0, position.qty - qty)
        positions = dict(account.positions)
        if remaining_qty <= position.initial_qty * 1e-12:
            positions.pop(position.symbol, None)
        else:
            positions[position.symbol] = replace(
                position,
                qty=remaining_qty,
                mark_px=price,
                notional_usd=remaining_qty * position.entry_px,
                initial_margin_usd=(remaining_qty * position.entry_px)
                / position.effective_leverage,
                take_profits=remaining_targets,
            )
        updated = replace(account, balance_usd=balance_after, positions=positions)
        fill = self._fill(
            position=position,
            decision_id=decision_id,
            action=action,
            reason=reason,
            qty=qty,
            price=price,
            fee=fee,
            realized=realized,
            balance_after=balance_after,
            candle_ts=candle_ts,
            created_at=created_at,
        )
        return updated, fill

    def _fill(
        self,
        *,
        position: LlmPaperPosition,
        decision_id: str,
        action: str,
        reason: str,
        qty: float,
        price: float,
        fee: float,
        realized: float,
        balance_after: float,
        candle_ts: int,
        created_at: datetime,
    ) -> LlmPaperFill:
        operation_id = _id("op", position.lane, decision_id, action, candle_ts, reason)
        return LlmPaperFill(
            fill_id=_id("fill", operation_id),
            operation_id=operation_id,
            decision_id=decision_id,
            position_id=position.position_id,
            lane=position.lane,
            symbol=position.symbol,
            action=action,
            reason=reason,
            side=position.side,
            qty=qty,
            price=price,
            fee_usd=fee,
            realized_pnl_usd=realized,
            balance_after_usd=balance_after,
            candle_ts=candle_ts,
            created_at=created_at,
        )

    def _liquidation_price(self, entry: float, leverage: float, side: str) -> float:
        if side == "long":
            return entry * (1 - 1 / leverage + self.maintenance_margin_rate)
        return entry * (1 + 1 / leverage - self.maintenance_margin_rate)
