# Paper Topup And Stop-Risk Design

Date: 2026-07-17  
Status: approved by the operator

## Goal

Promote the replay-validated thesis-budget `topup` policy directly into the
paper portfolio, while correcting position sizing and portfolio caps to use
the actual entry-to-stop loss distance. Preserve the AK MACD and UT Bot signal
rules agreed during the preceding strategy work.

This remains paper-only. It does not add a broker-order path and does not
authorize restarting the worker, watcher, dashboard, or producer.

## Locked Decisions

- Re-entry policy is `topup`, not `hold` and not shadow-only.
- Static merit order is `btc_utbot_m15_h1` before `btc_ak_macd_4h`.
- Minimum topup fraction is zero.
- Every grant opens a separate tranche with its own frozen stop and take-profit.
- A protective stop or take-profit closes only the tranche whose level was hit.
- A strategy `EXIT` signal closes every tranche belonging to that strategy.
- The BTC thesis cap remains 3% of the shared paper equity.
- The portfolio-wide cap remains authoritative.
- No temporary reduction of UT Bot's configured `risk_pct` is introduced.
- AK MACD and UT Bot signal generation, indicators, parameters, and timeframes
  are unchanged.

## Why The Production Shape Differs From The Harness Prototype

The replay arbiter identifies a tranche by suffixing the strategy identifier,
for example `btc_utbot_m15_h1::t2`. That shortcut is acceptable inside an
isolated report, but it would leak false strategy identifiers into persisted
paper fills, dashboard filters, attribution, and future reports.

The paper implementation therefore separates the two identities:

- `strategy_id` is stable and continues to identify AK MACD or UT Bot;
- `position_id` uniquely identifies one independently managed tranche.

Legacy single-position rows remain valid: when `position_id` is absent, the
loader uses the persisted dictionary key, which historically equals the
strategy identifier.

## Position And Risk Contract

Every position persists both:

- `atr_risk`: the legacy `2 * ATR(14)` diagnostic basis used when the signal
  supplies no stop;
- `risk_distance`: the per-unit loss distance used for sizing, caps, and R.

For a long entry:

1. If the strategy supplies a finite stop strictly below the entry,
   `risk_distance = entry_price - stop_loss_price`.
2. If the strategy supplies no stop, `risk_distance = 2 * ATR(14)`.
3. If the strategy supplies a stop that is non-finite, zero, or at/above the
   entry, the entry is refused as `invalid_stop`; the engine must not silently
   replace a malformed explicit stop with ATR.

Quantity is:

`granted_risk_usd / risk_distance`

Open portfolio risk and symbol-thesis risk are always:

`sum(position.qty * position.risk_distance)`

For legacy persisted positions without `risk_distance`, the loader falls back
to their existing `atr_risk`. This is a compatibility rule, not a rewrite of
historical state.

## Tranche Model

`Account.positions` remains a dictionary keyed by `position_id`. A position
stores its stable `strategy_id` separately. The first tranche may keep the
historical identifier; later tranches receive deterministic identifiers such
as `btc_utbot_m15_h1::t2` without changing their `strategy_id`.

Broker operations address `position_id`. Fill records contain both
`position_id` and the base `strategy_id`, so existing strategy attribution
continues to work and individual tranche lifecycles remain auditable.

Account equity remains the realized balance plus the sum of every tranche's
unrealized PnL. Fees are assessed independently for each tranche at entry and
exit, matching the replay policy.

## Paper-Engine Decision Flow

Each closed-candle cycle keeps the current two-phase structure:

1. Fetch closed candles and gather one signal per configured strategy.
2. Replay unseen monitoring candles over every open tranche and apply frozen
   protective levels.
3. Resolve non-entry intents. A strategy `EXIT` closes all of its remaining
   tranches.
4. Collect all eligible long-entry requests for the cycle.
5. Sort requests by the configured merit order, then stable configuration
   order.
6. Before each grant, recompute actual open risk from all remaining tranches.
7. Grant the minimum of requested risk, remaining symbol-thesis budget, and
   remaining portfolio budget.
8. Open a new tranche when the grant is positive; otherwise journal the exact
   binding refusal.

The expected intents are `open`, `open_topup`, `thesis_already_funded`,
`risk_cap_total`, `entry_disabled`, `invalid_stop`, and the existing close or
no-trade outcomes. Auction records persist requested risk, granted risk,
remaining budgets, tranche count, and outcome.

If one tranche exits protectively during a cycle, the strategy does not reopen
on the same cycle. Other strategies remain isolated and continue normally.

## Configuration

The paper portfolio configuration makes the selected policy explicit:

```yaml
reentry_policy: topup
merit_order:
  - btc_utbot_m15_h1
  - btc_ak_macd_4h
min_topup_fraction: 0.0
```

Unknown merit identifiers and unsupported re-entry policies fail during engine
construction. Existing configurations without these keys retain their current
single-position `hold` behavior so offline callers and old fixtures do not
silently change semantics.

## Dashboard Contract

The dashboard continues to group fills and positions by stable `strategy_id`.
For a strategy with multiple open tranches it shows:

- `LONG ONLY` on the UT Bot card;
- open tranche count;
- aggregate notional;
- aggregate actual stop risk in USD and as a percentage of current equity;
- each tranche's entry, stop, take-profit, and individual risk in the detail
  payload.

Real markers continue to use the base strategy identifier. Topup entries are
distinguishable through `position_id` without creating extra strategy cards.
The generic dashboard legend must not imply that UT Bot can open shorts.

## Verification Strategy

Implementation follows test-driven development. Failing tests are required
before each behavior change.

Focused coverage must prove:

- explicit-stop entries size from the entry-to-stop distance;
- no-stop strategies retain the `2 * ATR(14)` fallback;
- malformed explicit stops are refused;
- old persisted positions load with `risk_distance = atr_risk`;
- two topup tranches coexist under one stable strategy identifier;
- protective exits close only the hit tranche;
- a strategy `EXIT` closes all of its tranches;
- caps sum actual tranche stop risk;
- merit order determines same-cycle grants;
- zero-fraction topups are accepted when budget remains;
- dashboard aggregation and marker filtering remain strategy-correct;
- long-only signal generation is unchanged.

After focused and full-suite tests, the deterministic 2024-01-01 through
2026-07-15 replay must be rerun for both `hold` and `topup` using the corrected
risk contract. The prior `+391% / 34.4% DD` result is treated as legacy-sizing
evidence, not as the expected corrected result. The new comparison must report
return, maximum drawdown, fills, tranche counts, fees, risk-cap outcomes, and
the existing 0/2/5/10 bps execution stress.

## Non-Goals

- No short support.
- No changes to strategy signal logic.
- No HA reactivation.
- No forecast-gate restoration.
- No learned or rolling merit score.
- No broker, exchange order, margin, or settlement integration.
- No automatic process restart.

## Rollback

Before any process restart, rollback is the code/config revert because the
running processes have not loaded the change. After activation, setting
`reentry_policy: hold` stops new topups while preserving already-open tranches
for their normal protective or strategy exits. Persisted `position_id` and
`risk_distance` fields remain backward-compatible audit data and do not need to
be deleted.
