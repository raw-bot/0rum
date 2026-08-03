# ADR-006 — Dynamic position exits for the unified paper portfolio

- Status: proposed, implemented in isolated branch; runtime activation pending replay
- Date: 2026-07-22
- Scope: `scripts/run_paper_portfolio.py` → `PaperEngine` → `PaperBroker`

## Context

The active paper portfolio checks each open tranche against a stop-loss and a
take-profit frozen at entry.  It does not reduce giveback after a favorable
move and AK emits no native exit signal.  A profitable position can therefore
return to its initial stop while the runner continues to receive closed M15
candles.

This path is not the legacy `orum/loop.py` worker.  The portfolio runner is
invoked every 15 minutes and may own several tranches for one strategy.

## Decision

Add an optional, per-position dynamic policy.  Missing or `disabled` config
does not create policy state, does not fetch extra data, and preserves the
legacy serialized position and fill shapes.

The first policy is `ak_mfe_ssl_v1`:

- input is closed M15 data only;
- official R is the frozen entry `atr_risk`, matching ADR-005 sizing and fill
  accounting;
- protection arms after `activation_r`;
- its profit floor is `max(floor_r, mfe_r - giveback_r)`;
- the candidate stop may tighten to the EMA/ATR SSL lower band;
- a stop is monotone and remains distinct from the immutable initial stop;
- a stop computed from candle N becomes eligible on candle N+1;
- if that newly computed stop is already above N's close, the result is a
  market-close decision at N's close, not a retroactive stop fill;
- an armed close through the SSL band may emit an invalidation decision.

`PositionExitState` is advanced by a pure function returning the entire next
state plus a typed decision.  Evaluation is per tranche.  There is no
strategy-wide close in v1.

Policies are frozen when a new position opens.  Existing positions do not
adopt a policy automatically, and changing YAML does not rewrite an open
position.  `observe` state never becomes executable by a later YAML switch.

## Ordering and prices

For each pending monitor candle:

1. evaluate the previously active effective stop;
2. evaluate TP (stop-first on collisions);
3. advance MFE/SSL state using history available as of that candle;
4. act on a close-candle invalidation when configured;
5. persist the candidate stop for the next candle.

For a long, the effective stop is the greater of the initial stop and active
dynamic stop.  A current-candle dynamic stop fills at the worse of its frozen
level or candle open.  When recovery discovers an older dynamic trigger, the
engine closes at the latest available close and records
`recovered_after_gap=true`; it never claims a historical price was executable.
Static bracket fill semantics remain unchanged.

Each missed candle receives an as-of history prefix.  If the position cursor
predates a full 300-candle fetch, MFE/SSL evolution fails closed with
`monitor_history_gap`; the cursor is not advanced.  An already persisted stop
remains protective.

## Crash consistency

Dynamic fills use the account JSON as the authority and an outbox stored in
that JSON:

1. mutate cash and position once in memory;
2. add a deterministic `fill_id` record to `pending_fills`;
3. atomically save account plus outbox;
4. durably append any unseen fill IDs to JSONL;
5. clear the outbox and atomically save again.

Recovery republishes a pending fill without reapplying PnL.  A torn last JSONL
record is truncated before publication.  The protocol covers opens, dynamic
closes, static-bracket closes and strategy closes for every position that owns
a dynamic policy.  Positions without that optional policy retain the legacy
stream exactly; broad conversion of unrelated fills is outside this decision.

## Modes

- `disabled` or missing: exact legacy behavior and shape.
- `observe`: persist per-position state and append decisions to
  `paper_dynamic_exits.jsonl`; never close.
- `execute`: apply dynamic stops and close decisions using the transaction
  protocol above.

Runtime configuration starts in `observe`.  Promotion to `execute` requires a
separate reviewed YAML change after replay and shadow evidence.  No process
restart is part of this ADR.

## Reward/risk correction

The active YAML value was stored as `2.0` while AK still called
`compute_bracket()` at its default `1.5`.  New AK signals now receive the
configured RR.  Existing positions keep their frozen TP; their displayed RR is
derived from that TP and frozen structural distance instead of being
overwritten by YAML.

## Rejected alternatives

- Mutating `StrategyEngine.on_candle`: couples entry purity to portfolio state.
- Replacing TP with an unlimited runner: changes the validated bracket and is
  not needed for the first protection experiment.
- Retrofitting open positions from historical candles: creates ambiguous MFE,
  gap and activation semantics.
- Filling an outage decision at an old candle close: non-executable lookahead.
- Relying on the runner file lock for exactly-once fills: the lock does not make
  JSON and JSONL transactional.

## Verification gate

Before `execute`:

- focused unit tests for state transitions, stop/TP precedence, gap prices and
  outbox recovery;
- unchanged tests and replay outputs with dynamic exit missing/disabled;
- real replay comparison of bracket-only versus observe decisions;
- dashboard inspection of MFE, mode and candidate stop;
- adversarial review of the final diff.

Rollback is a YAML change to `mode: disabled` for future positions.  Positions
already frozen in `observe` remain non-executing; execute positions retain
their frozen policy until explicitly closed or migrated.
