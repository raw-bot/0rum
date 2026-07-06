"""Frozen-at-entry SL/TP bracket + risk-based sizing for exit_mode="bracket".

The AK MACD strategy exits ONLY on stop-loss or take-profit. Both levels are
computed ONCE at entry from the baseline and the recent high/low, and never move
afterwards (no trailing, no %-stop, no max_hold, no signal exit). The position is
sized so the dollar risk at the stop is constant (risk_usd) regardless of how far
the stop sits: a wider stop -> smaller size, a tighter stop -> larger size.

Pure module: no I/O, no globals — every input is passed in, so the math below is
exactly what the unit tests assert.

  LONG : SL = min(baseline_at_entry, recent_low);  risk = entry - SL; TP = entry + rr*risk
  SHORT: SL = max(baseline_at_entry, recent_high); risk = SL - entry; TP = entry - rr*risk
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_RR = 1.5

# Safety guardrails (overridable via strategy.yaml risk: block). Defaults are
# intentionally permissive enough not to disturb wide-stop trades, but they cap
# the disproportionate positions a tight stop would otherwise demand.
DEFAULT_MAX_LEVERAGE = 3.0       # notional <= account_equity * max_leverage
DEFAULT_FEE_RATE = 0.0004        # per side; round-trip cost = notional * fee * 2
DEFAULT_MIN_REWARD_RISK = 1.0    # refuse if fee-adjusted reward/risk falls below


@dataclass(frozen=True)
class Bracket:
    direction: str            # "long" | "short"
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    risk_distance: float      # always > 0
    sl_basis: str             # "baseline" | "recent_low" | "recent_high"
    reward_risk_ratio: float


def compute_bracket(
    *,
    entry_price: float,
    baseline_at_entry: float,
    recent_low: float | None = None,
    recent_high: float | None = None,
    direction: str = "long",
    rr: float = DEFAULT_RR,
) -> Bracket:
    """Compute the frozen SL/TP bracket. Raises ValueError on a non-positive
    risk distance (a degenerate setup that must NOT be opened)."""
    if direction == "long":
        if recent_low is None:
            raise ValueError("recent_low is required for a long bracket")
        # Widest (most protective) stop = the lower of the two for a long.
        if baseline_at_entry <= recent_low:
            stop_loss_price, sl_basis = baseline_at_entry, "baseline"
        else:
            stop_loss_price, sl_basis = recent_low, "recent_low"
        risk_distance = entry_price - stop_loss_price
        take_profit_price = entry_price + rr * risk_distance
    elif direction == "short":
        if recent_high is None:
            raise ValueError("recent_high is required for a short bracket")
        # Widest (most protective) stop = the higher of the two for a short.
        if baseline_at_entry >= recent_high:
            stop_loss_price, sl_basis = baseline_at_entry, "baseline"
        else:
            stop_loss_price, sl_basis = recent_high, "recent_high"
        risk_distance = stop_loss_price - entry_price
        take_profit_price = entry_price - rr * risk_distance
    else:
        raise ValueError(f"unknown direction {direction!r}")

    if risk_distance <= 0:
        raise ValueError(
            f"non-positive risk_distance ({risk_distance}) for {direction} "
            f"entry={entry_price} sl={stop_loss_price}; degenerate setup, do not open"
        )

    return Bracket(
        direction=direction,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        risk_distance=risk_distance,
        sl_basis=sl_basis,
        reward_risk_ratio=rr,
    )


def ssl_band(*, baseline: float, atr: float, atr_mult: float) -> tuple[float, float]:
    """SSL continuation band (lower, upper) around the baseline — same geometry as
    pine/ak_macd_15m.pine: bandLo = baseline - atr*mult, bandUp = baseline + atr*mult.

    Here ``atr_mult`` is the TRAIL buffer (k), intentionally wider than the 0.2 that
    colors the dots: it is the hysteresis that holds a trend through shallow
    pullbacks instead of whipsawing the stop out.
    """
    buf = atr * atr_mult
    return baseline - buf, baseline + buf


def trail_stop(
    *, direction: str, stop_loss_price: float, baseline: float, atr: float, atr_mult: float
) -> float:
    """One-way ratchet of the bracket stop toward the SSL band.

    The stop may only TIGHTEN — rise for a long, fall for a short — so realized risk
    can only shrink below the entry ``risk_usd``; it never loosens past the frozen
    structural stop. Returns the (possibly unchanged) stop price.
    """
    lower, upper = ssl_band(baseline=baseline, atr=atr, atr_mult=atr_mult)
    if direction == "long":
        return max(stop_loss_price, lower)
    if direction == "short":
        return min(stop_loss_price, upper)
    raise ValueError(f"unknown direction {direction!r}")


def band_invalidated(
    *, direction: str, close: float, baseline: float, atr: float, atr_mult: float
) -> bool:
    """True when price has closed back THROUGH the SSL band against the trade.

    This is the "red line" (long) / "blue line" (short) thesis invalidation — the
    soft exit that cuts a dead trade near break-even instead of riding the frozen
    structural stop for hours (cf. the 109-candle loser, BTC 27/06).
    """
    lower, upper = ssl_band(baseline=baseline, atr=atr, atr_mult=atr_mult)
    if direction == "long":
        return close < lower
    if direction == "short":
        return close > upper
    raise ValueError(f"unknown direction {direction!r}")


def bracket_sizing(
    *,
    account_equity: float,
    risk_pct: float,
    risk_distance: float,
    entry_price: float,
    reward_risk_ratio: float = DEFAULT_RR,
    max_leverage: float | None = None,
    max_notional_usd: float | None = None,
    fee_rate: float = 0.0,
    min_reward_risk: float | None = None,
) -> dict:
    """Risk-based sizing with safety caps and a fee-adjusted reward/risk gate.

    Base sizing (unchanged): the loss at the stop equals risk_usd, independent of
    the stop distance. ``risk_pct`` is a fraction (0.005 == 0.5%).

        position_size (qty_base) = risk_usd / risk_distance
        notional                 = position_size * entry_price

    A tight stop demands a large position to keep risk_usd constant, which can
    blow notional far past the account (hidden leverage). Two guardrails fix that
    WITHOUT moving the SL/TP price levels (the edge is untouched):

      1. Notional cap: notional <= min(account_equity * max_leverage,
         max_notional_usd). Capping shrinks qty, so the effective risk_usd drops
         BELOW the target -- the trade just gets safer, never riskier.
      2. Real reward/risk: after the cap, the reward/risk is recomputed in
         DOLLARS including round-trip fees (the gross price-based ratio is a
         fiction once fees bite, and fees bite hardest on tight stops). If that
         fee-adjusted ratio falls below ``min_reward_risk`` the trade is marked
         not accepted so the caller refuses it instead of opening.

    Caps/gate are opt-in: with the defaults (no caps, fee_rate=0, no min RR) the
    output is byte-for-byte the legacy sizing plus inert metadata.
    """
    if risk_distance <= 0:
        raise ValueError("risk_distance must be positive for bracket sizing")

    # Base risk-based sizing (target dollar risk at the stop).
    risk_usd_target = account_equity * risk_pct
    qty_base = risk_usd_target / risk_distance
    notional_usd = qty_base * entry_price

    # Observation-only: the size the strategy WANTED before any cap, so the log
    # can show the hidden leverage a tight stop demanded. Does not affect sizing.
    notional_requested_usd = notional_usd
    leverage_requested = (notional_requested_usd / account_equity) if account_equity else 0.0

    # 1) Notional / leverage cap -- take the most restrictive ceiling present.
    ceilings: list[tuple[str, float]] = []
    if max_leverage is not None:
        ceilings.append(("max_leverage", account_equity * max_leverage))
    if max_notional_usd is not None:
        ceilings.append(("max_notional", max_notional_usd))
    cap_reason: str | None = None
    if ceilings:
        reason, ceiling = min(ceilings, key=lambda kv: kv[1])
        if notional_usd > ceiling:
            notional_usd = ceiling
            qty_base = notional_usd / entry_price if entry_price else 0.0
            cap_reason = reason

    # Effective dollar risk after any cap (<= target once capped).
    risk_usd = qty_base * risk_distance

    # 2) Fee-adjusted (real) reward/risk on the position we'd actually take.
    fees_usd = notional_usd * fee_rate * 2.0
    gross_reward_usd = qty_base * reward_risk_ratio * risk_distance
    net_reward_usd = gross_reward_usd - fees_usd
    net_risk_usd = risk_usd + fees_usd
    effective_reward_risk = net_reward_usd / net_risk_usd if net_risk_usd > 0 else 0.0

    accepted = True
    reject_reason: str | None = None
    if min_reward_risk is not None and effective_reward_risk < min_reward_risk:
        accepted = False
        stop_pct = (risk_distance / entry_price * 100.0) if entry_price else 0.0
        reject_reason = (
            f"real reward/risk {effective_reward_risk:.2f} < min {min_reward_risk:.2f} "
            f"after fees (stop {stop_pct:.2f}% of price)"
        )

    return {
        "risk_usd": risk_usd,
        "qty_base": qty_base,
        "notional_usd": notional_usd,
        "notional_requested_usd": notional_requested_usd,
        "leverage_requested": leverage_requested,
        "capped": cap_reason is not None,
        "cap_reason": cap_reason,
        "gross_reward_risk": reward_risk_ratio,
        "effective_reward_risk": effective_reward_risk,
        "accepted": accepted,
        "reject_reason": reject_reason,
    }
