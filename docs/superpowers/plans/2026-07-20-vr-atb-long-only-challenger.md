# VR-ATB Long-Only Challenger Implementation Plan

> **For agentic workers:** Execute this plan task by task with TDD. This plan authorizes offline code, tests, replay artefacts, and documentation only. It does not authorize editing runtime state, enabling entries, placing orders, or restarting any live process.

**Goal:** Add a deterministic, inactive BTC/USDT M15/H1 VR-ATB challenger that combines a causal Donchian 20/10 breakout, a completed-H1 KAMA trend gate, an M15 ATR expansion gate, and a 2×ATR protective stop; then decide with locked chronological evidence whether it adds value beside AK-MACD and UTBot.

**Architecture:** Implement a new pure `VrAtbEngine` that composes the existing `DonchianEngine` and reads the existing multi-timeframe `StrategyContext`. Reuse the existing KAMA calculation without activating the failed KAMA-squeeze challenger. Keep all work in this plan outside the strategy registry and active portfolio. Dedicated research-only replay modules perform next-bar execution, costs, conservative stop simulation, ablations, chronological evaluation, and incremental portfolio comparison. Runtime parity, registration, and shadow activation belong to a later plan requiring explicit user approval.

**Tech Stack:** Python 3, dataclasses, existing `StrategyEngine` contract, `unittest`/pytest, replay harness snapshots, GitNexus impact/change analysis.

## Frozen executable contract

The first experiment must not change these values after results are visible:

| Concern | Frozen V1 rule |
|---|---|
| Instrument | `BTC/USDT`, spot semantics, long-only |
| Primary timeframe | M15 closed candles |
| Macro timeframe | H1 closed candles knowable at the evaluated M15 close |
| Donchian entry | current M15 close strictly above the highest of the prior 20 M15 closes |
| Donchian exit | current M15 close strictly below the lowest of the prior 10 M15 closes |
| KAMA | existing KAMA implementation with `efficiency_length=32`, `fast_length=2`, `slow_length=50` |
| KAMA entry gate | last knowable H1 close strictly above KAMA and KAMA strictly higher than 3 completed H1 bars earlier |
| Volatility gate | Wilder `ATR(14) / ATR(100) > 1.0` on M15 |
| Initial stop | fill price minus `2.0 × ATR(14)` from the signal candle |
| Take profit | none |
| Signal exit | Donchian 10 close break; never blocked by KAMA or volatility gates |
| Research fill | next M15 open after a confirmed signal |
| Stop simulation | conservative M15 gap/stop ordering: adverse open first, then low against the frozen stop, then close-based channel decision |
| Costs | existing round-trip fee convention plus 5 bps slippage per fill for every verdict; 0, 2, and 10 bps are descriptive stress only |
| Candidate risk | 0.5% requested stop risk, subject to existing 3% BTC and 5% total portfolio caps |
| Direction | `LONG`, `EXIT`, or no signal; never `SHORT` |

The phrase “KAMA flattens” is intentionally excluded from V1 because it is not an executable rule. A KAMA-based exit would be a separately pre-registered future experiment, not a post-result repair.

## Non-goals and safety boundaries

- Do not modify the existing `DonchianEngine`, KAMA-squeeze entry/exit rules, UTBot, AK-MACD, broker, risk caps, dashboard, or scheduler.
- Do not register VR-ATB or add it to `config/portfolio.yaml` during the research gate.
- Normal `load_engine("vr_atb")` lookup must continue to fail throughout this plan.
- Do not edit `state/portfolio.yaml`, positions, fills, equity, ledgers, heartbeats, or runtime YAML.
- Do not touch or clean the unrelated dirty worktree, including the experimental pairs-trading files.
- Do not restart worker, watcher, dashboard, producer, or LaunchAgent.
- Do not tune parameters after reading holdout results.
- Do not describe a closed-candle exit as instantaneous; research fills occur at the next executable price.

---

### Task 1: Record the decision and freeze the hypothesis

**Files:**
- Create: `docs/decisions/ADR-006-vr-atb-long-only-challenger.md`

- [ ] Record the frozen executable contract above and state that the candidate is offline-only.
- [ ] Explain why VR-ATB is a new hypothesis rather than a promotion of KAMA squeeze: the prior KAMA challenger failed its untouched holdout.
- [ ] Explain why Donchian 20/10 uses prior closes, matching the native engine, rather than silently switching to highs/lows.
- [ ] Explain why the volatility threshold is `> 1.0`: “expansion” requires short ATR to exceed long ATR; the earlier `> 0.9` wording could admit contraction.
- [ ] Record rejected alternatives: H4 instead of H1, EMA instead of KAMA, ADX/TRIX stacking, KAMA-flat exit, short positions, fixed TP, and live-first activation.
- [ ] Verify the ADR contains no claim that trend filters, KAMA, or volatility expansion guarantee profitability.

