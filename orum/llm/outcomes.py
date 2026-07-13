"""Deterministic point-in-time outcome metrics for LLM trade proposals."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    outcome_id: str
    decision_id: str
    lane: str
    symbol: str
    side: str
    evaluated_at: datetime
    exit_reason: str
    exit_price: float
    exit_candle_ts: int
    gross_return_on_margin: float
    net_return_on_margin: float
    account_return: float
    opposite_net_return_on_margin: float
    hold_return_on_margin: float
    mfe_pct: float
    mae_pct: float
    confidence: float
    calibration_squared_error: float

    def to_mapping(self) -> dict[str, object]:
        return {
            "outcome_id": self.outcome_id, "decision_id": self.decision_id,
            "lane": self.lane, "symbol": self.symbol, "side": self.side,
            "evaluated_at": self.evaluated_at.isoformat(),
            "exit_reason": self.exit_reason, "exit_price": self.exit_price,
            "exit_candle_ts": self.exit_candle_ts,
            "gross_return_on_margin": self.gross_return_on_margin,
            "net_return_on_margin": self.net_return_on_margin,
            "account_return": self.account_return,
            "opposite_net_return_on_margin": self.opposite_net_return_on_margin,
            "hold_return_on_margin": self.hold_return_on_margin,
            "mfe_pct": self.mfe_pct, "mae_pct": self.mae_pct,
            "confidence": self.confidence,
            "calibration_squared_error": self.calibration_squared_error,
        }


class OutcomeEvaluator:
    def __init__(self, *, fee_rate: float = 0.0005) -> None:
        self.fee_rate = float(fee_rate)

    def evaluate(
        self,
        *,
        decision_id: str,
        lane: str,
        symbol: str,
        side: str,
        entry_price: float,
        leverage: float,
        equity_fraction: float,
        confidence: float,
        stop_loss: float | None,
        take_profit: float | None,
        liquidation_price: float,
        candles: list[dict],
        evaluated_at: datetime,
    ) -> DecisionOutcome:
        if not candles:
            raise ValueError("outcome requires at least one closed candle")
        if side not in {"long", "short"}:
            raise ValueError("outcome side must be long or short")
        if not math.isfinite(entry_price) or entry_price <= 0:
            raise ValueError("entry price must be positive and finite")
        if not math.isfinite(leverage) or leverage <= 0:
            raise ValueError("leverage must be positive and finite")
        if not math.isfinite(equity_fraction) or not 0 < equity_fraction <= 1:
            raise ValueError("equity fraction must be in (0, 1]")
        direction = 1 if side == "long" else -1
        favorable: list[float] = []
        adverse: list[float] = []
        exit_reason = "horizon"
        exit_price = float(candles[-1]["close"])
        exit_ts = int(candles[-1]["ts"])
        for candle in candles:
            high, low = float(candle["high"]), float(candle["low"])
            if side == "long":
                favorable.append((high / entry_price - 1) * 100)
                adverse.append((low / entry_price - 1) * 100)
                events = (
                    (low <= liquidation_price, "liquidation", liquidation_price),
                    (stop_loss is not None and low <= stop_loss, "stop", stop_loss),
                    (take_profit is not None and high >= take_profit, "take_profit", take_profit),
                )
            else:
                favorable.append((1 - low / entry_price) * 100)
                adverse.append((1 - high / entry_price) * 100)
                events = (
                    (high >= liquidation_price, "liquidation", liquidation_price),
                    (stop_loss is not None and high >= stop_loss, "stop", stop_loss),
                    (take_profit is not None and low <= take_profit, "take_profit", take_profit),
                )
            event = next(((reason, price) for hit, reason, price in events if hit), None)
            if event is not None:
                exit_reason, exit_price = event
                exit_ts = int(candle["ts"])
                break
        raw_move = direction * (exit_price / entry_price - 1)
        gross = raw_move * leverage
        fee_on_margin = self.fee_rate * leverage * (1 + exit_price / entry_price)
        net = gross - fee_on_margin
        opposite = -raw_move * leverage - fee_on_margin
        account_return = net * equity_fraction
        won = 1.0 if net > 0 else 0.0
        digest = hashlib.sha256(
            f"{decision_id}|{exit_ts}|{exit_reason}".encode("utf-8")
        ).hexdigest()
        values = (gross, net, opposite, max(favorable), min(adverse))
        if not all(math.isfinite(item) for item in values):
            raise ValueError("outcome metrics must be finite")
        return DecisionOutcome(
            outcome_id=f"out-{digest[:24]}", decision_id=decision_id, lane=lane,
            symbol=symbol, side=side, evaluated_at=evaluated_at,
            exit_reason=exit_reason, exit_price=exit_price, exit_candle_ts=exit_ts,
            gross_return_on_margin=gross, net_return_on_margin=net,
            account_return=account_return,
            opposite_net_return_on_margin=opposite, hold_return_on_margin=0.0,
            mfe_pct=max(favorable), mae_pct=min(adverse), confidence=confidence,
            calibration_squared_error=(confidence - won) ** 2,
        )

    def evaluate_fills(
        self,
        *,
        decision_id: str,
        lane: str,
        symbol: str,
        side: str,
        confidence: float,
        equity_fraction: float,
        fills: list[dict],
        candles: list[dict],
        evaluated_at: datetime,
    ) -> DecisionOutcome:
        entries = [row for row in fills if row.get("action") in {"open", "add"}]
        exits = [
            row for row in fills
            if row.get("action") in {"reduce", "close", "stop", "liquidation", "time_exit", "take_profit"}
        ]
        if not entries or not exits:
            raise ValueError("fill outcome requires entry and exit fills")
        opening, closing = entries[0], exits[-1]
        starting_balance = float(opening["balance_after_usd"]) + float(opening.get("fee_usd", 0))
        ending_balance = float(closing["balance_after_usd"])
        if not math.isfinite(starting_balance) or starting_balance <= 0:
            raise ValueError("fill outcome starting balance must be positive and finite")
        net_pnl = ending_balance - starting_balance
        account_return = net_pnl / starting_balance
        allocated = starting_balance * equity_fraction
        net_margin = net_pnl / allocated
        total_fees = math.fsum(float(row.get("fee_usd", 0)) for row in fills)
        gross_margin = (net_pnl + total_fees) / allocated
        entry_price = float(opening["price"])
        direction = 1 if side == "long" else -1
        favorable = []
        adverse = []
        for candle in candles:
            high, low = float(candle["high"]), float(candle["low"])
            if side == "long":
                favorable.append((high / entry_price - 1) * 100)
                adverse.append((low / entry_price - 1) * 100)
            else:
                favorable.append((1 - low / entry_price) * 100)
                adverse.append((1 - high / entry_price) * 100)
        won = 1.0 if account_return > 0 else 0.0
        exit_ts = int(closing["candle_ts"])
        exit_reason = str(closing.get("action", "close"))
        digest = hashlib.sha256(
            f"{decision_id}|{exit_ts}|{closing.get('fill_id', exit_reason)}".encode("utf-8")
        ).hexdigest()
        return DecisionOutcome(
            outcome_id=f"out-{digest[:24]}", decision_id=decision_id, lane=lane,
            symbol=symbol, side=side, evaluated_at=evaluated_at,
            exit_reason=exit_reason, exit_price=float(closing["price"]),
            exit_candle_ts=exit_ts, gross_return_on_margin=gross_margin,
            net_return_on_margin=net_margin, account_return=account_return,
            opposite_net_return_on_margin=-gross_margin,
            hold_return_on_margin=0.0,
            mfe_pct=max(favorable, default=0.0), mae_pct=min(adverse, default=0.0),
            confidence=confidence,
            calibration_squared_error=(confidence - won) ** 2,
        )
