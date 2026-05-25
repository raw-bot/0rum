"""Phase 6 risk-module DTOs — distinct from ORM layer (per CONTEXT D-01).

These Pydantic v2 data transfer objects cross phase boundaries:
- PositionSizing is returned by the ATR sizer (Plan 04 / RISK-04).
- RiskDecision is returned by RiskGateRunner.evaluate (Plan 07).
- CircuitBreakerAlert is emitted when the circuit breaker trips (Plan 06 / CONTEXT D-13).
  Startup code may register alert adapters against the hook surface in src/risk/hooks.py;
  each adapter receives CircuitBreakerAlert as its sole argument.

Intentional deviation from src/models/signal_data.py: ALL three DTOs here are
frozen (ConfigDict with frozen=True). signal_data.py omits that setting because
those DTOs are mutated in-process within the pipeline. Risk DTOs are immutable
because they cross the Phase 6 → Phase 7 boundary (T-06-02-02).
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PositionSizing(BaseModel):
    """Output of the ATR-based position sizer (RISK-04).

    Per CONTEXT D-07, size is computed in order: vol → cap → concentration.
    risk_pct is float (not Decimal): values are in the range 0.001–0.02 and
    IEEE 754 precision is acceptable for percentage arithmetic.
    risk_amount_usd and size_lots are Decimal to match TradeORM.pnl_pct
    (src/models/trade.py:41) — money math must be exact.

    Attributes:
        risk_pct: Final risk percentage after vol bump and concentration cap (≥0.0).
        risk_amount_usd: Dollar risk for this trade (equity × risk_pct).
        size_lots: Calculated position size in lots.
        vol_factor: ATR volatility multiplier applied to base risk_per_trade.
        concentration_reduced: True when concentration cap halved risk_pct.
    """

    model_config = ConfigDict(frozen=True)

    risk_pct: float = Field(ge=0.0)
    risk_amount_usd: Decimal
    size_lots: Decimal
    vol_factor: float
    concentration_reduced: bool


class RiskDecision(BaseModel):
    """Result of running all risk gates against one candidate signal.

    passed=False when any gate rejects or the circuit breaker is tripped (D-12).
    When passed=True, sizing is always populated. When passed=False, reason names
    the rejecting gate ('daily_loss_limit', 'max_positions', 'breaker_tripped', etc.)
    and sizing is None.

    Attributes:
        passed: Whether the candidate cleared all risk gates.
        reason: Gate name or rejection cause; None when passed=True.
        sizing: Position sizing output; None when passed=False.
        concentration_reduced: Propagated from PositionSizing for upstream logging.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: Optional[str] = None
    sizing: Optional[PositionSizing] = None
    concentration_reduced: bool = False
    equity_usd: Optional[Decimal] = None
    notional_after_usd: Optional[Decimal] = None
    stop_risk_after_usd: Optional[Decimal] = None
    exposure_multiple_after: Optional[Decimal] = None
    candidate_notional_usd: Optional[Decimal] = None


class CircuitBreakerAlert(BaseModel):
    """Event emitted when the circuit breaker trips (CONTEXT D-13).

    Startup code may register alert adapters against src/risk/hooks._alert_hooks.
    Each adapter receives this DTO as its sole argument. The DTO is frozen
    (T-06-02-02) so no hook can mutate it.

    Attributes:
        tripped_at: UTC timestamp when the breaker tripped.
        consecutive_stops: Number of consecutive stop-outs that triggered the trip.
        cooldown_until: UTC timestamp when the breaker resets automatically.
        last_stop_strategy: Strategy name of the final stop that tripped the breaker.
        last_stop_trade_id: UUID of the final stop trade; None if not yet persisted.
    """

    model_config = ConfigDict(frozen=True)

    tripped_at: datetime
    consecutive_stops: int
    cooldown_until: datetime
    last_stop_strategy: str
    last_stop_trade_id: Optional[UUID] = None
