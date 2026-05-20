# Phase 5: Backtesting & Validation — Context

**Gathered:** 2026-04-22
**Status:** Ready for planning
**Source:** User brief (PRD-equivalent) — planning session 2026-04-22

<domain>
## Phase Boundary

Phase 5 delivers the walk-forward optimizer, Monte Carlo validation, and parameter activation pipeline that makes strategy parameters verifiable before any live signal is generated. It does NOT deliver live signal emission (Phase 7) or risk gates (Phase 6).

The central unresolved question for this phase is the **data strategy**: the optimizer requires historical candle windows (6m train / 2m OOS) that exceed what retired-provider warm-up delivers. This question must be structured as a decision gate — not assumed away.

What this phase is NOT:
- A live data provider switch (data provider architecture stays as-is)
- A risk management system (Phase 6)
- A signal delivery system (Phase 7)
- A refactor of the ingestion layer

</domain>

<decisions>
## Implementation Decisions

### Locked: Technical Objectives

Phase 5 must deliver TWO separable things:
1. **Framework technique exécutable** — the optimizer/walk-forward/Monte Carlo code, wired into the scheduler, reading from DB, writing to `optimizer_results`
2. **Validation statistique complète** — WFE > 50% confirmed against real 6m/2m XAUUSD windows, multi-window test passing, Monte Carlo passing

These two objectives are NOT the same delivery. The framework can be built and verified without full historical data. The statistical validation requires sufficient data. Plans must reflect this distinction.

### Locked: Walk-Forward Parameters

- LHS sampling: exactly 100 combinations per strategy
- Training window: `wf_train_months = 6` months (from config)
- OOS window: `wf_test_months = 2` months (from config)
- WFE gate: `wfe_minimum = 0.50` (from config, 50%)
- Multi-window test: 2 of 3 OOS windows must be profitable before activating params
- Optimizer cadence: every 24h via APScheduler (`optimizer_interval_hours = 24`)

### Locked: Monte Carlo Parameters

- Simulations: 1000
- P95 drawdown ≤ 2× historical drawdown
- P5 profit factor > 1.0

### Locked: DB Output

