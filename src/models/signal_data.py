"""Pydantic data classes for signals, enums, and candle data.

These are in-memory data transfer objects — distinct from the ORM models in signal.py.
CLAUDE.md §7 source of truth.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class Direction(str, Enum):
    """Trade direction."""

    BUY = "BUY"
    SELL = "SELL"


class Timeframe(str, Enum):
    """Supported XAUUSD timeframes."""

    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


class StrategyName(str, Enum):
    """Canonical strategy identifiers — must match optimizer_results.strategy column."""

    LIQUIDITY_SWEEP = "liquidity_sweep"
    TREND_CONTINUATION = "trend_continuation"
    BREAKOUT_EXPANSION = "breakout_expansion"
    EMA_MOMENTUM = "ema_momentum"


class SignalStatus(str, Enum):
    """Pipeline processing status for candidate signals."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEDUPED = "DEDUPED"


class TradeStatus(str, Enum):
    """Lifecycle status for trade records."""

    OPEN = "OPEN"
    TP1_HIT = "TP1_HIT"
    CLOSED = "CLOSED"
    STOPPED = "STOPPED"


class MarketRegimeType(str, Enum):
    """Market regime classifications for regime detection."""

    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOL = "HIGH_VOL"


class CandleData(BaseModel):
    """In-memory candle OHLCV Pydantic model (not ORM).

    Attributes:
        instrument: Trading instrument, always XAUUSD.
        timeframe: Candle timeframe (M15, H1, H4, D1).
        timestamp: Candle open timestamp (UTC).
        open: Open price.
        high: High price.
        low: Low price.
        close: Close price.
        volume: Tick volume.
        complete: Whether candle is finalised.
    """

    instrument: str = "XAUUSD"
    timeframe: Timeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    complete: bool = True


class CandidateSignal(BaseModel):
    """In-memory candidate signal produced by a strategy (not ORM).

    Per CLAUDE.md §7 — used throughout strategy engine and pipeline.
    No DB writes occur until the pipeline persists approved signals.

    Attributes:
        strategy: Strategy that generated this signal.
        direction: BUY or SELL.
        entry_price: Suggested entry price.
        sl_price: Stop-loss price.
        tp1_price: First take-profit price.
        tp2_price: Optional second take-profit price.
        confidence: Signal quality score in [0.0, 1.0].
        timeframe: Entry timeframe.
        params_snapshot: Copy of params used to generate this signal.
    """

    strategy: StrategyName
    direction: Direction
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: Optional[float] = None
    confidence: float = Field(ge=0.0, le=1.0)
    timeframe: Timeframe
    params_snapshot: dict


class ApprovedSignal(BaseModel):
    """Pipeline-approved signal ready for execution or Telegram send.

    Attributes:
        candidate_signal_id: UUID linking back to the candidate signal.
        rank_score: Composite ranking score from the ranker.
        risk_check_passed: Whether all 3 risk gates were satisfied.
    """

    candidate_signal_id: UUID
    rank_score: float
    risk_check_passed: bool = True


class TradeRecord(BaseModel):
    """In-memory record for a placed or theoretical trade.

    Attributes:
        approved_signal_id: UUID of the approved signal that triggered this trade.
        direction: BUY or SELL.
        entry_price: Actual entry price.
        sl_price: Stop-loss price.
        tp1_price: First take-profit price.
        tp2_price: Optional second take-profit.
        size_lots: Position size in lots.
        status: Current trade lifecycle status.
    """

    approved_signal_id: UUID
    direction: Direction
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: Optional[float] = None
    size_lots: float
    status: TradeStatus = TradeStatus.OPEN


class StrategyParams(BaseModel):
    """Optimisable parameters for a strategy — max 3 per CLAUDE.md §17.

    Attributes:
        strategy: Strategy name.
        params: Dict of param_name → value.
        wfe: Walk-forward efficiency score from last optimizer run.
        is_active: Whether these params are currently active.
    """

    strategy: StrategyName
    params: dict
    wfe: float = 0.0
    is_active: bool = False


class MarketRegime(BaseModel):
    """Market regime snapshot from the regime detector.

    Attributes:
        timestamp: When this regime was detected.
        regime: Regime classification.
        atr_value: ATR value at detection time.
        atr_pctile: ATR percentile rank (0–1).
        adx_value: ADX(14) value if available.
    """

    timestamp: datetime
    regime: MarketRegimeType
    atr_value: float
    atr_pctile: float
    adx_value: Optional[float] = None
