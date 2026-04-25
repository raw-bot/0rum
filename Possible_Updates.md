# Possible Updates

This document captures architectural improvements for `0rum` inspired by external
AI trading bot and AI trading organization case studies, but adapted to `XAUUSD`
and to the current `0rum` design.

The goal is not to copy the other system's strategy. The useful idea is its
decision funnel:

1. decide whether the market is worth trading
2. narrow the allowed conditions
3. only then let strategies compete

For `0rum`, that translates into three complementary upgrades:

1. a new context layer placed before the existing strategy and pipeline flow
2. a stricter research and governance model around how strategies are tested,
   remembered, promoted, and constrained
3. a clearer operational cadence with persistent memory and structured writeback

## Core Thesis

The strongest reusable ideas are not new indicator stacks.

For the runtime path, the strongest reusable idea is a `Tradeability Screener` for
a single asset:

- Is `XAUUSD` tradable right now?
- Is the current regime clean enough for signal generation?
- Are we too close to a macro event?
- Should we allow normal risk, reduced risk, or block trading entirely?

This would likely improve `0rum` more than adding another strategy.

For the operating model, the strongest reusable ideas are:

- a research sidecar separated from the live runtime
- explicit experiment memory
- promotion gates before anything can influence live behavior
- reviewer / approver patterns for critical changes
- hard risk controls that agents cannot rewrite or redeploy away
- recurring operating routines with explicit read -> act -> writeback discipline
- lightweight operator-facing artifacts such as daily briefs and weekly reviews

## Proposed Delta

Current high-level runtime flow:

```text
Candles -> Strategies -> Pipeline -> Approved Signals
```

Proposed runtime flow:

```text
Candles
  -> Market Context Engine
  -> Tradeability Screener
  -> Strategy Runner
  -> Signal Pipeline
  -> Risk / Execution
  -> Monitoring / Daily Brief
```

Proposed full system shape:

```text
Research Sources
  -> Research Sidecar
  -> Experiment Ledger
  -> Review / Red Team / Approval Gates
  -> Active Params / Baselines / Allowed Strategy Set

Candles
  -> Market Context Engine
  -> Tradeability Screener
  -> Strategy Runner
  -> Signal Pipeline
  -> Risk / Execution
  -> Monitoring / Daily Brief

Operational Cadence
  -> Scheduled reviews and operating routines
  -> Structured memory writeback
  -> Daily / weekly operator artifacts
```

## New Layer 0

### Name

`Layer 0: Market Context and Tradeability`

### Responsibility

Turn raw market state into an actionable gate for the rest of the system.

### Output contract

The new layer should produce a single normalized object consumed by later layers:

```python
class TradeabilityDecision(BaseModel):
    mode: Literal["ALLOW", "REDUCE", "BLOCK"]
    risk_multiplier: float
    allowed_strategies: list[str]
    session: str
    regime: str
    event_risk: str
    confidence: float
    reasons: list[str]
    valid_until: datetime
```

### Meaning

- `ALLOW`: run normally
- `REDUCE`: run with tighter filters and reduced risk
- `BLOCK`: do not open new trades

## Component 1 - Tradeability Screener

### Why

The stock bot uses a screener to reduce a large equity universe to a short watchlist.

`0rum` only trades one instrument, so the equivalent is not symbol selection. The
equivalent is context selection.

### Inputs

- latest `M15`, `H1`, `H4`, `D1` candles
- current spread if available from provider or broker
- ATR and ATR percentile
- ADX / EMA regime classification
- session classifier
- macro event calendar
- recent chop or breakout quality metrics

### Outputs

- `ALLOW`, `REDUCE`, or `BLOCK`
- risk multiplier such as `1.0`, `0.5`, or `0.0`
- per-strategy allowlist
- reasons attached for logging and Telegram summaries

### Suggested rules

Example rule families:

- `BLOCK`
  - high-impact macro event inside no-trade window
  - spread abnormally wide
  - extremely low-liquidity / dead session conditions
  - circuit breaker already active
