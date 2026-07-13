# LLM Paper Runtime Activation

Date: 2026-07-13

Status: approved in conversation; amended after explicit legacy-engine retirement

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

### 1. Worker supervised by the legacy engine

Add a small loop runner to the existing engine process group. It executes the
already validated one-shot autonomous command, waits until the next scheduled
cycle, and is restarted by the existing supervisor if it crashes.

This was initially selected, then rejected operationally when the user
explicitly retired the legacy engine. Coupling the LLM to that process would
either revive a non-authoritative trading path or prevent the LLM from running.

### 2. Inline scheduling inside the native trading worker

This would require the native strategy scheduler to own OpenRouter latency,
retries, and paper-laboratory state. It creates unnecessary failure coupling
and makes it harder to prove that LLM paper decisions cannot alter the native
portfolio. This approach is rejected.

### 3. Independent launchd agent (selected)

This provides maximum process isolation and matches the post-retirement runtime:
the unified paper portfolio is already a scheduled LaunchAgent rather than a
long-lived engine session. The LLM receives its own hourly paper-only agent so
OpenRouter latency or failure cannot delay the authoritative portfolio cycle.

## Activation contract

The `com.0rum.llm-paper` LaunchAgent starts the worker only when installed and
enabled. Its non-secret environment also requires `LLM_PAPER_ENABLED=1`;
absence of the variable, an empty value, or `0` makes an accidental manual
invocation a no-op. The worker always invokes the laboratory with all three
explicit gates:

- mode `paper_autonomous`;
- one-shot execution;
- paper confirmation.

The LaunchAgent cadence comes from the validated `analyst_interval_minutes`
configuration, initially 60 minutes, and is installed with `StartInterval=3600`.
The one-shot runtime's existing locks and idempotency remain authoritative if a
manual kickstart overlaps an earlier candle.

The LLM agent and unified portfolio agent share no account state or process
group. Stopping, disabling, or crashing the LLM agent must not stop native paper
monitoring.

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
6. Exit and let launchd schedule the next interval.

Transient OpenRouter or market-data failure affects only the current cycle. The
existing bounded retry inside the OpenRouter client remains the only immediate
API retry; launchd supplies the next hourly attempt and cannot create an
unbounded retry storm.

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
- Worker crash: launchd records the exit and waits until the next scheduled run;
  `KeepAlive` is deliberately absent.
- Agent bootout or disable: no future LLM call occurs and the unified paper
  portfolio continues independently.

## Verification

Implementation is acceptable only after all of the following pass:

1. Unit tests for Keychain retrieval success and redacted failure using an
   injected command runner; tests never access the real secret.
2. Unit tests for activation parsing, heartbeat, and non-spinning one-shot
   failure behaviour.
3. LaunchAgent validation proving a 3600-second cadence, no `KeepAlive`, a
   paper-only command, and no credential in the plist.
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

Boot out or disable `com.0rum.llm-paper`. This stops future LLM calls without
restarting the dashboard, touching the unified portfolio, or deleting journals
and paper accounts. If the integration itself must be removed, delete only the
LLM LaunchAgent and runner; the one-shot laboratory and its immutable history
remain usable.

The Keychain item is independent of code rollback and is removed only by an
explicit operator action.
