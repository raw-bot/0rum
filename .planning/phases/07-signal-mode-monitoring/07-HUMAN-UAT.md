---
status: partial
phase: 07-signal-mode-monitoring
source: [07-VERIFICATION.md]
started: 2026-05-18T10:00:00Z
updated: 2026-05-18T10:00:00Z
---

## Current Test (Web-Only Monitoring)

[awaiting human testing]

## Tests

### 1. App startup without Telegram
expected: No startup errors; 'app.execution_services_wired' log event present. TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are NOT required.
why_human: Startup behavior can't be fully verified without running the app in the target environment.
result: [pending]

### 2. Dashboard visual appearance and auto-refresh
expected: Dark background (#0d0f11), '0rum Dashboard' title, SIGNAL/AUTO badge, Status Row with DB/Redis indicators, five data panels (Open Trades, Closed Trades, Candidate Decisions, Strategy Performance, Latest Signals). After 30 seconds, timestamp updates.
why_human: Visual appearance, layout fidelity, and CSS rendering can't be verified programmatically.
result: [pending]

### 3. Operational events banner
expected: When DB or Redis is down, a warning banner '⚠ database_unreachable' or '⚠ redis_unreachable' appears at the top of the dashboard.
why_human: Requires manual interruption of services to verify UI response.
result: [pending]

### 4. Local Signal Auditing
expected: Triggering a pipeline run logs `execution.signal_mode.recorded` with strategy and direction details.
why_human: Requires observing log output during a live run.
result: [pending]

## Summary

total: 4
passed: 0
issues: 0
pending: 4
skipped: 0
blocked: 0

## Gaps
None.