- Best params written to `optimizer_results` with `is_active = TRUE`
- Strategy failing WFE gate retains PREVIOUS active params (not deactivated)
- WFE score stored per result (used by signal ranker's 20% weight)

### Locked: Data Strategy Constraints (what NOT to assume)

- **Binance/PAXG is NOT acceptable** for Phase 5 optimization or validation
- **retired-provider warm-up bars alone are NOT sufficient** for a 6-month training window: retired-provider provides 300 M15 bars (~3 days), 250 H1 bars (~10 days), 80 H4 bars (~13 days), 60 D1 bars (60 days). A 6-month train window requires ~175 D1 bars, ~1050 H4 bars, ~4380 H1 bars — far beyond current retired-provider limits
- **retired-provider historical API MAY support larger requests** than retired-provider warm-up, but this has not been validated for optimizer-scale volume. Only one minimal test (max=1) was confirmed working on 2026-04-22
- Do NOT assume retired provider can automatically supply 6m/2m windows without rate-limit or quota risk assessment

### Locked: Architecture Constraints

- Provider/execution separation MUST be preserved — no coupling
- No major ingestion refactor — optimizer reads from DB candles, not directly from provider
- Walk-forward reads candles already in the DB; it is NOT an ingestion concern
- New modules go in `src/backtesting/` (optimizer.py, walk_forward.py, monte_carlo.py — per CLAUDE.md "Not implemented yet")

### Decision Gate: Historical Bootstrap Source (locked by 05-04)

retired provider is insufficient for Phase 5 historical validation: the account is no longer
available and prior `/prices` probes failed even for 1-bar historical requests.
OANDA_RETIRED remains proscribed from earlier testing. retired bootstrap archive XAUUSD M1 Generic ASCII
archives are the selected offline bootstrap source for Phase 5 only.

Runtime provider selection remains deferred. Dukascopy is the likely candidate
to validate before Phase 7 signal mode, but it is not needed to complete Phase 5
offline walk-forward validation.

### Claude's Discretion

- Internal implementation of LHS sampling (scipy.stats.qmc.LatinHypercube recommended, already in stack)
- Walk-forward evaluation metric (profit factor, Sharpe, or custom — must be defined per strategy)
- Monte Carlo simulation method (bootstrap resampling vs parametric — bootstrap is standard)
- Whether to use vectorized backtesting (vectorbt/backtesting.py) or hand-roll — given stack constraints (numpy/pandas already present), hand-roll is cleanest
- Exact DB schema for `optimizer_results` extension if fields need adding
- Whether framework test uses synthetic candle fixtures or real minimal DB snapshot

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project constraints
- `.planning/PROJECT.md` — core constraints (3 params max, WFE > 50% gate, provider split)
- `CLAUDE.md` — current scope, invariants, missing modules (src/backtesting/*), safe modification rules
- `src/config.py` — all optimizer config values (wf_train_months=6, wf_test_months=2, lhs_combos=100, wfe_minimum=0.50)

### Existing implementation to understand first
- `src/strategies/runner.py` — how strategies are called and how params are loaded from DB (this is what the optimizer feeds)
- `src/models/` — ORM models, especially `optimizer_results` table if it exists
- `src/ingestion/candle_fetcher.py` — provider runtime ingestion, intentionally unchanged by retired bootstrap archive bootstrap
- `src/backtesting/historical_loader.py` — retired bootstrap archive offline bootstrap parser/resampler/import helper
- `scripts/retired_bootstrap_archive_phase5_loader.py` — read-only QA and explicit import command
- `src/scheduler/jobs.py` — how to add the optimizer job (create_scheduler pattern)

### Planning references
- `.planning/ROADMAP.md` — Phase 5 success criteria (all 6 must be met)
- `.planning/REQUIREMENTS.md` — OPTIM-01 through OPTIM-05

</canonical_refs>

<specifics>
## Specific Requirements from Brief

### Phase 5 success criteria (from ROADMAP.md — all 6 must be TRUE):
1. LHS optimizer generates exactly 100 parameter combinations per strategy and evaluates each against the 6-month training window
2. Only parameter sets with WFE > 50% are written to `optimizer_results` with `is_active = TRUE`
3. A strategy that fails the WFE gate retains its previous active parameters rather than being deactivated
4. Multi-window test confirms parameters are profitable in at least 2 of 3 OOS windows before activation
5. Monte Carlo validation (1000 simulations) confirms P95 drawdown ≤ 2× historical and P5 profit factor > 1.0
6. The optimizer runs automatically every 24 hours via the APScheduler job

### Data reality check (key facts for planner):
- retired-provider warm-up provides: 300 M15 bars (~3 days), 250 H1 bars (~10 days), 80 H4 bars (~13 days), 60 D1 bars (60 trading days ≈ 3 months)
- A 6-month training window at D1 = ~175 bars — retired-provider D1 warmup (60 bars) covers ~34% of this
- A 6-month training window at H1 = ~4380 bars — retired-provider H1 warmup (250 bars) covers ~6% of this
- retired provider daily incremental refreshes add only ~10 bars per timeframe per scheduled run
- At current retired-provider rates: reaching 6m of H1 data takes ~4380/24 ≈ 6 months of continuous operation
- Conclusion: Chemin A (runtime accumulation) cannot produce full WFE validation for many months; retired bootstrap archive offline bootstrap is the current path to immediate Phase 5 validation

### User preference (from brief):
> "Je préfère un plan honnête et exécutable plutôt qu'un plan 'idéal' mais irréaliste. Si la validation complète ne peut pas raisonnablement être le premier run, dis-le clairement et structure la Phase 5 en conséquence."

This means: the plan MUST acknowledge the data gap honestly. It MUST NOT promise immediate WFE validation if the data isn't there.

</specifics>

<deferred>
## Deferred Ideas

- Auto-switching between providers mid-optimization run
- Storing full simulation trace data (only summary metrics needed for now)
- Optimization of risk parameters (explicitly forbidden — all risk params are env vars)
- Structural parameter optimization (EMA50/200, swing order=10 — explicitly forbidden)
- Cross-strategy portfolio optimization
- Regime-conditional walk-forward splits

</deferred>

---

*Phase: 05-backtesting-validation*
*Context gathered: 2026-04-22 from detailed user brief*