- `REDUCE`
  - elevated volatility without clean structure
  - mixed trend signals across H1/H4
  - event has passed but post-event whipsaw window still active
- `ALLOW`
  - session is favorable
  - regime matches at least one strategy family
  - no major event conflict

### Suggested files

```text
src/context/__init__.py
src/context/session_classifier.py
src/context/tradeability_screener.py
src/context/state_models.py
```

## Component 2 - Macro Event Engine

### Why

Gold is heavily regime-sensitive around macro releases and Fed communication.

The external stock bot's strongest anecdotal results came from volatility windows.
For `0rum`, that should become explicit architecture, not operator intuition.

### Responsibilities

- ingest macro events into normalized UTC timestamps
- classify event impact
- expose pre-event and post-event guard windows
- produce a current `event_risk` state for the screener and execution layer

### Example event classes

- CPI
- NFP
- FOMC rate decision
- Powell / Fed speakers
- PCE
- ISM / PMI
- major USD labor or inflation releases

### Suggested data model

```text
macro_events
- id
- event_time
- title
- currency
- impact
- source
- forecast
- previous
- actual
- created_at
```

### Suggested files

```text
src/events/__init__.py
src/events/calendar_client.py
src/events/event_normalizer.py
src/events/event_guard.py
```

### Runtime behavior

- refresh upcoming events every 1h
- refresh more often near major releases if provider allows
- mark each 15-minute scheduler cycle with:
  - `SAFE`
  - `PRE_EVENT_LOCK`
  - `POST_EVENT_COOLDOWN`
  - `EVENT_BREAKOUT_ONLY`

## Component 3 - Daily Market Brief

### Why

The stock bot had a simple human-readable operating artifact: the daily watchlist.

`0rum` should have the equivalent for one asset: a daily context brief.

### Output

Telegram message or persisted text snapshot like:

```text
XAUUSD Daily Brief
- Bias: neutral to bullish
- H4 regime: trending_up
- Risk mode: REDUCE until 13:45 UTC
- Major events: US CPI at 12:30 UTC
- Preferred windows: London open, NY post-data continuation
- Avoid: 30 min before CPI, first 15 min after release
- Active strategies: breakout_expansion, trend_continuation
```

### Benefits

- makes the system inspectable
- reduces black-box behavior
- creates an operator audit trail

### Suggested files

```text
src/monitoring/daily_brief.py
```

## Component 4 - Operational Routine Loop

### Why

The third case study's strongest idea is not its broker setup or its model version.
It is the disciplined loop:

1. wake up on a defined cadence
2. read durable context
3. do only the task relevant to that window
4. write back the minimum state needed by the next cycle

That pattern is valuable for `0rum`, especially once monitoring, execution, and
review layers start to exist.

### Responsibility

Create explicit operating windows around the live runtime so the system does not
behave like an always-thinking black box.

### Suggested cadence for `XAUUSD`

Unlike the equity example, `0rum` should adapt cadence to gold and macro timing:

- pre-London context review
- London session readiness check
- pre-US data / pre-NY tradeability refresh
- end-of-day summary
- weekly review

The 15-minute strategy and pipeline loop can remain in place.
These routines are supervisory and interpretive, not replacements for the runtime.

### Read -> Act -> Writeback contract

Each routine should:

- read the minimum durable artifacts it needs
- perform only the scoped work for that window
- write a compact structured summary at the end

### Example outputs

- updated daily brief
- decision summary
- anomalies or failures detected
- portfolio state summary once execution exists
- suggestions for strategy or skill refinement

### Design caution

The useful idea is the memory loop, not the exact persistence mechanism used in
the case study.

For `0rum`, do not use Git commits as primary runtime memory.
Prefer database rows and controlled text artifacts generated from them.

### Suggested files

```text
src/ops/__init__.py
src/ops/routine_manager.py
src/ops/run_artifacts.py
src/ops/weekly_review.py
```

## Component 5 - Reference Baseline Strategy

