# SharpResearch Bridge for 0rum

Date: 2026-04-27
Source project: `/Users/cube/Documents/00-code/SharpReasearch`
Target project: `/Users/cube/Documents/00-code/0rum`
Status: planning/research bridge

## Purpose

This bridge translates the useful parts of the SharpResearch / SharpEducation analysis into 0rum's current roadmap.

The goal is not to import SharpResearch strategies into 0rum. The goal is to transfer research discipline:

- immutable evidence
- strict anti-lookahead checks
- train/test and walk-forward separation
- risk/reward skepticism
- paper/live track record design
- clear rejection of attractive but weak backtests

0rum is currently a pure technical XAUUSD bot. Its project constraints explicitly exclude ML, neural networks, RSI/MACD, multi-asset, dashboards, and external data APIs for v1. This bridge respects those constraints.

## SharpResearch Takeaway In One Line

SharpResearch is most useful for 0rum as an anti-delusion layer: every signal must prove that it was generated before the outcome, survived validation outside the training window, passed risk gates, and can be audited later.

## What Transfers To 0rum Now

### 1. Evidence Before Complexity

SharpResearch's strongest product lesson is not the model. It is the public evidence shape:

- signals are timestamped before evaluation
- historical calls remain visible
- performance is grouped and benchmarked
- raw artifacts can be audited
- live/paper records are separated from backtests

0rum should apply this in Phase 7 Signal Mode.

Concrete 0rum mapping:

- `ApprovedSignal` should become an immutable paper/live signal record.
- Theoretical trades should be linked back to the exact approved signal.
- Daily summaries should be reconstructable from database records, not only Telegram messages.
- Any later report should distinguish backtest, optimizer OOS, signal-mode paper, and real execution.

### 2. Anti-Lookahead As A Hard Gate

SharpEducation repeatedly shows strategies looking good until signals are shifted correctly.

0rum already uses walk-forward validation, but the bridge requirement is broader: every strategy and risk gate should have an explicit data availability story.

Concrete 0rum mapping:

- Every generated signal should persist `generated_at`, `source_candle_timeframe`, and the last candle close time used.
- Strategy tests should include at least one assertion that the current/incomplete candle is not consumed as if closed.
- Backtest and live signal generation should use the same candle-availability contract.
- Any report should state whether entries are next-bar, same-bar, close-to-close, or delayed-confirmation.

### 3. Risk/Reward Is Not Edge

SharpEducation's risk/reward work is directly relevant to Phase 6.

The useful lesson: a 1:2 or 1:3 target does not create expectancy. Expected value comes from hit probability, path behavior, volatility, fees/spread, and signal quality.

Concrete 0rum mapping:

- Risk gates should not treat high R:R as automatically good.
- Ranker score can include R:R, but reports must show realized hit rate and expectancy by strategy.
- Theoretical trade tracking should store planned R:R and realized R multiple.
- Phase 7 summaries should include average R, win rate, profit factor, max adverse excursion if available, and count.

### 4. Failed Hypotheses Are Assets

SharpEducation keeps showing that plausible ideas fail. That is useful.

Concrete 0rum mapping:

- When a strategy fails optimizer validation, keep the reason.
- When a risk gate blocks a signal, keep the reason.
- When a candidate signal is filtered by dedup/conflict/quota, keep the reason.
- When a strategy remains skipped because no active optimizer row exists, surface that explicitly.

This protects the project from silently forgetting why an idea was rejected.

### 5. Train/Test And Walk-Forward Must Stay Separate From Signal Mode

SharpResearch separates historical validation from live evidence. 0rum should preserve the same boundary.

Concrete 0rum mapping:

- Phase 5 optimizer output is not live performance.
- Phase 7 signal-mode theoretical trades are the first practical forward record.
- Phase 8 auto mode should not start until Phase 7 has enough closed theoretical trades to satisfy the existing transition gate.

0rum already states: minimum 4 weeks signal mode, win rate > 55%, PF > 1.3, WFE avg > 50%.

Bridge addition:

- Also require enough closed trades to make the metrics meaningful.
- If sample size is too small after 4 weeks, extend signal mode rather than forcing auto mode.

## What Does Not Transfer To 0rum v1

### No ML Import

SharpEducation's regression/logistic regression sections are useful as validation culture, not as direct implementation.

Do not add:

- sklearn classifiers
- neural networks
- SharpEdge-like stock rating buckets
- logistic regression signal filters

