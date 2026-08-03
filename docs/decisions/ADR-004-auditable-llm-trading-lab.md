# ADR-004: Isolate auditable LLM paper execution and learning

## Status

Accepted

## Date

2026-07-13

> Update 2026-07-25: the live `paper_autonomous` cycle
> (`orum/llm/paper_agent.py`) now calls NVIDIA directly (`NvidiaClient`,
> Keychain entry `0rum-nvidia`, `NVIDIA_API_KEY`) instead of routing the
> pinned model through OpenRouter — OpenRouter kept returning HTTP 402
> (insufficient credits) and 404 (no endpoint) for this model. `nvidia` is
> now the only supported provider (default and sole CLI choice); OpenRouter
> can no longer be selected.

## Context

The project originally intended to let an LLM participate directly in trading
decisions. Reference logs show useful reasoning about facts versus
interpretation, narrative versus price, pain trades, alternatives, sizing,
leverage, stops and targets. They also show why prose cannot be trusted as an
execution contract: verbal rationale and structured action may contradict one
another.

The research goal explicitly permits aggressive paper risk. It also requires
that every decision be visible, replayable and attributable, that learned
behavior can be compared with a stable control, and that a French regulatory
annotation never silently weakens the experiment. The native unified paper
ledger must remain authoritative for native strategies.

## Decision

Install five named laboratory modes. `off`, `observer`, `shadow` and
`paper_autonomous` are implemented. Autonomous paper requires an explicit
per-invocation `--confirm-paper`. `paper_assisted` fails closed until the native
strategy defines a tested field-ownership and merge contract.

Pin `nvidia/nemotron-3-ultra-550b-a55b` with strict structured outputs and no
silent model/provider fallback (originally routed through OpenRouter; see the
2026-07-25 note above). Let the model propose direction,
equity fraction, leverage, order intent, stop, targets, trailing/time exit and
French rationale. Validate response schema, provenance and mechanics locally.
Market actions can reach only an isolated simulator; limit intents are visibly
rejected until pending-order state exists.

Persist canonical point-in-time snapshots, briefs, proposals, validations,
fills, deterministic outcomes, LLM post-mortems and lesson events. Store
observable rationale rather than private chain-of-thought. Resume valid
journaled proposals after a crash from server-recorded execution context and
make fill/account publication idempotent across processes. Decision IDs and
timestamps are server-derived; model-provided values are not authoritative.

Give `llm_reference` and `llm_evolving` separate isolated-margin accounts. The
reference lane receives no lessons. The evolving lane receives at most five
deterministically matching active lessons and is the only lane allowed to train
the lesson book. Compare actual account return only on a shared calendar window
with observations in both lanes and label missing coverage.

Use a pure long/short leveraged simulator with explicit fees and liquidation
approximation. For ambiguous closed candles, order events pessimistically as
liquidation, stop, then take profit. Never sum LLM accounts into the native
portfolio.

Keep experimental leverage and regulatory eligibility independent. Paper
leverage is clamped only by configurable experiment bounds (40x by default).
The conservative French-retail annotation reports 2x for crypto
perpetual/CFD-like products and marks excess as experimental; it neither
authorizes a product nor changes the paper decision.

Expose the audit in a bounded, read-only dashboard payload. Escape all model
text and provide no mode-activation UI. Provide an offline replay that uses
recorded decisions and candles and produces a stable digest without any model,
news or exchange call.

Real-money execution, credentials and private exchange order methods are
outside this architecture.

## Alternatives considered

### Reuse the native unified paper broker

Rejected. It is long-only/spot-style and risk-sizes through native strategy
rules. Adding LLM shorts, 40x leverage and liquidation would contaminate the
control portfolio and change its accounting semantics.

### Let confidence determine leverage mechanically

Rejected. The experiment is specifically intended to test whether the model
can choose size, leverage, stop and targets. Mechanical confidence throttling
would hide the behavior under study. Hard paper bounds and mechanical validity
checks remain.

### Activate a lesson after one loss

Rejected. A single outcome cannot distinguish a stable process error from
noise. One case creates a candidate; corroboration or exact-window replay
improvement is required for activation.

### Execute limit proposals at the current close

Rejected. That fabricates fills. Until a pending-order book is implemented,
limit intent is journaled and rejected explicitly.

## Consequences

- The LLM can make aggressive, fully specified 1x–40x paper decisions without
  touching native or real accounts.
- Every actionable proposal can be traced through snapshot, brief, validation,
  fill/rejection, outcome, post-mortem and lesson.
- Crash retries do not re-call the model or double-charge an accepted action.
- Reference/evolving learning claims have an uncontaminated control and common
  comparison window.
- Operators can read French rationale, `HOLD`, failures and regulatory labels
  from the dashboard and journals.
- `paper_assisted`, limit pending orders, macro/on-chain evidence and real
  execution remain explicit future decisions rather than implicit behavior.
- Passing replay and tests demonstrates determinism and isolation, not trading
  profitability or legal eligibility.