### Why

The external bot benefits from simplicity. `0rum` already has a richer design, but
it still needs a boring benchmark to compare against.

Without a baseline, complexity can look better than it is.

### Proposal

Add one intentionally simple reference strategy used only for comparison:

- London breakout
or
- H1 trend + pullback continuation
or
- event breakout continuation

This strategy does not need to be traded live first. It can be used as a benchmark
inside backtesting and walk-forward research.

### Benefit

If the reference strategy performs similarly to the more complex stack, then the
real edge may be in gating and execution timing, not strategy sophistication.

## Component 6 - Research Sidecar

### Why

The most useful idea from the trading-organization case study is not "many agents."
It is the separation between:

- live runtime responsibilities
- research and experimentation responsibilities

`0rum` should not let experimental strategy generation touch the runtime path
directly.

### Responsibility

Run research, hypothesis generation, parameter exploration, and baseline comparison
outside the live scheduler path.

### Scope

The research sidecar can:

- generate candidate ideas
- compare strategy variants
- backtest on historical slices
- write findings into a durable ledger
- propose promotions

The research sidecar should not:

- place trades
- change runtime risk constraints
- activate a strategy directly in the live path

### Suggested files

```text
src/research/__init__.py
src/research/idea_runner.py
src/research/experiment_service.py
src/research/promotion_service.py
```

## Component 7 - Experiment Ledger

### Why

The trading-organization case study correctly emphasizes memory of what was tested.

Without a durable experiment ledger, `0rum` risks:

- retesting the same ideas
- forgetting why a strategy was rejected
- promoting changes based on anecdote instead of evidence

### Responsibility

Store the full history of strategy hypotheses, test runs, outcomes, and decisions.

### Minimum fields

Each experiment record should capture:

- hypothesis
- strategy family
- parameter set
- dataset or provider
- training and test windows
- backtest metrics
- walk-forward result
- Monte Carlo result
- reviewer notes
- final decision

### Benefit

This creates an institutional memory for `0rum`, not just a stream of optimizer
rows.

## Component 8 - Promotion Gates

### Why

A strong idea from the organization case study is that critical work should pass
through reviewers and approvers.

For `0rum`, this matters more for strategy promotion than for code generation.

### Promotion path

Suggested lifecycle:

```text
Idea
  -> Historical backtest
  -> Walk-forward validation
  -> Monte Carlo validation
  -> Red-team review
  -> Signal-only shadow run
  -> Limited-capital approval
  -> Normal deployment
```

### Rules

- no strategy or parameter set becomes active without a persisted decision
- failed validation stays queryable, not deleted
- shadow results should be compared with backtest expectations
- approval into live-like usage should be explicit, not implied by "best score"

### Suggested roles

- `research`
  - proposes and tests ideas
- `review`
  - checks data hygiene and backtest correctness
- `red_team`
  - tries to break assumptions
- `risk_approver`
  - decides whether a promoted variant may influence capital

## Component 9 - Immutable Risk Boundary

### Why

The single most important warning in the trading-organization case study is that
an agent should not be able to bypass the hard constraints that limit it.

For `0rum`, that means hard risk controls must live behind interfaces that runtime
agents cannot rewrite on the fly.

### Principle

Anything that protects capital should be harder to change than the strategy code
that consumes it.

### Hard boundaries

Before auto-execution is ever enabled, `0rum` should enforce:

- immutable max risk caps
- immutable deployment mode gates
- immutable circuit breaker logic
- immutable allowed broker action surface

### Design implication

The future execution path should call a narrow risk service or policy layer such as:

```text
strategy / pipeline
  -> risk policy service
  -> execution adapter
```

The caller may request an action.
The policy layer alone decides whether it is allowed.

### What must be avoided

- letting a research agent modify risk constants
- letting an execution agent redeploy the service that enforces limits
- letting "promotion" automatically imply "live approval"
- granting broad autonomous repository or infrastructure permissions to the live
  trading control plane

