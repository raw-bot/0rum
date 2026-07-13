# LLM Trading Lab — Paper Execution, Outcomes, Learning, and Dashboard Plan

> **Execution rule:** implement task by task with TDD, run GitNexus impact before
> changing an existing symbol, run focused tests after every task, and run
> `gitnexus_detect_changes` before each commit. Never restart the live worker,
> watcher, dashboard or producer without explicit operator approval.

**Goal:** turn validated shadow decisions into isolated, aggressive paper
experiments; evaluate them deterministically; create bounded, falsifiable
lessons; compare reference and evolving lanes; and expose the complete audit in
the existing read-only dashboard.

**Non-goals:** real exchange orders, exchange credentials, professional-client
classification, legal advice, auto-editing prompts/source code, embeddings,
and activation/restart of live processes.

## Architectural correction before implementation

The existing unified portfolio broker is long-only, ATR-risk-sized and
spot-style. Retrofitting shorts, leverage and liquidation into it would change
native strategy accounting and invalidate the reference/control portfolio.
Therefore each LLM lane receives an isolated experimental derivatives book.
The existing unified ledger remains authoritative for native paper strategies;
the LLM lane ledgers are authoritative only for their named experiments. The
dashboard labels and compares them explicitly and never sums their balances.

All simulator math is pure. A persistence service owns JSON/JSONL I/O and uses
the same redirectable `STATE_DIR`. No simulator or LLM service imports a real
exchange order boundary.

## State additions

- `state/llm_reference_account.json`
- `state/llm_evolving_account.json`
- `state/llm_paper_fills.jsonl`
- `state/llm_outcomes.jsonl` (already reserved)
- `state/llm_lessons.jsonl` (already reserved)
- `state/llm_postmortems.jsonl`

Account snapshots are replaceable state written atomically. Fills, outcomes,
post-mortems and lesson events are append-only. Every record includes lane,
decision ID, snapshot hash, schema version and UTC timestamp.

## Task 1: Define derivatives paper contracts

**Files:**

- Create `orum/llm/paper_contracts.py`
- Create `tests/test_llm_paper_contracts.py`
- Modify `orum/paths.py` only to add redirectable path constants

**TDD cases:**

- long and short positions round-trip without losing stop/TP fractions;
- account equity includes realized cash plus side-correct unrealized PnL;
- requested and effective leverage remain distinct;
- decision, fill and position IDs are non-empty and immutable;
- NaN, infinity, negative collateral, duplicate TP IDs and unknown fields fail;
- legacy/missing account files produce a fresh versioned account rather than
  inheriting the native portfolio account.

**Contracts:**

- `LlmPaperPosition`: lane, decision ID, side, quantity, entry/mark,
  notional, initial margin, effective leverage, fees, liquidation estimate,
  stop, remaining TP ladder, trailing/time exit and thesis/invalidation copy.
- `LlmPaperAccount`: lane, starting balance, realized balance, positions keyed
  by symbol, processed decision IDs and last processed candle per position.
- `LlmPaperFill`: unique fill ID, decision ID, action, reason, side, quantity,
  price, fee, realized PnL, balance after and source candle timestamp.

**Verification:** contract tests plus existing paper broker serialization tests.

## Task 2: Implement the pure leveraged simulator

**Files:**

- Create `orum/llm/paper_simulator.py`
- Create `tests/test_llm_paper_simulator.py`

**Sizing model:**

For an opening decision:

```text
allocated_equity = equity_before * equity_fraction
notional = allocated_equity * paper_effective_leverage
quantity = notional / entry_price
initial_margin = notional / paper_effective_leverage
```

This intentionally permits aggressive exposure. Entry and exit fees are
charged on notional. The account never borrows from or mutates the native paper
portfolio.

**Liquidation model v1:**

Use an explicit configurable maintenance-margin fraction and isolated-margin
approximation. Record the formula/version on each position. Liquidation is a
simulator event, not an exchange claim. Reject entries whose stop is already
beyond the estimated liquidation price unless config explicitly enables
`allow_stop_beyond_liquidation` for destructive experiments; record that flag.

**Intrabar rules:**

- operate on closed normalized OHLC candles only;
- if stop and TP collide in one candle, process the adverse event first;
- liquidation precedes stop when both are touched;
- apply TP fractions sequentially and side-correctly;
- update trailing stop only after processing the current bar's adverse side;
- time exits use closed-candle timestamps;
- one decision/candle/position event is idempotent.

