# LLM Paper Runtime Activation

Date: 2026-07-13

Status: approved direction; written specification awaiting user review

Target repository: `/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading`

## Purpose

Activate the existing auditable LLM laboratory as a continuous, paper-only
runtime. The runtime uses the pinned OpenRouter model
`deepseek/deepseek-v4-pro`, runs one decision cycle per hour, remains visibly
auditable in the dashboard, and can be disabled without removing code or
laboratory history.

This activation never sends an exchange or broker order. It may mutate only
the isolated LLM paper accounts and append-only LLM journals already defined by
the laboratory.

## Considered approaches

### 1. Worker supervised by the existing engine (selected)

Add a small loop runner to the existing engine process group. It executes the
already validated one-shot autonomous command, waits until the next scheduled
cycle, and is restarted by the existing supervisor if it crashes.

This keeps start and stop semantics aligned with the rest of the bot while
preserving the LLM laboratory's separate files and failure boundary.

### 2. Inline scheduling inside the native trading worker

This would require the native strategy scheduler to own OpenRouter latency,
retries, and paper-laboratory state. It creates unnecessary failure coupling
and makes it harder to prove that LLM paper decisions cannot alter the native
portfolio. This approach is rejected.

### 3. Independent launchd agent

This provides maximum process isolation but adds a second operational lifecycle
and can leave the LLM running when the trading engine is intentionally stopped.
It is deferred unless future runtime or resource requirements justify it.

## Activation contract

The engine starts the LLM worker only when `LLM_PAPER_ENABLED=1`. Absence of the
variable, an empty value, or `0` keeps the feature off. The worker always invokes
the laboratory with all three explicit gates:

- mode `paper_autonomous`;
- one-shot execution;
- paper confirmation.

The worker cadence comes from the validated `analyst_interval_minutes`
configuration, initially 60 minutes. It runs once after startup and then aligns
subsequent attempts to the interval. The one-shot runtime's existing locks and
idempotency remain authoritative if a restart overlaps an earlier candle.

The LLM worker and native strategy worker share no account state. Stopping or
crashing the LLM worker must not stop native paper monitoring.

## Secret handling

The OpenRouter key is stored in the user's default macOS Keychain under:

- service: `0rum-openrouter`;
- account: the current macOS user.

The secret is retrieved at runtime through `/usr/bin/security` and passed to the
one-shot Python process through its environment. It is never placed in:

- source or YAML;
- launchd property lists;
- shell command arguments;
- process titles;
- stdout or stderr;
- dashboard API responses;
- decision journals, prompts, fixtures, or test snapshots.

The retrieval helper returns only a success/failure status to logs. Missing,
locked, or denied Keychain access prevents that LLM cycle and produces a
redacted configuration error; the native engine continues running.

## Model selection and routing

Every request uses the concrete model slug
`deepseek/deepseek-v4-pro`. The runtime does not use OpenRouter Auto Router,
`latest` aliases, or a fallback model list. Structured-output support remains
mandatory.

The response's `model` field must exactly equal the requested slug. A mismatch
is rejected before any decision can reach the paper simulator and is recorded
as a redacted model error. Request ID, latency, token usage, prompt version, and
model slug remain visible in the audit journal.

## Worker behaviour

Each cycle performs these steps:

1. Check that paper activation is still enabled.
2. Retrieve the OpenRouter key from Keychain without logging it.
3. Run one `paper_autonomous` laboratory cycle.
4. Append the existing decision, execution, outcome, and error records.
5. Publish a redacted heartbeat containing timestamps and cycle status.
6. Wait until the next interval unless terminated.

Termination signals interrupt the wait promptly. Transient OpenRouter or
market-data failure affects only the current cycle. The existing bounded retry
inside the OpenRouter client remains the only immediate API retry; the worker
does not spin or create an unbounded retry storm.

## Dashboard visibility

The existing read-only LLM card remains the primary operator surface. Runtime
activation adds only bounded, non-secret state:

- enabled or disabled;
- worker running or stopped;
- configured model and cadence;
- last cycle start and completion time;
- last cycle result;
- next scheduled cycle;
- last redacted error;
- reference and evolving decision summaries already exposed by the lab.

No dashboard endpoint may return environment variables, Keychain output,
authorization headers, raw provider request bodies, or exception objects that
could contain credentials.

## Failure handling

- Missing Keychain entry: worker remains alive, marks the cycle
  `configuration_error`, and tries again at the next interval.
- Keychain authorization denied or locked: same redacted behaviour as a missing
  entry.
- OpenRouter authentication failure: record HTTP status class without response
  bodies that might contain sensitive data.
- Model mismatch or invalid structured output: reject the decision; do not
  mutate either paper account.
- Market-data failure: record incomplete evidence or cycle failure according to
  the existing laboratory contract.
- Worker crash: the existing engine supervisor restarts only the LLM worker.
- Engine stop: the worker receives the same process-group termination as the
  native worker, watcher, and producer.

## Verification

Implementation is acceptable only after all of the following pass:

1. Unit tests for Keychain retrieval success and redacted failure using an
   injected command runner; tests never access the real secret.
2. Unit tests for activation parsing, cadence, termination, heartbeat, and
   non-spinning failure behaviour.
3. Engine-script tests proving disabled mode starts no LLM worker and enabled
   mode starts the paper-only worker with explicit confirmation.
4. Existing OpenRouter tests proving exact model pinning, structured JSON, and
   mismatch rejection.
5. Existing simulator tests proving isolation from native paper files.
6. Full Python test suite and JavaScript syntax check.
7. A single real canary cycle using the Keychain entry, with inspection limited
   to model slug, status, timestamps, decision IDs, and redacted logs.
8. Runtime status and dashboard API checks after the user-authorized restart.

The canary must not print the key, request authorization header, or full process
environment.

## Rollback

Set `LLM_PAPER_ENABLED=0` and restart the engine. This stops future LLM calls
without deleting journals or paper accounts. If the worker integration itself
must be removed, revert only the LLM runner and engine-supervision changes; the
one-shot laboratory and its immutable history remain usable.

The Keychain item is independent of code rollback and is removed only by an
explicit operator action.
