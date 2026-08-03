"""Pure, closed-candle position management for the paper portfolio.

The module owns no I/O and never mutates a broker position.  A caller passes
the frozen policy, the previously persisted state, one newly closed candle and
the indicator history available *as of* that candle.  The result contains the
complete next state plus an optional close decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from math import isfinite
from typing import Mapping, Sequence


AK_POLICY_VERSION = "ak_mfe_ssl_v1"
GENERIC_POLICY_VERSION = "mfe_ratchet_v1"
POLICY_VERSION = AK_POLICY_VERSION
SUPPORTED_POLICY_VERSIONS = {AK_POLICY_VERSION, GENERIC_POLICY_VERSION}


class ExitAction(str, Enum):
    HOLD = "hold"
    CLOSE = "close"


@dataclass(frozen=True)
class DynamicExitPolicy:
    mode: str
    version: str = POLICY_VERSION
    activation_r: float = 1.0
    giveback_r: float = 0.5
    floor_r: float = 0.1
    ema_len: int = 30
    atr_len: int = 14
    ssl_atr_mult: float = 1.0
    close_on_ssl_invalidation: bool = True
    adaptive_target: bool = False
    target_review_buffer_r: float = 0.5
    target_step_r: float = 1.0
    target_strength_min: int = 3

    def __post_init__(self) -> None:
        if self.mode not in {"observe", "execute"}:
            raise ValueError("dynamic_exit.mode must be observe or execute")
        if self.version not in SUPPORTED_POLICY_VERSIONS:
            raise ValueError(f"unsupported dynamic_exit version {self.version!r}")
        if not (self.activation_r > 0 and self.giveback_r > 0):
            raise ValueError("activation_r and giveback_r must be positive")
        if self.floor_r < 0 or self.ema_len < 2 or self.atr_len < 2:
            raise ValueError("invalid dynamic_exit floor/window")
        if self.ssl_atr_mult < 0:
            raise ValueError("ssl_atr_mult must be non-negative")
        if not isinstance(self.adaptive_target, bool):
            raise ValueError("adaptive_target must be boolean")
        if self.target_review_buffer_r <= 0:
            raise ValueError("target_review_buffer_r must be positive")
        if self.target_step_r <= 0:
            raise ValueError("target_step_r must be positive")
        if (
            isinstance(self.target_strength_min, bool)
            or not isinstance(self.target_strength_min, int)
            or not 1 <= self.target_strength_min <= 4
        ):
            raise ValueError("target_strength_min must be an integer from 1 to 4")
        if self.version == GENERIC_POLICY_VERSION and self.floor_r > self.activation_r:
            raise ValueError("floor_r cannot exceed activation_r")
        if self.version == GENERIC_POLICY_VERSION and self.close_on_ssl_invalidation:
            raise ValueError("mfe_ratchet_v1 cannot enable SSL invalidation")
        numeric = (
            self.activation_r, self.giveback_r, self.floor_r,
            self.ssl_atr_mult, self.target_review_buffer_r,
            self.target_step_r,
        )
        if not all(isfinite(value) for value in numeric):
            raise ValueError("dynamic_exit numeric values must be finite")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_config(cls, raw: Mapping[str, object] | None) -> "DynamicExitPolicy | None":
        if not raw:
            return None
        if not isinstance(raw, Mapping):
            raise ValueError("dynamic_exit must be a mapping")
        mode = str(raw.get("mode", "disabled"))
        if mode == "disabled":
            return None
        version = str(raw.get("version", POLICY_VERSION))
        close_on_ssl = raw.get(
            "close_on_ssl_invalidation", version == AK_POLICY_VERSION
        )
        if not isinstance(close_on_ssl, bool):
            raise ValueError("close_on_ssl_invalidation must be boolean")
        adaptive_target = raw.get("adaptive_target", False)
        if not isinstance(adaptive_target, bool):
            raise ValueError("adaptive_target must be boolean")
        target_strength_min = raw.get("target_strength_min", 3)
        if (
            isinstance(target_strength_min, bool)
            or not isinstance(target_strength_min, int)
        ):
            raise ValueError("target_strength_min must be an integer from 1 to 4")
        activation_r = float(raw.get("activation_r", 1.0))
        floor_r = float(raw.get("floor_r", 0.1))
        if floor_r > activation_r:
            raise ValueError("floor_r cannot exceed activation_r")
        return cls(
            mode=mode,
            version=version,
            activation_r=activation_r,
            giveback_r=float(raw.get("giveback_r", 0.5)),
            floor_r=floor_r,
            ema_len=int(raw.get("ema_len", 30)),
            atr_len=int(raw.get("atr_len", 14)),
            ssl_atr_mult=float(raw.get("ssl_atr_mult", 1.0)),
            close_on_ssl_invalidation=close_on_ssl,
            adaptive_target=adaptive_target,
            target_review_buffer_r=float(
                raw.get("target_review_buffer_r", 0.5)
            ),
            target_step_r=float(raw.get("target_step_r", 1.0)),
            target_strength_min=target_strength_min,
        )


@dataclass(frozen=True)
class DynamicExitState:
    policy: DynamicExitPolicy
    peak_favorable_price: float
    mfe_r: float
    dynamic_stop_price: float | None = None
    armed: bool = False
    last_candle_ts: object = None
    tracking_origin: str = "entry"
    tracking_started_at: object = None
    active_target_price: float | None = None
    active_target_r: float | None = None
    target_extensions: int = 0
    target_strength_score: int = 0
    last_target_extension_ts: object = None
    hypothetical_closed: bool = False
    hypothetical_exit_ts: object = None
    hypothetical_exit_reason: str | None = None
    hypothetical_exit_price: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def initial(
        cls,
        policy: DynamicExitPolicy,
        *,
        entry_price: float,
        last_candle_ts: object,
        tracking_origin: str = "entry",
        tracking_started_at: object = None,
        initial_target_price: float | None = None,
        atr_risk: float | None = None,
    ) -> "DynamicExitState":
        if tracking_origin not in {"entry", "adoption"}:
            raise ValueError("tracking_origin must be entry or adoption")
        active_target_price = None
        active_target_r = None
        if (
            initial_target_price is not None
            and atr_risk is not None
            and float(initial_target_price) > float(entry_price)
            and float(atr_risk) > 0
        ):
            active_target_price = float(initial_target_price)
            active_target_r = (
                active_target_price - float(entry_price)
            ) / float(atr_risk)
        return cls(
            policy=policy,
            peak_favorable_price=float(entry_price),
            mfe_r=0.0,
            last_candle_ts=last_candle_ts,
            tracking_origin=tracking_origin,
            tracking_started_at=(
                last_candle_ts if tracking_started_at is None else tracking_started_at
            ),
            active_target_price=active_target_price,
            active_target_r=active_target_r,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "DynamicExitState":
        tracking_origin = str(raw.get("tracking_origin", "entry"))
        if tracking_origin not in {"entry", "adoption"}:
            raise ValueError("tracking_origin must be entry or adoption")
        return cls(
            policy=DynamicExitPolicy(**dict(raw["policy"])),
            peak_favorable_price=float(raw["peak_favorable_price"]),
            mfe_r=float(raw.get("mfe_r", 0.0)),
            dynamic_stop_price=(
                float(raw["dynamic_stop_price"])
                if raw.get("dynamic_stop_price") is not None
                else None
            ),
            armed=bool(raw.get("armed", False)),
            last_candle_ts=raw.get("last_candle_ts"),
            tracking_origin=tracking_origin,
            tracking_started_at=raw.get(
                "tracking_started_at", raw.get("last_candle_ts")
            ),
            active_target_price=(
                float(raw["active_target_price"])
                if raw.get("active_target_price") is not None
                else None
            ),
            active_target_r=(
                float(raw["active_target_r"])
                if raw.get("active_target_r") is not None
                else None
            ),
            target_extensions=int(raw.get("target_extensions", 0)),
            target_strength_score=int(raw.get("target_strength_score", 0)),
            last_target_extension_ts=raw.get("last_target_extension_ts"),
            hypothetical_closed=bool(raw.get("hypothetical_closed", False)),
            hypothetical_exit_ts=raw.get("hypothetical_exit_ts"),
            hypothetical_exit_reason=raw.get("hypothetical_exit_reason"),
            hypothetical_exit_price=(
                float(raw["hypothetical_exit_price"])
                if raw.get("hypothetical_exit_price") is not None else None
            ),
        )


@dataclass(frozen=True)
class ExitDecision:
    action: ExitAction
    reason_code: str
    position_id: str
    decision_candle_ts: object
    desired_price: float | None = None


@dataclass(frozen=True)
class ExitEvaluation:
    next_state: DynamicExitState
    decision: ExitDecision


def _ema(values: Sequence[float], length: int) -> float | None:
    if len(values) < length:
        return None
    alpha = 2.0 / (length + 1.0)
    value = float(values[0])
    for item in values[1:]:
        value = alpha * float(item) + (1.0 - alpha) * value
    return value


def _atr(candles: Sequence[Mapping[str, object]], length: int) -> float | None:
    if len(candles) < length + 1:
        return None
    trs: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        if previous_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - previous_close), abs(low - previous_close))
        trs.append(tr)
        previous_close = close
    alpha = 1.0 / length
    value = trs[0]
    for item in trs[1:]:
        value = alpha * item + (1.0 - alpha) * value
    return value


def _trend_strength_score(
    candles: Sequence[Mapping[str, object]], ema_len: int
) -> int:
    """Return a causal 0..4 continuation score from closed candles only."""
    closes = [float(item["close"]) for item in candles]
    if len(closes) < max(ema_len + 1, 4):
        return 0
    slow = _ema(closes, ema_len)
    previous_slow = _ema(closes[:-1], ema_len)
    fast_len = max(3, min(ema_len - 1, ema_len // 3))
    fast = _ema(closes, fast_len)
    if slow is None or previous_slow is None or fast is None:
        return 0
    return sum((
        closes[-1] > slow,
        slow > previous_slow,
        fast > slow,
        closes[-1] > closes[-4],
    ))


def evaluate_dynamic_exit(
    *,
    position_id: str,
    entry_price: float,
    atr_risk: float,
    initial_stop_price: float | None,
    previous_state: DynamicExitState,
    candle: Mapping[str, object],
    candles_asof: Sequence[Mapping[str, object]],
    initial_target_price: float | None = None,
) -> ExitEvaluation:
    """Advance one position using exactly one newly closed candle.

    ``dynamic_stop_price`` returned here is eligible from the *next* candle.
    The caller must test the previously persisted stop before invoking this
    function.  R is the repository's official ATR sizing unit (``atr_risk``).
    """
    if not (atr_risk > 0 and isfinite(atr_risk)):
        raise ValueError("atr_risk must be finite and positive")
    policy = previous_state.policy
    high = float(candle["high"])
    close = float(candle["close"])
    if not all(isfinite(value) and value > 0 for value in (high, close, entry_price)):
        raise ValueError("dynamic exit received an invalid price")

    peak = max(previous_state.peak_favorable_price, high)
    mfe_r = max(previous_state.mfe_r, (peak - entry_price) / atr_risk)
    armed = previous_state.armed or mfe_r >= policy.activation_r
    stop = previous_state.dynamic_stop_price
    reason = "dynamic_hold"
    action = ExitAction.HOLD
    desired_price = None
    active_target_price = previous_state.active_target_price
    active_target_r = previous_state.active_target_r
    target_extensions = previous_state.target_extensions
    target_strength_score = previous_state.target_strength_score
    last_target_extension_ts = previous_state.last_target_extension_ts
    if (
        policy.adaptive_target
        and active_target_price is None
        and initial_target_price is not None
        and float(initial_target_price) > entry_price
    ):
        active_target_price = float(initial_target_price)
        active_target_r = (active_target_price - entry_price) / atr_risk

    baseline = None
    atr = None
    if policy.version == AK_POLICY_VERSION:
        closes = [float(item["close"]) for item in candles_asof]
        baseline = _ema(closes, policy.ema_len)
        atr = _atr(candles_asof, policy.atr_len)
    if armed:
        ssl_invalidated = (
            policy.close_on_ssl_invalidation
            and baseline is not None
            and atr is not None
            and close <= baseline - policy.ssl_atr_mult * atr
        )
        if ssl_invalidated:
            action = ExitAction.CLOSE
            reason = "dynamic_ssl_invalidation"
            desired_price = close

        lock_r = max(policy.floor_r, mfe_r - policy.giveback_r)
        candidate = entry_price + lock_r * atr_risk
        if initial_stop_price is not None:
            candidate = max(candidate, float(initial_stop_price))
        if baseline is not None and atr is not None:
            candidate = max(candidate, baseline - policy.ssl_atr_mult * atr)
        if stop is not None:
            candidate = max(candidate, stop)

        # A stop computed from this candle cannot be filled retroactively.  If
        # it already sits above the close, the only honest close-candle action
        # is a market-close decision at this close.
        if action == ExitAction.HOLD and candidate >= close:
            action = ExitAction.CLOSE
            reason = "dynamic_profit_reversal"
            desired_price = close
        elif candidate < close:
            stop = candidate

    if policy.adaptive_target:
        target_strength_score = _trend_strength_score(
            candles_asof, policy.ema_len
        )
        if (
            action == ExitAction.HOLD
            and active_target_price is not None
            and active_target_r is not None
            and mfe_r >= max(
                0.0, active_target_r - policy.target_review_buffer_r
            )
            and target_strength_score >= policy.target_strength_min
        ):
            active_target_r += policy.target_step_r
            active_target_price = entry_price + active_target_r * atr_risk
            target_extensions += 1
            last_target_extension_ts = candle.get("ts")
            reason = "dynamic_target_extended"

    next_state = DynamicExitState(
        policy=previous_state.policy,
        peak_favorable_price=peak,
        mfe_r=mfe_r,
        dynamic_stop_price=stop,
        armed=armed,
        last_candle_ts=candle.get("ts"),
        tracking_origin=previous_state.tracking_origin,
        tracking_started_at=previous_state.tracking_started_at,
        active_target_price=active_target_price,
        active_target_r=active_target_r,
        target_extensions=target_extensions,
        target_strength_score=target_strength_score,
        last_target_extension_ts=last_target_extension_ts,
        hypothetical_closed=(
            previous_state.hypothetical_closed
            or (policy.mode == "observe" and action == ExitAction.CLOSE)
        ),
        hypothetical_exit_ts=(
            candle.get("ts")
            if policy.mode == "observe" and action == ExitAction.CLOSE
            else previous_state.hypothetical_exit_ts
        ),
        hypothetical_exit_reason=(
            reason
            if policy.mode == "observe" and action == ExitAction.CLOSE
            else previous_state.hypothetical_exit_reason
        ),
        hypothetical_exit_price=(
            desired_price
            if policy.mode == "observe" and action == ExitAction.CLOSE
            else previous_state.hypothetical_exit_price
        ),
    )
    return ExitEvaluation(
        next_state=next_state,
        decision=ExitDecision(
            action=action,
            reason_code=reason,
            position_id=position_id,
            decision_candle_ts=candle.get("ts"),
            desired_price=desired_price,
        ),
    )


# Backward-compatible public name for callers and legacy AK policy tests.
evaluate_ak_exit = evaluate_dynamic_exit
