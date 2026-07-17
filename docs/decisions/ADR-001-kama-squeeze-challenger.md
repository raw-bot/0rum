# ADR-001: Evaluate KAMA squeeze as an inactive challenger

## Status

Accepted for offline research only

## Date

2026-07-10

## Context

The active paper portfolio already owns a BTC/USDT position opened by the
AK-MACD 4h strategy. A proposed long-only KAMA trend runner combines a
Bollinger/Keltner squeeze release, positive rising linear-regression momentum,
a rising KAMA, and an efficiency-ratio gate. Its supplied sizing used 10x
leverage and non-monotonic ATR regime multipliers (4.2 / 0.1 / 7.4), which can
place the planned stop beyond a liquidation boundary and increase risk during
high volatility.

The supplied settings omitted two values required for reproducibility. This
challenger freezes ATR length at 14, matching the existing research stack, and
linear-regression momentum length at 16, matching the supplied squeeze length.

## Decision

Implement `kama_squeeze` as a deterministic, registry-loadable strategy engine,
but do not add it to `state/portfolio.yaml` and do not let it open a second BTC
position.

The executable indicator contract is:

- KAMA source is close; efficiency length 32; fast/slow periods 2/50.
- Efficiency ratio is `abs(close[t]-close[t-32]) / sum(abs(delta close), 32)`;
  a zero denominator produces zero, never NaN.
- KAMA is rising when `kama[t] > kama[t-1]`.
- Bollinger Bands use a 16-bar close SMA and population standard deviation at
  2.0 standard deviations.
- Keltner Channels use the same 16-bar close SMA and a 16-bar SMA of true range
  at 1.5 ranges.
- Squeeze-on means both Bollinger bands are strictly inside the Keltner channel.
  Release means the preceding run contains at least two squeeze-on bars and the
  current confirmed bar is squeeze-off.
- Momentum uses the endpoint of a 16-bar ordinary least-squares regression of
  `close - ((highest high + lowest low)/2 + SMA(close))/2`. Entry requires the
  endpoint to be positive and strictly higher than its preceding value.
- Entry also requires close above KAMA and ER strictly greater than 0.20.
- ATR uses Wilder smoothing over 14 bars. Initial risk distance is 2.8 times
  signal-bar ATR.
- The long trail is the non-decreasing maximum of the prior effective stop,
  the initial stop, and `highest confirmed close since entry - 5*current ATR`.
- A confirmed close at or below the effective stop requests an exit on the next
  bar. A close below KAMA while momentum is negative does the same. Intrabar
  highs/lows never create a same-bar fill.
- Signals and exits are calculated from confirmed bars; research fills occur at
  the next bar open with configured slippage and fees.

The fair comparison uses spot-style 1x exposure, a 0.5% account-risk budget per
trade, and a 10% notional cap. Position sizing is continuous:

`notional = min(equity * 10%, equity * 0.5% / (2.8 * ATR / entry_price))`.

## Alternatives Considered

### Activate KAMA beside AK-MACD

Rejected because both positions express the same BTC beta and would make the
portfolio appear diversified while doubling directional exposure.

### Preserve fixed 10x leverage

Rejected because leverage does not create signal edge and introduces funding,
mark-price, maintenance-margin and liquidation behavior absent from the current
spot paper broker.

### Tune missing definitions after seeing results

Rejected because choosing ATR or momentum semantics after observing performance
would add unreported degrees of freedom.

## Consequences

- Existing paper positions, journals and active configuration stay untouched.
- KAMA can be backtested and loaded through the engine registry, but cannot trade
  until a later explicit promotion decision.
- Promotion requires a same-data, next-bar, after-cost comparison against
  AK-MACD plus chronological out-of-sample and parameter-neighborhood checks.
