# Live Paper Audit Repair Design

## Goal

Make the bot's live paper state understandable and enforceable: one
authoritative portfolio, explicit position sides and exit policies, real
protective exits for bracket strategies, and an auditable record of every
6-hour forecast compared with the market that followed.

The user approved the complete repair on 2026-07-13 after the weekly audit.
The implementation remains paper-only and must not place broker orders.

## Observed failures

- The legacy `orum.run` worker and local AK producer still run beside the
  unified `PaperEngine`, but the dashboard reads only the unified ledger.
- The local AK producer continues to submit candidates through a retired
  external-source route, so every candidate is rejected as unauthorized.
- The unified BTC AK position has no persisted stop-loss or take-profit, and
  `AkMacdEngine` cannot emit an exit signal by design.
- A real paper open is labelled only `IN`; model markers such as `InL` are not
  distinguished clearly from filled trades or given an unambiguous direction.
- Forecast state is rewritten with only the strategies evaluated on the current
  cycle. A strategy card may therefore display another strategy's asset-level
  forecast.
- Only the latest forecast exists. There is no honest way to reconstruct what
  was predicted earlier in the week.

## Considered approaches

### 1. Dashboard-only patch

This would improve labels but leave the AK position without an executable exit,
retain cross-strategy forecast drift, and continue hiding the competing paper
history. It does not repair the trading state.

### 2. Reauthorize the local AK producer

This would make previously rejected candidates tradable again, but through a
second ledger and risk path. It can duplicate BTC exposure and make portfolio
P&L irreconcilable. This approach is rejected.

### 3. Unified portfolio plus explicit legacy audit

The unified `PaperEngine` remains the only authoritative paper ledger. The
legacy worker and producer are exposed as non-authoritative diagnostics until
the operator explicitly approves their shutdown. Their historical trades stay
visible in a separate panel and are never merged into unified equity or P&L.
This is the selected approach.

The authoritative `btc_ak_macd_4h` sleeve accepts new entries after its current
position closes. It evaluates the validated H4 long-only policy directly;
short candidates remain disabled. The retired local producer stays
unauthorized and is not used as an alternate entry route.

## Position and exit contract

Every persisted position gains optional, backward-compatible fields:

- `stop_loss_price` and `take_profit_price`;
- `exit_policy`;
- `monitor_timeframe`;
- `sl_basis` and `reward_risk_ratio` when a bracket exists.

`PaperEngine` evaluates protective exits on closed monitoring candles even when
the strategy's primary candle is a duplicate. For long positions, a candle low
at or below SL closes at the frozen SL; a candle high at or above TP closes at
the frozen TP. If both are touched on the same candle, the conservative rule is
SL first. Every close records `stop_loss`, `take_profit`, or the strategy's
signal reason.

Exit policies are strategy-specific and must be shown honestly:

- `btc_ak_macd_4h`: frozen structural bracket, RR 1.5, monitored on closed M15
  candles. New entries use `baseline_at_entry` and `recent_low` from the AK
  signal payload. The already-open position is migrated once with the only
  persisted risk basis available: SL = entry − `atr_risk`, TP = entry +
  1.5 × `atr_risk`, with `sl_basis=atr_fallback_migration`.
- `btc_utbot_m15_h1`: the signal's suggested UT stop is persisted as a
  protective stop and the normal UT SELL crossover remains the strategy exit.
  No take-profit is invented.
- `eth_donchian`: the 10-day channel break remains the exit. No SL or TP is
  invented for the existing position.

Existing account JSON remains loadable because all new position fields have
safe defaults. Migration changes only missing metadata and never re-sizes an
open position.

## Forecast audit contract

The latest forecast state is merged by `strategy_id`; a duplicate candle must
not delete the previous row. Dashboard cards resolve forecast state by strategy
only and never fall back to another strategy sharing the same asset.

An append-only `state/forecast_history.jsonl` records one prediction per
strategy and UTC 6-hour bucket. Each prediction stores:

- forecast origin timestamp and origin price;
- p10/p50/p90 returns for +6 h, +12 h and +24 h;
- the decision and qualification state used at that origin.

As horizons mature, append-only realization records store the actual closed
price, actual return, median error and direction correctness. No pre-deployment
history is fabricated. The dashboard states when the archive began.

The market chart renders archived p50 paths in yellow at 50% opacity and the
corresponding realized path as a distinct solid line. The existing future p50
line also uses 50% opacity. The +18 h point remains a labelled interpolation
between the independent +12 h and +24 h model horizons; it is not represented
as a separately trained forecast.

## Dashboard truth contract

- Real fills use `IN L` or `IN S`; closes use `TP`, `SL`, or `OUT` according to
  the persisted reason, not according to whether P&L happened to be positive.
- Display-only strategy markers are prefixed `MODEL` and cannot be mistaken for
  fills.
- Every open-position card shows side, entry, mark, unrealized P&L, exit policy,
  SL, TP and age. Missing levels render as an explicit dash with the signal exit
  description.
- Legacy trades appear in a separate, labelled, non-authoritative weekly audit.
  They never affect unified balance, equity, score or trade count.
- A health warning remains visible while the legacy worker/producer process is
  still running.

## Operational boundary

Code and paper state may be prepared and verified without process control. No
dashboard, worker, watcher, producer or launchd service is stopped or restarted
without a second explicit operator confirmation. Until then, the old process
can continue logging rejected candidates, but it cannot become authoritative or
open a unified position.

## Verification and rollback

- Unit tests cover backward-compatible position loading, bracket construction,
  conservative SL/TP collision handling, monitoring on duplicate primary
  candles and strategy-signal exits.
- Forecast tests cover state merge, 6-hour deduplication, matured realizations
  and strict strategy lookup.
- Dashboard tests cover directional fill labels, reason-based exit labels,
  position policy fields, legacy separation and forecast-history payloads.
- The full suite, a dry portfolio construction, API snapshot inspection and
  static browser inspection must pass before completion is claimed.
- Rollback is code-only: revert the new optional fields/readers and keep the
  append-only audit file. Existing fills and account balances are never edited
  or deleted.