## Component 10 - Small-Team Operating Model

### Why

The trading-organization case study is also correct that too many agents too early
mainly add cost, slowness, and failure surface.

### Recommended bootstrap team

If `0rum` ever formalizes multiple agent roles, start with a very small set:

- `research`
- `backtesting`
- `red_team`
- `risk`
- `execution`

### Recommendation

These roles do not require five always-on agents on day one.
They can begin as five explicit responsibilities, even if implemented by one human
plus one coding agent.

## Architecture Placement

### Option A - Preferred

Insert the new context layer before `StrategyRunner`.

```text
Scheduler
  -> Refresh candles
  -> Build market context
  -> Evaluate tradeability
  -> If BLOCK: skip strategy run and send note
  -> If REDUCE / ALLOW: run strategies
  -> Pipeline ranking can still use regime and context tags
```

### Why this is preferred

- saves compute
- avoids generating signals in known-bad conditions
- makes the runtime logic easier to explain

### Option B

Let strategies always run, then filter in the pipeline.

This is less elegant because noisy signal generation still happens even when the
system already knows market conditions are poor.

## Proposed Persistence Additions

### Table `tradeability_snapshots`

```text
tradeability_snapshots
- id
- timestamp
- mode
- risk_multiplier
- session
- regime
- event_risk
- spread
- atr_value
- atr_pctile
- allowed_strategies (jsonb)
- reasons (jsonb)
- created_at
```

### Optional table `daily_market_briefs`

```text
daily_market_briefs
- id
- brief_date
- regime
- event_summary
- risk_mode
- preferred_windows
- blocked_windows
- content
- created_at
```

### Table `routine_runs`

```text
routine_runs
- id
- routine_name
- started_at
- completed_at
- status
- summary
- artifacts_written (jsonb)
- error_message
- created_at
```

### Optional table `weekly_reviews`

```text
weekly_reviews
- id
- week_start
- week_end
- regime_summary
- pnl_summary
- benchmark_summary
- risk_events
- open_questions
- content
- created_at
```

### Table `strategy_experiments`

```text
strategy_experiments
- id
- hypothesis
- strategy_name
- params (jsonb)
- provider
- instrument
- timeframe_scope
- train_start
- train_end
- test_start
- test_end
- backtest_metrics (jsonb)
- walk_forward_metrics (jsonb)
- monte_carlo_metrics (jsonb)
- status
- created_at
```

### Table `promotion_decisions`

```text
promotion_decisions
- id
- experiment_id
- decision
- stage
- decided_by
- rationale
- created_at
```

## Scheduler Changes

Add these jobs:

- `refresh_macro_events`
  - cadence: hourly
- `compute_tradeability_snapshot`
  - cadence: every 15 minutes
- `send_daily_brief`
  - cadence: once daily before London or before main operating window
- `run_pre_london_review`
  - cadence: once daily before London
- `run_pre_us_review`
  - cadence: once daily before key US window
- `run_close_review`
  - cadence: once daily after main trading window
- `run_weekly_review`
  - cadence: once weekly
- `run_research_cycle`
  - cadence: optional nightly or manual-only at first
- `evaluate_promotion_candidates`
  - cadence: manual initially, automated later with strict gates

Integration idea for existing `run_pipeline` job:

1. fetch candles
2. compute context
3. if `BLOCK`, persist snapshot and skip strategies
4. if `REDUCE` or `ALLOW`, run strategies and pass context downstream

Integration idea for supervisory routines:

1. read latest tradeability snapshot, recent signals, and recent outcomes
2. generate compact operational artifact
3. persist routine result
4. notify operator only when useful or abnormal

## Config Additions

Potential `.env` additions:

