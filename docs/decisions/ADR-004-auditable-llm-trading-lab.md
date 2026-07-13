# ADR-004: Separate auditable LLM proposals from paper execution

## Status

Accepted for the observer/shadow foundation

## Date

2026-07-13

## Context

The project originally intended to let an LLM participate directly in trading
decisions. Previous reference logs demonstrate useful market reasoning —
facts versus interpretation, narrative versus price, pain trade, alternative
hypotheses, sizing, leverage, stops and targets — but also show that prose and
the final structured action can contradict each other.

The research goal requires aggressive decisions, including leverage and risk,
without pretending those experiments are French-retail eligible or allowing a
model response to reach an order path before it is observable and replayable.
The existing unified paper ledger is authoritative and cannot safely be
mutated by a new orchestration layer without a separately tested adapter.

## Decision

Introduce an opt-in LLM laboratory with five named modes. Only `off`,
`observer` and `shadow` are installed in the foundation. `paper_assisted` and
`paper_autonomous` are valid configuration concepts but fail closed with an
explicit error until their execution phase exists.

Pin `deepseek/deepseek-v4-pro` through OpenRouter strict structured outputs,
disable silent model/provider fallback and validate every response locally.
The model may choose direction, equity fraction, leverage, entry type, stop,
targets and time exit. Mechanical validation and provenance checks reject
malformed or invented evidence rather than weakening the experiment.

Persist canonical point-in-time snapshots, market briefs and proposed
decisions in append-only journals. Store observable rationale and a French
memo, not private chain-of-thought. Run a lesson-free reference lane beside an
eventually learning evolving lane, with duplicate snapshot/lane decisions
skipped before another model call.

Keep experimental leverage and regulatory eligibility independent. Paper
leverage is clamped only by configurable paper bounds (40x by default). The
French retail display conservatively reports 2x for crypto perpetual/CFD-like
products under the AMF/ESMA framework and labels excess leverage as
experimental; it never changes the requested paper experiment.

Use public, credential-free CCXT market endpoints and point-in-time GDELT
headlines. Do not import the paper engine, paper broker or any order method from
the LLM foundation.

## Consequences

- The bot can state a comprehensive market opinion and aggressive trade plan
  without changing a position.
- Every accepted response and model failure is tied to the exact evidence
  snapshot, model and prompt version.
- Reference/evolving comparison is possible without contaminating the control
  lane with learned lessons.
- The legal label remains visible without being mistaken for either legal
  advice or a paper-risk rule.
- A separate phase must define idempotent paper fills, outcome timing,
  post-mortems, lesson promotion and dashboard/operator controls before either
  paper mode can be enabled.
- Real-money execution remains outside this decision.
