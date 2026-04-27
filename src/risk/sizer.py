"""ATR-based position sizer (RISK-04) — pure synchronous function.

Per AGENTS.md §12.2 + CONTEXT D-07: order vol -> cap -> concentration.
No I/O — caller passes equity / regime / trade-context as kwargs.
Orchestrator (src/risk/runner.py) emits the structured log entry after calling
this function; this module does not log.

atr_value is accepted in the signature for future use (e.g. ATR-based
stop-distance sanity checks); it is not used in v1 sizing math.
"""

from decimal import Decimal

from src.risk.events import PositionSizing


def calculate_position_size(
    *,
    equity: Decimal,
    risk_per_trade: float,
    entry_price: Decimal,
    sl_price: Decimal,
    atr_value: Decimal,
    atr_pctile: float,
    hard_cap: float,
    atr_high_vol_pctile: int,
    atr_low_vol_pctile: int,
    same_direction_open_count: int,
) -> PositionSizing:
    """Compute position size using ATR-regime-adjusted risk percentage.

    Order of operations (AGENTS.md §12.2, CONTEXT D-07):
      1. Normalize whole-number settings to 0.0-1.0 thresholds.
      2. Determine vol_factor from atr_pctile vs thresholds (0.7 / 1.0 / 1.3).
      3. risk_pct = risk_per_trade * vol_factor
      4. risk_pct = min(risk_pct, hard_cap)     — cap AFTER vol bump (D-07).
      5. concentration_reduced = same_direction_open_count >= 4
      6. risk_pct *= 0.5  if concentration_reduced — halve AFTER cap (D-04).
      7. risk_amount = equity * Decimal(str(risk_pct))  — Pitfall 5 guard.
      8. sl_distance = abs(entry_price - sl_price)
      9. size_lots = risk_amount / (sl_distance * 100) quantized to 0.01,
         or Decimal("0") when sl_distance == 0 (XAUUSD: 1 lot = 100 oz).

    Args:
        equity: Account equity in USD.
        risk_per_trade: Base risk fraction (e.g. 0.01 for 1%).
        entry_price: Trade entry price (Decimal, XAUUSD).
        sl_price: Stop-loss price (Decimal, XAUUSD).
        atr_value: Current ATR value — reserved for future use; not used in v1.
        atr_pctile: ATR percentile on 0.0-1.0 scale (RegimeDetector output).
        hard_cap: Maximum allowed risk_pct after vol adjustment (e.g. 0.02).
        atr_high_vol_pctile: High-vol threshold in whole numbers (e.g. 90 -> 0.90).
        atr_low_vol_pctile: Low-vol threshold in whole numbers (e.g. 10 -> 0.10).
        same_direction_open_count: Number of open positions in the same direction.

    Returns:
        PositionSizing with risk_pct, risk_amount_usd, size_lots, vol_factor,
        and concentration_reduced fields populated.
    """
    # Step 1: Normalize whole-number settings to 0.0-1.0 thresholds.
    high_threshold = atr_high_vol_pctile / 100.0
    low_threshold = atr_low_vol_pctile / 100.0

    # Step 2: Determine vol_factor (THREE-branch: high / low / normal).
    if atr_pctile >= high_threshold:
        vol_factor = 0.7
    elif atr_pctile <= low_threshold:
        vol_factor = 1.3
    else:
        vol_factor = 1.0

    # Step 3: Apply vol adjustment.
    risk_pct = risk_per_trade * vol_factor

    # Step 4: Apply hard cap AFTER vol adjustment (D-07: bump cannot escape cap).
    risk_pct = min(risk_pct, hard_cap)

    # Step 5-6: Concentration halving AFTER cap (D-04 REDUCE semantics).
    concentration_reduced = same_direction_open_count >= 4
    if concentration_reduced:
        risk_pct *= 0.5

    # Step 7: Compute dollar risk — Pitfall 5: Decimal x float requires str cast.
    risk_amount = equity * Decimal(str(risk_pct))

    # Step 8-9: Compute lot size; guard against zero sl_distance (T-06-04-04).
    sl_distance = abs(entry_price - sl_price)
    if sl_distance == 0:
        size_lots = Decimal("0")
    else:
        # XAUUSD: 1 lot = 100 oz (AGENTS.md §12.2).
        size_lots = (risk_amount / (sl_distance * Decimal("100"))).quantize(
            Decimal("0.01")
        )

    return PositionSizing(
        risk_pct=risk_pct,
        risk_amount_usd=risk_amount,
        size_lots=size_lots,
        vol_factor=vol_factor,
        concentration_reduced=concentration_reduced,
    )