**Verification:** read the ADR against this plan and confirm every table row is represented exactly once.

### Task 2: Implement pure VR-ATB state and signals with TDD

**Files:**
- Create: `orum/strategies/vr_atb.py`
- Create: `tests/test_vr_atb.py`

- [ ] Before editing, run GitNexus context/impact for any existing symbol that would be imported or modified; no existing symbol should require modification in this task.
- [ ] Write failing tests proving the engine is long-only and requires `15m` plus `1h` data.
- [ ] Write failing tests for Wilder ATR14/ATR100, zero/insufficient volatility history, strict `> 1.0`, and finite-value rejection.
- [ ] Write failing tests proving H1 alignment excludes an H1 candle that was not closed at the evaluated M15 decision time.
- [ ] Define the causal boundary exactly: an H1 row is eligible only when `bar_open_ts + 3_600_000 <= evaluated_m15_open_ts + 900_000`.
- [ ] Add exact-hour-boundary and future-mutation-invariance tests: changing any not-yet-eligible H1 or future M15 value must not change the current verdict.
- [ ] Write failing tests for the exact H1 KAMA gate: close above KAMA and three-bar positive KAMA slope.
- [ ] Write failing tests proving Donchian entry uses the prior 20 closes and Donchian exit uses the prior 10 closes.
- [ ] Write a failing test proving an `EXIT` is emitted even when KAMA/volatility entry gates are false.
- [ ] Write a failing test proving a qualified `LONG` carries a suggested 2×ATR stop and audit metadata for all three gates.
- [ ] Implement `VrAtbParams` with the frozen values and reject invalid overrides rather than silently changing the hypothesis.
- [ ] Compose `DonchianEngine` for channel decisions; pass only causally truncated H1 rows to `compute_kama_state`; keep ATR and H1 alignment pure and deterministic.
- [ ] Do not add `ENGINE_CLASS` and do not edit `orum/strategies/__init__.py` in the research gate.
- [ ] Add a regression test proving normal registry lookup for `vr_atb` raises `StrategyEngineError`.
- [ ] Run `uv run python -m unittest tests.test_vr_atb -v` until green.

### Task 3: Build a causal offline replay and ablation harness

**Files:**
- Create: `scripts/replay_harness/vr_atb.py`
- Create: `scripts/backtest_vr_atb.py`
- Create: `tests/test_vr_atb_backtest.py`

- [ ] Write failing tests proving a signal at M15 close can only fill at the next M15 open.
- [ ] Write failing tests proving signal-candle ATR is frozen for initial risk distance and gaps are reported separately.
- [ ] Write failing tests for one-position-at-a-time behavior, no same-bar re-entry, and complete trade blocking while a position remains open.
- [ ] Write failing tests for conservative stop/channel ordering: if the M15 open gaps below the long stop, fill at that worse open; otherwise a low touching the stop fills at the stop; only a surviving position may evaluate the close-based channel exit for next-open execution.
- [ ] Implement three pre-registered streams over identical candles and fills:
  - `D0`: Donchian 20/10 only.
  - `D1`: Donchian 20/10 + completed-H1 KAMA gate.
  - `D2`: Donchian 20/10 + H1 KAMA + M15 ATR expansion (the VR-ATB candidate).
- [ ] Report trades, win rate, profit factor, expectancy in R, net R, maximum drawdown in R, exposure, average hold, turnover, and yearly/fold results.
- [ ] Apply the existing fee convention and slippage stress at 0/2/5/10 bps without changing signals.
- [ ] Use existing fees plus 5 bps per fill for every expectancy, PF, net R, drawdown, ablation, isolated gate, and portfolio gate. Label 0/2/10-bps output descriptive and forbid it from changing a verdict.
- [ ] Do not require 1m data for the frozen, non-ratcheting protective stop.
- [ ] Before reading prices or calculating indicators, build a timestamp-only completeness mask. Require unique, strictly increasing, contiguous M15 opens and correctly aligned completed H1 opens over the entire frozen window.
- [ ] If any required M15/H1 bar is missing, duplicated, misaligned, or lacks the next M15 open needed for a queued fill, stop the qualification run with `INSUFFICIENT_EVIDENCE`. Do not exclude the interval, bridge the gap, carry indicator state across it, or invent a fill for an open trade.
- [ ] Ensure the harness accepts an explicit dataset/run identifier and records time range plus content hashes.
- [ ] Run `uv run python -m unittest tests.test_vr_atb_backtest -v` until green.

### Task 4: Run the locked chronological falsification study