```env
MACRO_EVENTS_ENABLED=true
MACRO_PRE_EVENT_BLOCK_MINUTES=30
MACRO_POST_EVENT_COOLDOWN_MINUTES=15
TRADEABILITY_MIN_CONFIDENCE=0.60
MAX_SPREAD_MULTIPLIER=1.8
DAILY_BRIEF_ENABLED=true
OPS_ROUTINES_ENABLED=true
RUN_WRITEBACK_ENABLED=true
WEEKLY_REVIEW_ENABLED=true
RESEARCH_CYCLE_ENABLED=false
PROMOTION_REQUIRES_APPROVAL=true
```

These should remain risk controls, not optimizer parameters.

## Integration With Existing 0rum Modules

### Existing pieces to reuse

- `src/backtesting/regime_detector.py`
  - can become one input to the new screener
- `src/models/optimizer_result.py`
  - can seed parts of the future experiment ledger, but is not sufficient alone
- `src/pipeline/ranker.py`
  - can consume extra context signals
- `src/scheduler/jobs.py`
  - natural place for orchestration
- `src/monitoring/health.py`
  - can surface latest tradeability state
- future `src/monitoring/telegram_bot.py`
  - natural output path for daily briefs, close summaries, and weekly review

### Minimal invasive path

Do not rewrite the four strategies first.

Instead:

1. add context computation
2. gate strategy execution
3. add operator-facing routine artifacts and writeback
4. add experiment memory and promotion records
5. expose operator-facing brief and weekly review
6. only then adjust ranking or execution details
7. keep hard risk boundaries outside agent-controlled research paths

## Rollout Plan

### Phase 1 - Tradeability skeleton

- add `src/context/`
- define DTOs
- compute session + regime + simple `ALLOW/REDUCE/BLOCK`
- log decision each cycle

### Phase 2 - Macro event integration

- add `src/events/`
- ingest calendar data
- implement pre-event and post-event guards
- attach event reasons to tradeability decisions

### Phase 3 - Experiment ledger

- add persistent records for research runs and promotion decisions
- keep rejected ideas and failed validations queryable
- make future optimizer output promotable only through explicit state changes

### Phase 4 - Operational routine loop

- define routine scopes such as pre-London, pre-US, close, weekly review
- implement read -> act -> writeback discipline
- persist routine outputs in structured form

### Phase 5 - Promotion gates

- define approval stages
- require review for critical promotions
- prevent direct activation from raw backtest results

### Phase 6 - Daily brief and weekly review

- produce Telegram or persisted text summary
- include regime, risk mode, event windows, allowed strategies
- add a weekly review artifact with benchmark and open-issue summary

### Phase 7 - Context-aware ranking

- allow ranker to reward signals aligned with current tradeability mode
- optionally disable specific strategy families in certain conditions

### Phase 8 - Research baseline

- implement one simple reference strategy
- compare it against the current four-strategy stack

### Phase 9 - Immutable risk boundary

- design execution-facing risk policy service
- separate hard guards from research and promotion code
- ensure no autonomous path can relax live risk constraints

## Success Criteria

This update is working if:

- the bot skips more bad periods without reducing too much good exposure
- signal quality improves more than raw signal count
- drawdown clusters shrink around macro volatility spikes
- the operator can explain why the bot traded or refused to trade
- each routine leaves behind a compact, auditable writeback artifact
- every promoted strategy has an auditable validation trail
- rejected ideas remain queryable and do not get rediscovered blindly
- no research or execution path can silently bypass hard risk limits
- runtime memory does not depend on fragile Git-side effects or broad repo-write
  permissions

## Recommendation

If only one improvement is chosen, implement this first:

`Layer 0: Tradeability Screener`

It is the cleanest way to import the strongest idea from the external bot without
downgrading `0rum` into a simpler but less robust system.

If one governance improvement is chosen after that, implement this next:

`Experiment Ledger + Promotion Gates`

That pair imports the strongest organizational idea from the second case study and
fits naturally with `0rum`'s future optimizer, validation, and auto-execution path.

If one operational improvement is chosen after that, implement this next:

`Operational Routine Loop + Structured Writeback`

That imports the strongest idea from the third case study while avoiding its most
fragile parts, such as Git-based runtime memory and overly broad autonomous
permissions.
