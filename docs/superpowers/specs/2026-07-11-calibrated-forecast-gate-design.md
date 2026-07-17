# Calibrated forecast gate — design

## Goal

Turn the dashboard scenario fan into an auditable, paper-only forecast gate for
new long entries in the single shared portfolio. The gate may veto or resize an
entry; it never changes exits, existing positions, leverage limits, or broker
state outside the current paper account.

## Forecast contract

- Source: closed Binance spot candles at 1 hour, newest last.
- Horizons: +6 h, +12 h and +24 h.
- Output: return quantiles P10, P25, P50, P75 and P90.
- Model: nearest historical regimes using only information available at each
  forecast origin. Regime features are trailing 24-hour return and realized
  hourly volatility. Neighbours are selected in standardized feature space.
- Evaluation: chronological walk-forward predictions. A target at `t+h` is
  never admitted to the training set before `t+h` exists.
- Calibration metrics: P10–P90 empirical coverage, median direction accuracy,
  mean quantile (pinball) loss and skill versus an unconditional historical
  quantile baseline.

## Activation lock

The forecast may influence an entry only when the +12 h and +24 h horizons each
have at least 250 realized walk-forward forecasts, P10–P90 coverage in
`[0.72, 0.88]`, direction accuracy of at least `0.52`, and strictly positive
pinball skill against the baseline. Missing, stale or invalid data locks the
gate. The +6 h horizon remains diagnostic and does not unlock the gate alone.

These thresholds are intentionally fixed before observing the result. Failure
to qualify is evidence that the current forecaster has not earned control, not
a reason to relax the threshold.

## Entry policy

The baseline strategy signal is always retained in the audit record.

- `veto` (`0.0x`): P50 is non-positive at both +12 h and +24 h, or P90 at +24 h
  is non-positive.
- `shrink` (`0.50x`): P10 at +24 h is worse than the strategy ATR risk fraction.
- `boost` (`1.15x`): P25 is positive at +12 h and +24 h and both direction
  accuracies are at least `0.55`.
- `pass` (`1.00x`): all other qualified forecasts.
- `locked` (`1.00x`): activation criteria are not met.

The multiplier is applied to `risk_pct` before the existing broker sizes the
position. It can never bypass existing risk or leverage controls. Exits are
never filtered.

## Persistence and UI

- `state/forecast_gate.json`: latest forecasts, metrics and decisions by asset.
- `state/forecast_audit.jsonl`: append-only counterfactual record containing the
  baseline intent, gate action, multiplier, quantiles and reasons.
- The dashboard reads this state and renders calibrated quantile curves. It no
  longer labels the fan as decorative when calibrated state exists.
- Horizontal pointer dragging pans through the available 1-hour history. Card
  movement remains restricted to the card header.

## Safety and rollback

The feature is paper-only and defaults to enabled-but-locked. Set
`forecast_gate.enabled: false` in `state/portfolio.yaml` to make it observe and
audit without influencing sizing. Removing the block also defaults to disabled,
so older configurations preserve their behavior.