**TDD matrix:** long/short win and loss, fees, 1x/20x/40x, partial TP, stop/TP
collision, liquidation, add, reduce, close, hold, duplicate action, insufficient
equity, two lanes with identical inputs and ruin to zero.

## Task 3: Add deterministic pre-execution validation

**Files:**

- Create `orum/llm/paper_validator.py`
- Create `tests/test_llm_paper_validator.py`

Validation produces a structured `ValidationReport`; it never edits a model
decision. Check:

- decision action agrees with current position state;
- snapshot symbol/cutoff and decision horizon are current;
- market/limit entry can be resolved from closed data;
- effective leverage comes from the committed leverage policy;
- equity fraction, available isolated margin and venue precision/minimum are
  mechanically valid;
- long/short stop and TP geometry;
- liquidation/stop relationship and explicit destructive-experiment flag;
- decision ID and snapshot/lane are not already processed;
- memo action and structured action are not obviously contradictory using a
  bounded deterministic action vocabulary.

Every rejection is journaled with all reasons and no account mutation.

## Task 4: Add crash-safe lane persistence

**Files:**

- Create `orum/llm/paper_store.py`
- Create `tests/test_llm_paper_store.py`
- Modify `orum/paths.py`

Use per-lane file locks and atomic temp-file replacement for account state.
Persist the fill journal before publishing the new account snapshot, with an
operation ID allowing startup reconciliation. A repeated operation must either
complete the missing account publication or return the original fill; it must
never charge fees twice.

Test simulated failure after fill append, concurrent duplicate decision IDs,
corrupt account state, lane mismatch and `0RUM_STATE_DIR` isolation.

## Task 5: Wire `paper_autonomous` behind explicit one-shot activation

**Files:**

- Modify `orum/llm/runtime.py`
- Modify `scripts/run_llm_lab.py`
- Modify `orum/llm/config.py`
- Create/update `tests/test_llm_runtime.py`

Add injected validator/store/simulator dependencies. Runtime sequence:

1. monitor each existing LLM position on the new closed candle;
2. snapshot the resulting lane account state;
3. produce one shared brief;
4. retrieve lane-specific lessons;
5. obtain decisions;
6. journal proposals before validation;
7. journal validation;
8. execute valid actions once in the isolated paper book;
9. return proposal/validation/execution IDs and French memos.

`paper_autonomous` requires both `--mode paper_autonomous` and
`--confirm-paper`; absence returns exit 2. This confirmation is per command and
does not enable any daemon. `off`, `observer` and `shadow` behaviour stays
byte-for-byte compatible.

`paper_assisted` remains refused until a precise native-signal merge contract
is specified and tested. This avoids inventing which fields the native strategy
owns.

Prove by import/AST tests that `orum.llm` and the runner contain no CCXT private
method, API secret, order creation or real executor import.

## Task 6: Compute deterministic outcomes

**Files:**

- Create `orum/llm/outcomes.py`
- Create `tests/test_llm_outcomes.py`

Evaluate every executed or shadow actionable decision at configured closed
horizons and final close. Store:

- return after simulated costs and account return;
- MFE and MAE with timestamps;
- time to stop, TP, liquidation and close;
- realized PnL and remaining exposure;
- counterfactual HOLD and opposite-direction returns;
- pessimistic same-bar collision result;
- confidence calibration contribution;
- process flags such as stale evidence or structured/text mismatch.

Metrics are computed without an LLM. Outcome IDs are deterministic from
decision ID + horizon + cutoff so reruns deduplicate. Never reconstruct missing
historical candles from current data.

## Task 7: Add bounded LLM post-mortems

**Files:**

- Create `orum/llm/postmortem.py`
- Extend `orum/llm/prompts.py`
- Create `tests/test_llm_postmortem.py`

The post-mortem receives only the original snapshot/brief/decision, immutable
deterministic metrics and known error taxonomy. It cannot change metrics. It
returns concise French process assessment, primary/secondary error categories,
what was done well, what would be changed, and a candidate falsifiable lesson.

Reject invented decision/evidence IDs and mismatched metric values. Journal
invalid responses as `model_error`. Distinguish bad process/lucky profit from
good process/adverse randomness.

## Task 8: Implement lessons as an event-sourced state machine

**Files:**

- Create `orum/llm/lessons.py`
- Create `tests/test_llm_lessons.py`

