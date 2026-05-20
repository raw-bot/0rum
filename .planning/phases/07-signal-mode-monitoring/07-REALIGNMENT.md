---
phase: 07-signal-mode-monitoring
status: approved
date: 2026-05-06
decision: remove-external notification channel-web-monitoring-only
---

# Phase 7 Realignment: Web Monitoring Only

## Decision

External notification channel is removed from Phase 7.

0rum is an autonomous trading bot. It should not notify the operator for every
decision, candidate signal, approved signal, or theoretical trade lifecycle
event. The operator experience is a web UI that monitors what the bot is doing.

## Product Intent

The bot trades by itself. The UI exists to observe, audit, and diagnose the bot,
not to approve or react to each trading decision.

Normal decisions are persisted in PostgreSQL, logged with structlog, and exposed
through the dashboard API. They are not pushed to an external chat channel.

## Phase 7 Runtime Scope

Phase 7 must provide:

- `/dashboard`: read-only operator web UI.
- `/api/dashboard`: complete monitoring payload for the UI.
- `/health`: lightweight machine-readable health endpoint.
- PostgreSQL persistence for signals, approved decisions, theoretical trades,
  lifecycle transitions, and strategy stats.
- Structured logs for operational audit and debugging.
- Scheduler jobs for ingestion, strategies, pipeline processing, trade
  monitoring, optimization, and any local summary/state aggregation needed by
  the UI.

Phase 7 must not require:

- `EXTERNAL_NOTIFICATION_TOKEN`.
- `EXTERNAL_NOTIFICATION_CHAT_ID`.
- A live External notification channel Bot API call.
- Any External notification channel startup initialization.
- Any External notification channel-based UAT step.
- Any notification on normal trading decisions.

## Dashboard Monitoring Contract

The dashboard is the primary operator surface and must show enough information
to understand what the autonomous bot is doing:

- System state: database, Redis, scheduler/ingestion freshness, execution mode,
  circuit breaker state.
- Trading activity: open trades, recently closed trades, P&L, position status.
- Bot decisions: recent candidate signals, approved signals, rejected signals
  when rejection reason is available.
- Strategy state: active/inactive strategies, WFE/statistics, recent
  performance.
- Operational issues: risk blocks, provider/broker failures, stale data, and
  critical exceptions when available from persisted state or logs.

The UI remains read-only in Phase 7. It must not contain controls that mutate
trading state.

## Codebase Realignment

Implementation should remove External notification channel from the active code path:

- Remove External notification channel settings from `src/config.py` and `.env.example`.
- Remove `external-notification-client` from project dependencies if no import remains.
- Remove External notification channel initialization and shutdown from `src/main.py`.
- Replace the signal-mode delivery abstraction with a local execution/audit path
  that records decisions and lets the dashboard display them.
- Remove External notification channel notification jobs and hooks from scheduler/risk wiring.
- Delete or rewrite External notification channel-specific tests.
- Update Phase 7 verification and UAT docs so web monitoring is the validation
  target.

## Validation Target

Phase 7 is complete when the current app can be started locally without
External notification channel secrets and the operator can inspect bot state through the web UI and
JSON endpoints.

Local UAT should verify:

- App startup succeeds with no External notification channel environment variables.
- `/dashboard` loads successfully.
- `/api/dashboard` exposes system state, trading activity, strategy stats, and
  recent decisions.
- `/health` reports degraded or healthy status based on DB/Redis availability.
- No UAT step depends on a External notification channel chat or Bot API call.