**Files:**
- Create from output: `backtests/runs/vr_atb_v1/manifest.json`
- Create from output: `backtests/runs/vr_atb_v1/report.json`
- Create from output: `backtests/runs/vr_atb_v1/trades.jsonl`
- Create: `docs/research/vr-atb-long-only-2026-07-20.md`

- [ ] Use only point-in-time closed BTC/USDT M15 and H1 snapshots already present in the replay dataset; obtain separate approval before any network fetch.
- [ ] Before calculating any signal or metric, verify price-blind complete synchronized coverage from `2024-01-01T00:00:00Z` through `2026-07-15T00:00:00Z`, write the completeness verdict and immutable content hashes to the manifest, and stop if the window is incomplete. Any replacement window requires a documented plan amendment before results are computed.
- [ ] Freeze these evaluation folds before inspecting results:
  - development fold A: `2025-01-01T00:00:00Z` through `2025-06-30T23:59:59Z`;
  - development fold B: `2025-07-01T00:00:00Z` through `2025-12-31T23:59:59Z`;
  - terminal untouched fold C: `2026-01-01T00:00:00Z` through `2026-07-15T00:00:00Z`.
  The 2024 data are warmup/context only. Do not inspect fold C until code, hashes, metrics, and development-fold decisions are frozen.
- [ ] Keep D0, D1, and D2 parameters identical across every fold; no fold-specific optimization.
- [ ] Require at least 30 completed D2 trades across folds A-C and at least 8 in terminal fold C for a non-provisional verdict. If counts are lower, record `INSUFFICIENT_EVIDENCE`; do not extend the window or loosen rules after results are visible.
- [ ] Remove parameter-neighborhood behavior from every promotion decision. An optional neighborhood appendix may be generated only after the terminal verdict and may not change it or select new parameters.
- [ ] Reject the “robust” label if results depend on 0-bps execution.
- [ ] Write the research report before proposing registry or portfolio changes.

**Isolated promotion gate:** D2 may proceed to portfolio comparison only if all are true:

1. Across development folds A-B, D1 expectancy under existing fees plus 5 bps per fill is strictly greater than D0, and D2 expectancy is strictly greater than D1; otherwise the claimed filters have not added value.
2. In terminal fold C, D2 expectancy is strictly positive after fees and 5-bps-per-fill slippage and D2 net R exceeds D0 net R.
3. Aggregate folds A-C D2 PF is above 1.0, and D2 remains net-positive after removing its single best trade.
4. Maximum drawdown, yearly results, trade counts, and excluded-data coverage are reported without omission.
5. Causal, alignment, stop-ordering, future-mutation, and reproducibility tests all pass.

Failure closes the experiment with an honest negative report; it does not trigger parameter tuning.

### Task 5: Measure incremental value beside AK-MACD and UTBot

**Files:**
- Create: `scripts/replay_harness/vr_atb_portfolio.py`
- Create: `tests/test_vr_atb_portfolio.py`
- Create: `backtests/configs/research_vr_atb_trio.yaml`

- [ ] Keep the portfolio comparison on a dedicated research-only code path that accepts a `VrAtbEngine` instance directly. Do not modify `runtime_replay.py`, the production registry, or normal runtime configuration parsing.
- [ ] Add a failing test proving the research driver receives synchronized M15/H1 closed candles, never consumes a future H1 close, and cannot be selected through normal `load_engine` configuration.
- [ ] Keep production-equivalent candidate risk at 0.5%, current BTC stop-risk cap at 3%, and total stop-risk cap at 5%.
- [ ] Put VR-ATB last in static merit order (`UTBot`, `AK-MACD`, `VR-ATB`) so the challenger cannot displace validated sleeves merely by being new.
- [ ] Freeze the research portfolio event order at every M15 bar:
  1. at bar open, execute queued signal exits before queued entries;
  2. apply gap-through-stop exits for positions carried into the open;
  3. mark post-exit/pre-entry equity at that open;
  4. auction all queued simultaneous entries in static merit order using that single equity snapshot;
  5. release risk from same-open exits before the auction, but never reuse risk from an intrabar stop until the next M15 open;
  6. during the bar, apply frozen protective stops against the adverse low;
  7. at bar close, mark equity, evaluate only fully closed inputs, and queue signal actions for the next open.