Lesson states: candidate, active, rejected, expired, superseded. Each event
contains conditions, adjustment, supporting decisions, counterexamples, sample
count, evidence strength, creation/expiry and version.

Activation requires either two supporting completed cases without a stronger
counterexample or one case plus a replay improvement over the exact historical
window. A single loss cannot activate a lesson. Conflicting lessons remain
candidates. Events never rewrite prior records.

## Task 9: Add deterministic similar-case retrieval

**Files:**

- Create `orum/llm/retrieval.py`
- Create `tests/test_llm_retrieval.py`

Rank active, non-expired lessons by exact/categorical similarity over symbol,
regime, volatility bucket, side, action, funding sign, OI-change bucket,
narrative class and portfolio exposure. Tie-break by evidence strength,
support count, recency and lesson ID. Return at most configured five lessons.

The reference lane always receives zero lessons. The evolving lane receives
only the returned IDs and structured content. No embeddings or free-form web
memory enter v1.

## Task 10: Add comparison metrics

**Files:**

- Create `orum/llm/comparison.py`
- Create `tests/test_llm_comparison.py`

Compute lane-separated compounded return, max drawdown, Calmar, log growth,
liquidation/ruin rate, turnover, validity rate, confidence calibration and
regime breakdown. Use common cutoff windows and report missing coverage; never
compare a mature lane against a shorter lane without labelling the intersection
window.

Test reference/evolving identical history, divergent history, one ruined lane,
no decisions and unequal coverage.

## Task 11: Expose a read-only dashboard API

**Files:**

- Modify `orum/dashboard.py`
- Modify dashboard template/style only in the existing dashboard files
- Create `tests/test_dashboard_llm.py`

Run GitNexus impact on every existing route/helper before edits. Add a bounded
read-only payload containing:

- current market opinion and freshness;
- decision timeline including HOLD/errors/rejections/fills;
- decision detail with requested/effective/FR leverage;
- open LLM experimental positions and liquidation estimates;
- deterministic outcomes and post-mortems;
- candidate/active/expired lessons;
- native/reference/evolving comparison;
- alerts for bias flip, leverage spike, stale evidence, model change,
  contradiction and lane divergence.

All cards link snapshot, brief, decision, fill, outcome and lesson IDs. Escape
all model text. Bound JSONL reads and tolerate malformed tail records without
taking down the native dashboard. The UI provides mode visibility but no mode
activation button in v1.

Visual verification requires a separately launched isolated dashboard or
explicit permission to restart the live dashboard. Unit/API rendering tests do
not require a live restart.

## Task 12: Replay and end-to-end acceptance

**Files:**

- Create `scripts/replay_llm_lab.py`
- Create `tests/test_llm_replay.py`
- Update `docs/llm-trading-lab.md`, README and ADR-004

Replay uses recorded model responses and historical closed candles, never a
remote LLM or current news. Verify:

1. a 20x long and 40x short are simulated while showing 2x French eligibility;
2. identical snapshot/lane decisions never execute twice;
3. stop/TP/liquidation collision is pessimistic and deterministic;
4. outcomes are reproducible;
5. one loss stays candidate and two corroborating cases can activate a lesson;
6. reference never sees the lesson and evolving does;
7. lane comparison uses the shared cutoff intersection;
8. `off` leaves all native files byte-identical;
9. no real order method is reachable;
10. no API key appears in any state file or dashboard payload.

Run focused suites, then the full test suite. Run `git diff --check`, compile,
secret-pattern scan and GitNexus detect-changes. Do not start or restart a live
process.

## Rollback

- `mode: off` immediately disables calls and LLM paper mutations.
- Each LLM lane account is isolated from native portfolio state.
- Every implementation task is an atomic commit.
- Account snapshots can be rebuilt from fills only by an explicit recovery
  command; rollback never deletes journals.
- Lessons roll back by selecting an earlier active-set version or emitting a
  superseding event.
- Native paper strategy, ledger and dashboard payload remain functional when
  all LLM files are absent.

## Completion gate

Do not call this phase complete until every actionable proposal can be traced
`snapshot -> brief -> decision -> validation -> fill/rejection -> outcome ->
post-mortem -> lesson`, both lanes are comparable on common data, all LLM text
is visible and escaped, and a structural test proves that the only execution
implementation reachable from the LLM runtime is the isolated paper simulator.
