---
status: partial
phase: 07-signal-mode-monitoring
source: [07-VERIFICATION.md]
started: 2026-05-03T20:00:00Z
updated: 2026-05-03T20:00:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. App startup with Telegram wiring
expected: No startup errors; 'app.telegram_bot_initialized' and 'app.execution_services_wired' log events present
why_human: Requires valid TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars; startup behavior can't be verified without running the app
result: [pending]

### 2. Dashboard visual appearance and auto-refresh
expected: Dark background (#0d0f11), '0rum Dashboard' title, SIGNAL/AUTO badge, Status Row with DB/Redis indicators, three data panels (Open Trades, Strategy Performance, Latest Signals). After 30 seconds, timestamp updates.
why_human: Visual appearance, layout fidelity, and CSS rendering can't be verified programmatically
result: [pending]

### 3. Telegram signal message format
expected: Formatted BUY/SELL message with entry, SL, TP1, TP2, confidence, and size suggestion arrives in configured Telegram chat
why_human: Requires live Telegram Bot API interaction; can't verify message delivery without sending to real chat
result: [pending]

### 4. Trade lifecycle notifications
expected: TP1 HIT, TP2 HIT, SL HIT, TRAIL STOP Telegram notifications fire correctly as trades progress through lifecycle
why_human: Real-time behavior depending on market price movements; requires app running against live market data
result: [pending]

### 5. Circuit breaker alert
expected: After 8 consecutive theoretical SL closes, a CIRCUIT BREAKER TRIPPED Telegram alert is sent
why_human: Cannot trigger 8 consecutive stops without a running system with live data
result: [pending]

### 6. Daily summary at 00:00 UTC
expected: Daily summary Telegram message with signals sent, trades, P&L, and circuit breaker state
why_human: Scheduled job at 00:00 UTC; requires app to run through midnight
result: [pending]

## Summary

total: 6
passed: 0
issues: 0
pending: 6
skipped: 0
blocked: 0

## Gaps