- [ ] Add failing tests for exits-before-entries, simultaneous-signal merit ordering, one shared sizing snapshot, same-open cap release, delayed intrabar cap release, and deterministic replay under reordered input dictionaries.
- [ ] Compare baseline `AK + UTBot` against `AK + UTBot + VR-ATB` over identical cycle timestamps and execution stress.
- [ ] Reprice every baseline and trio entry with the same next-open convention before comparison; also publish a separate descriptive signal-close parity table, never mixed into the promotion gate.
- [ ] Start baseline and trio from identical equity. Force-close any remaining open position at the terminal close using the decisive fee + 5-bps convention so final metrics contain no unpriced inventory.
- [ ] Define candidate contribution as VR-ATB realized net PnL including entry/exit fees and the terminal close. Define net return as `(final_equity / starting_equity) - 1`. Define maximum drawdown from the M15 close-to-close marked equity curve after fees. Define net/drawdown as net return divided by absolute maximum drawdown, with zero drawdown reported separately rather than coerced.
- [ ] Report incremental net PnL, total net return, drawdown, net/drawdown ratio, exposure overlap, signal proximity, daily PnL correlation, risk-cap refusals, candidate attribution, and every baseline trade denied or resized because candidate exposure consumed shared risk.
- [ ] Emit an event ledger for signals, queued actions, fills, fees, equity snapshots, requested/granted risk, cap refusals, and exits. For every displaced baseline trade, attach its baseline-run realized counterfactual PnL under identical execution assumptions.
- [ ] Reconcile the final equity difference to the cent: `trio - baseline = VR-ATB attribution + baseline fill/sizing changes + fee/equity-path effects`. A reconciliation failure invalidates the portfolio verdict.
- [ ] Verify candidate exits close only its own sleeve and never another BTC strategy’s position.
- [ ] Run leave-one-best-trade-out and leave-one-fold-out comparisons for both baseline and trio.

**Portfolio research gate:** the candidate may be labelled `RESEARCH_QUALIFIED` only if all are true:

1. VR-ATB candidate contribution, as defined above, is positive after existing fees plus 5 bps per fill.
2. Capped trio total net return exceeds capped AK+UTBot total net return under the same next-open stress.
3. The trio’s net/drawdown ratio improves over the AK+UTBot baseline.
4. Trio maximum drawdown increases by no more than 2 percentage points.
5. Existing symbol/total risk caps are never bypassed, and all suppressed baseline trades are attributed.
6. Improvement survives each leave-one-fold-out comparison and the leave-one-best-trade-out comparison.

### Task 6: Stop at research qualification and open a separate runtime-parity decision

**Files:**
- Create only if research gates pass: `docs/research/vr-atb-runtime-parity-handoff.md`

- [ ] If either research gate fails, write the negative report and stop with `REJECTED` or `INSUFFICIENT_EVIDENCE`.
- [ ] If both gates pass, label the result `RESEARCH_QUALIFIED`, not shadow-qualified or paper-qualified.
- [ ] Document the unresolved semantic difference: research fills at next open, while current `PaperEngine` fills at signal close.
- [ ] Propose a separate plan that either adds verified next-open paper execution or re-falsifies VR-ATB under current signal-close semantics before any registry change.
- [ ] Obtain explicit user approval before that later plan modifies the registry, paper engine, portfolio configuration, or processes.

This plan never registers VR-ATB and never qualifies it for shadow or paper execution.

### Task 7: Final verification and rollback audit

**Files:**
- Verify all files above; do not modify runtime state.

- [ ] Run focused tests:
  `uv run python -m unittest tests.test_vr_atb tests.test_vr_atb_backtest tests.test_vr_atb_portfolio tests.test_donchian_engine tests.test_kama_squeeze tests.test_utbot_mtf tests.test_strategy_registry tests.test_paper_engine tests.test_paper_broker -v`
- [ ] Run the complete suite: `uv run python -m unittest discover -s tests`.
- [ ] Run GitNexus `detect_changes` and confirm only expected research/strategy/test/documentation flows changed.
- [ ] Review `git diff --check` and `git status --short`; preserve all unrelated dirty files.
- [ ] Verify `config/portfolio.yaml`, `state/portfolio.yaml`, positions, fills, ledgers, and heartbeat files are unchanged by the work.
- [ ] Confirm no worker, watcher, dashboard, producer, or LaunchAgent was restarted.

## Rollback strategy

All work is additive and inactive. Rollback consists of removing only the new VR-ATB module, tests, dedicated research harnesses, research config, reports, handoff, and ADR created by this plan; no shared replay module, runtime registry, portfolio configuration, or persisted trading state requires migration.

## Final deliverable

The implementation is complete only when it produces one of three auditable outcomes:

- **REJECTED:** causal implementation is verified, the locked candidate fails an isolated or portfolio gate, and the negative report is preserved;
- **INSUFFICIENT EVIDENCE:** synchronized coverage or frozen minimum trade counts are not met, without changing the rules; or
- **RESEARCH_QUALIFIED:** all isolated and portfolio gates pass, the evidence and hashes are recorded, and a separate runtime-parity plan is required.

Neither outcome authorizes live trading.
