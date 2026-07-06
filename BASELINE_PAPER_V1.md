# baseline_paper_v1 — frozen paper-trading baseline

**Freeze window:** 2026-06-19 → ~2026-07-03 (1–2 weeks).
**Goal:** observe the current strategy in real paper trading, fully logged, with
**zero strategic change**, then compare against the long backtest before deciding
whether exits / sizing / filters should change.

## Frozen configuration (DO NOT CHANGE during the window)

### Strategy — AK MACD 15m (long + short), unchanged
- Signal brain: `orum/external/ak_macd_producer.py` (live, `--interval 30`).
- Indicator params (frozen, Pine `ak_macd_15m.pine` defaults):
  MACD 12/26/9 · baseline EMA30 · ATR14 × 0.2 · volMA9 · RR 1.5.
- Entry confirmation (`strategy.yaml` → `ak_macd:`): `confirmation_bars: 2`,
  `candidate_window_bars: 2`, `regime_filter: true`. **Unchanged.**

### Exits — unchanged
- Frozen-at-entry SL/TP **bracket** (`exit_mode="bracket"`), computed once at
  entry by `compute_bracket`. Bracket-only exits (SL or TP).
- **No** partial TP · **no** trailing stop · **no** time-stop · **no** new filter.

### Risk config (`state/strategy.yaml` → `risk:`) — unchanged except the safety cap
```yaml
risk:
  stop_loss_pct: 2.0        # native path only; bracket path uses real price risk
  take_profit_pct: 3.0      # native path only
  max_hold_candles: 120     # native path only; bracket ignores max_hold
  position_size_r: 2.0      # 2% risk per trade
  max_leverage: 1.0         # SAFETY GUARDRAIL ONLY — notional capped at 1x equity
```
- `max_leverage: 1.0` is kept **solely as a safety guardrail**. Known effect:
  because the strategy's stops are very tight, ~98% of trades get capped to 1x
  in backtest. This is accepted for the baseline; it is NOT a strategic tuning.
- Sizing gate defaults (engine, `bracket.py`): `fee_rate=0.0004`,
  `min_reward_risk=1.0` (fee-adjusted RR floor → refuses tightest-stop setups).

## Baseline membership rules

**Rule 1 — baseline start cutoff.** `baseline_paper_v1` begins at the **first
trade opened after `max_leverage=1.0` went live** = worker boot
`2026-06-19T09:47:38Z`. Any trade with `opened_at` before that is EXCLUDED from
validation metrics, including the legacy ~3.5x short (opened `07:45:20Z`) and the
3 earlier paper trades. The daily report enforces this (`BASELINE_START`), marks
excluded rows `EXCL`, and counts only in-baseline trades in the summaries.

**Rule 2 — no selftest writes to live state.** Tests / dry-runs / ad-hoc signal
injections MUST run against an isolated state, never `state/`. Mechanism added
`2026-06-19`: `orum/paths.py` now honours `0RUM_STATE_DIR` —
```bash
0RUM_STATE_DIR=$(mktemp -d) uv run python <any test or injection>
```
With the env unset, the live default is byte-for-byte unchanged. The standard
scripts (`replay_ak_macd.py`, `ak_macd_bridge_selftest.py`) are already isolated
(in-memory / temp). The daily report flags any executed signal priced `< $1000`
as likely selftest noise so it never contaminates the read.

## Allowed actions during the freeze
1. Verify heartbeat / worker health (`scripts/daily_paper_report.py`).
2. Run the **daily paper report**.
3. Verify trades are logged.
4. Fix **blocking bugs OR log pollution only**.
5. Keep this document accurate.
6. Produce the **final comparison** after 1–2 weeks.

Explicitly NOT allowed: any change to entries, exits, sizing, risk config,
filters, or parameters.

## Logging captured per closed trade (`state/trades.jsonl`)
Observation-only fields added 2026-06-19 (additive; execution unchanged, all 325
tests green). Trades opened before this date show `—` for the new fields.

| field | meaning |
|---|---|
| `direction`, `entry_price`, `exit_price` | side + fills |
| `exit_reason` | `stop_loss` (SL) / `take_profit` (TP) |
| `risk_distance` | stop distance in price; TP distance = `risk_distance × reward_risk_ratio` |
| `stop_loss_price`, `take_profit_price`, `sl_basis` | frozen bracket levels |
| `reward_risk_ratio` | gross (price) RR = 1.5 |
| `effective_reward_risk` | **fee-adjusted real RR** on the taken position |
| `notional_requested_usd`, `leverage_requested` | size/leverage the strategy wanted **before cap** |
| `notional_usd`, `qty_base`, `risk_usd` | **after cap** (real position) |
| `capped`, `cap_reason` | whether/why the leverage cap fired |
| `fees_usd` | round-trip fees (`notional × fee_rate × 2`) |
| `pnl_usd`, `net_pnl_usd` | result in $ (gross / net of fees) |
| `r_multiple` | result in R = `net_pnl_usd / risk_usd` |
| `held_candles` | trade duration |

Setups, accept/reject, and reject reasons are in `state/ak_macd_local_shadow.jsonl`
(producer funnel) and `state/events.jsonl` (orchestrator `external_signal_*`).

## How to run the daily report
```bash
uv run python scripts/daily_paper_report.py            # today (UTC)
uv run python scripts/daily_paper_report.py 2026-06-20 # a specific day
```

## Known caveats / watch-items
- **Legacy open position (excluded — rule 1):** a short opened
  `2026-06-19T07:45:20Z` at ~3.5x notional ($35.5k) by the pre-cap worker. Left
  to exit naturally on its bracket; EXCLUDED from baseline metrics.
- **Selftest noise (rule 2) — ROOT CAUSE FOUND & FIXED 2026-06-19:** the
  `BUY_CANDIDATE BTCUSD @ 100.0` (stale bar `1781625600000`) came from
  `tests/test_bridge_live.py`, which builds the real `ExternalOrchestrator` with
  its default `logger=log_event`; that logger appends to the module-level live
  `EVENTS_PATH`. So **every `pytest` run** leaked synthetic signals into live
  `events.jsonl`. Fix: added `tests/conftest.py` that sets `0RUM_STATE_DIR` to
  a tempdir before any 0rum import, isolating ALL state I/O for the whole test
  session (no production code touched). Verified: a full suite run leaves the
  live `@100` count unchanged (11 → 11). The 11 historical polluted events are
  left as-is (rewriting live logs is riskier than filtering them in the report;
  they create no closed trade and are excluded by the baseline cutoff anyway).
- The long backtest (PF 0.77, expectancy −0.058R at 1x over ~1.4y) is an alert
  signal, **not** yet a decision basis. Compare it to real paper results at the
  end of the window before changing anything.

## Reference: long backtest baseline to beat
See `state/baseline_ak_macd_result.txt` (run via `scripts/baseline_ak_macd.py`).