Reason: 0rum's current project scope explicitly excludes ML/neural networks and prioritizes pure technical XAUUSD strategies.

### No External Macro Layer For v1

SharpEducation's FRED/macro episodes are intellectually useful, but 0rum v1 excludes external data APIs beyond market-data/execution providers.

Do not add now:

- FRED
- CPI
- Fed funds
- TIPS
- yield curve data
- macro regime filters

Future note:

Macro context may be a v2 research direction, but it should not contaminate v1 validation.

### No Dashboard Requirement For v1

SharpResearch's public evidence dashboard is a good product lesson, but 0rum v1 explicitly says no frontend/dashboard.

Do now:

- store evidence in DB
- export CLI/log/Telegram summaries
- make artifacts easy to query

Do later:

- dashboard
- public track-record page
- downloadable research bundle

## Phase Mapping

### Phase 6: Risk Management

Bridge requirements:

- Preserve blocked signal reasons.
- Store risk-gate decision details, not just pass/fail.
- Treat R:R as one input, not proof of edge.
- Connect risk decisions to theoretical trade outcomes later.
- Make circuit breaker state auditable after restart.

Suggested Phase 6 additions:

- `risk_decision_reason`
- `risk_gate_snapshot`
- planned risk amount
- planned R:R
- planned position size
- rejection metadata

### Phase 7: Signal Mode & Monitoring

Bridge requirements:

- Signal mode is 0rum's first true forward-test record.
- Every Telegram signal should correspond to an immutable DB row.
- Theoretical trade lifecycle should produce an audit trail.
- Daily summary should include evidence metrics, not just event counts.

Suggested Phase 7 additions:

- closed theoretical trade count
- win rate by strategy
- profit factor by strategy
- average R by strategy
- blocked-signal count by reason
- approved vs rejected signal funnel
- signal-mode age and auto-mode eligibility status

### Phase 8: Auto Mode

Bridge requirements:

- Auto mode must inherit the same evidence IDs from signal mode.
- Auto execution should not create a separate, incomparable record.
- Real execution should be compared against theoretical paper execution.

Suggested Phase 8 additions:

- paper entry vs broker fill
- slippage
- spread/cost
- partial close execution result
- trailing stop modification log
- realized R after execution costs

## Suggested Research Artifact For 0rum

Add a planning/research artifact called:

`/.planning/research/SHARPRESEARCH-BRIDGE.md`

This file should remain a reference note, not a phase plan. Future GSD phases can promote its recommendations into concrete plans.

## Minimal 0rum Evidence Schema

0rum does not need a full SharpEdge-style public spreadsheet now. It does need the underlying data shape.

Minimum evidence fields:

- signal ID
- strategy name
- strategy version or parameter row ID
- optimizer result ID if applicable
- timeframe
- generated timestamp
- last closed candle timestamp used
- direction
- entry, SL, TP1, TP2
- confidence
- ranker score
- risk gate status
- risk gate reasons
- planned R:R
- position size suggestion
- theoretical trade ID
- theoretical close reason
- realized R
- benchmark/context return over holding window if relevant

## Validation Checklist Imported From SharpResearch

Before a strategy or signal family is treated as trustworthy:

- signals are generated only from available candles
- parameters were selected before the evaluated period
- walk-forward windows are separated from signal-mode evidence
- every blocked/rejected signal has a reason
- sample count is shown next to win rate and PF
- R:R is reported alongside realized expectancy
- single favorable backtests are not treated as proof
- paper/live results are never blended with historical backtests

## Recommended Priority

P0 for current 0rum work:

1. Preserve risk-gate decision details in Phase 6.
2. Ensure ApprovedSignals and theoretical trades form an immutable audit chain in Phase 7.
3. Add signal-mode eligibility reporting before Phase 8.

P1:

1. Add evidence export commands after Phase 7.
2. Add a simple research ledger for failed optimizer/strategy hypotheses.
3. Add a negative-control baseline report if strategy quality becomes unclear.

P2:

1. Revisit macro/context data only after v1 proves signal mode.
2. Consider a dashboard only after the DB evidence trail is reliable.

## Bottom Line

The bridge from SharpResearch to 0rum is methodological, not strategic.

0rum should not become SharpEdge. It should borrow SharpResearch's discipline:

```text
timestamp the signal
  -> lock the evidence
  -> enforce risk gates
  -> track theoretical outcome
  -> separate backtest from forward record
  -> require enough closed trades before auto mode
```

That is the useful bridge.

