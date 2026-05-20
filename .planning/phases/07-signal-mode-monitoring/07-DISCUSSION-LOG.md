# Phase 7: signal-mode-monitoring - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-28
**Phase:** 07-signal-mode-monitoring
**Areas discussed:** Trade lifecycle monitor, Module structure, size_lots threading, SIG-03 stats storage

---

## Trade Lifecycle Monitor

| Option | Description | Selected |
|--------|-------------|----------|
| New APScheduler job every 15 min | Evaluates M15 high/low range. Clean separation from run_pipeline. | ✓ (freeform) |
| Inline at start of run_pipeline() | Couples trade monitoring to the signal flow. | |
| Triggered on H1 candle refresh | Slower TP1/SL detection (60-min cadence). | |

**User's choice:** New APScheduler job every 15 min — evaluate latest completed M15 candle HIGH/LOW range (not close only). H1 ATR used only for trailing distance after TP1. Clean separation from run_pipeline.

---

| Option | Description | Selected |
|--------|-------------|----------|
| New column on TradeORM (trailing_stop_price) | Alembic migration. Persistent, ratchet-safe. | ✓ |
| Redis key per trade | No migration but loses state on Redis flush. | |
| Recompute each run | No storage, no ratchet guarantee across restarts. | |

**User's choice:** New column `trailing_stop_price: Decimal nullable` via Alembic migration.

---

| Option | Description | Selected |
|--------|-------------|----------|
| TP2 OR trailing stop, whichever first | Two exit conditions active simultaneously post-TP1_HIT. | ✓ |
| Trailing stop only | TP2 is a bonus, not a required close trigger. | |
| TP2 only, no trailing in Phase 7 | Defer trailing to Phase 8. | |

**User's choice:** Both TP2 and trailing stop active after TP1_HIT. Whichever hits first closes.

---

**Additional clarification (freeform):** Conservative candle resolution — SL beats TP1 when both touched in same M15 candle (pre-TP1_HIT); trailing stop beats TP2 in same candle (post-TP1_HIT). All price comparisons use Decimal.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Blended P&L on close | pnl_pct = 0.5 × pnl_at_tp1 + 0.5 × pnl_at_exit. Single row. | ✓ |
| Two rows (TP1 partial + final close) | Doubles rows per trade, complicates SIG-03. | |
| Record only final exit P&L | Understates wins when TP1 captured before trail reversed. | |

**User's choice:** Blended P&L on final close. Single TradeORM row.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Same monitor job, inline | record_stop() called immediately after SL close. | ✓ |
| Event hook (monitor emits, breaker subscribes) | Decoupled but adds indirection. | |

**User's choice:** Monitor calls BreakerManager.record_stop() inline after SL close.

---

| Option | Description | Selected |
|--------|-------------|----------|
| pnl_pct > 0 on blended close | TRAIL exits in profit also count as wins. | ✓ |
| close_reason in (TP1, TP2) only | TRAIL close in profit would NOT reset counter. | |

**User's choice:** pnl_pct > 0 on blended close triggers record_win(). Includes profitable TRAIL exits.

---

## Module Structure

| Option | Description | Selected |
|--------|-------------|----------|
| src/execution/ + src/monitoring/notification_adapter.py | Both modules created. Matches AGENTS.md layout. | ✓ |
| Everything in src/monitoring/notification_adapter.py | Simpler but contradicts AGENTS.md and requires Phase 8 refactor. | |
| src/execution/ only | notification_adapter.py stays empty placeholder. | |

**User's choice:** Create both modules.

---

| Option | Description | Selected |
|--------|-------------|----------|
| signal_sender = signal msg only; notification_adapter = everything else | Clean split. Pipeline imports signal_sender; monitor imports notification_adapter. | ✓ |
| signal_sender = all outbound sends; notification_adapter = bot command handling | Puts all External notification channel I/O in one place. | |

**User's choice:** signal_sender.py owns initial signal message only. notification_adapter.py owns all lifecycle notifications + CB + daily summary.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Create ExecutionRouter stub now | auto branch raises NotImplementedError. Phase 8 extension point ready. | ✓ |
| Call signal_sender directly, skip router | Less scaffolding now, more refactoring in Phase 8. | |

**User's choice:** ExecutionRouter created in Phase 7 with NotImplementedError on auto branch.

---

| Option | Description | Selected |
|--------|-------------|----------|
| In main.py application startup | register_alert_hook() at startup alongside scheduler setup. | ✓ |
| In monitor job, lazy registration | Delayed but avoids importing notification_adapter at startup. | |

**User's choice:** CB hook registered in main.py at app startup.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Singleton Bot via dependency injection | One Bot() in main.py, injected into both constructors. | ✓ |
| Each class instantiates its own Bot | Two separate client objects for same token. | |

**User's choice:** Single Bot instance instantiated in main.py, injected into SignalSender and NotificationAdapter.

---

## size_lots Threading

| Option | Description | Selected |
|--------|-------------|----------|
| Thread full RiskDecision through risk_passed | list[tuple[CandidateSignal, float, RiskDecision]]. Minimal change. | ✓ |
| Thread size_lots Decimal only | Lighter but loses vol_factor and concentration_reduced. | |
| Pass RiskDecision as separate dict arg to _persist() | Zero change to list type but more refactoring. | |

**User's choice:** Thread full RiskDecision — risk_passed becomes list[tuple[CandidateSignal, float, RiskDecision]].

---

| Option | Description | Selected |
|--------|-------------|----------|
| TradeORM inside _persist(), same transaction as ApprovedSignalORM | Atomic — no orphan signals. | ✓ |
| TradeORM in ExecutionRouter, after _persist() returns | Two transactions — inconsistency risk if send fails. | |

**User's choice:** TradeORM creation inside _persist() in same transaction as ApprovedSignalORM.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Update execution_status to SENT after successful send | Auditable. AGENTS.md enum value. | ✓ (corrected) |
| Keep PENDING | TradeORM.status=OPEN as source of truth. | |

**User's choice:** execution_status → SENT after successful External notification channel send.
**Correction:** Use AGENTS.md enum values (PENDING, SENT, EXECUTED, SKIPPED) — not SIGNAL_SENT. Failed send keeps PENDING + structured log event.

---

## SIG-03 Stats Storage

**User specification (freeform):** New strategy_stats table updated idempotently when monitor_trades transitions a TradeORM to CLOSED/STOPPED. Fields: strategy, trade_count, wins, losses, gross_profit_pct, gross_loss_pct, total_pnl_pct, win_rate, profit_factor, updated_at. Stats upsert in same DB transaction as trade close (exactly-once). Store raw inputs (gross_profit_pct, gross_loss_pct) not just derived profit_factor.

---

| Option | Description | Selected |
|--------|-------------|----------|
| One row per strategy, lifetime cumulative | PK = strategy. Simple upsert. | ✓ |
| One row per strategy + calendar month | Rolling windows but complicates daily summary. | |

**User's choice:** One row per strategy, lifetime cumulative. PK = strategy (varchar).

---

| Option | Description | Selected |
|--------|-------------|----------|
| strategy_stats for lifetime, trades for today | Two reads, clean separation. | ✓ |
| Re-query trades for everything | Always fresh but strategy_stats used only by health endpoint. | |

**User's choice:** strategy_stats for cumulative win_rate/PF; trades WHERE opened_at >= today for session stats.

---

**User clarification (freeform):** 
- Strategy source = CandidateSignalORM.strategy via TradeORM → ApprovedSignalORM → CandidateSignalORM join.
- Daily summary content: signals sent, trades closed today by close_reason, daily pnl_pct, MTD pnl_pct if cheap, CB status, mode, top per-strategy lifetime win_rate/profit_factor.
- /health: stay lightweight — add strategies_active count only (not full strategy_stats array). Overrides earlier "Yes" selection on strategy_stats in /health.

---

## Claude's Discretion

- Internal layout of src/execution/ (broker_executor.py stub now vs Phase 8). Recommended: defer to Phase 8.
- StrategyStatsORM upsert pattern (ON CONFLICT DO UPDATE vs SELECT-then-UPDATE). Recommended: PostgreSQL upsert.
- Monitor job ATR source (DB query vs shared in-memory regime). Recommended: query DB for latest H1 candle.
- Test layout for tests/test_monitoring/ and tests/test_execution/. Recommended: mirror tests/test_pipeline/ structure; mock Bot.send_message.

## Deferred Ideas

- Auto-mode broker order placement, partial close, live trailing stop — Phase 8.
- External notification channel bot interactive commands — Phase 8 / v2.
- strategies_performance array in /health — too heavy for a health probe.
- Rolling window stats (30-day, 7-day) in strategy_stats — Phase 8 / v2.
- Retry mechanism for failed External notification channel sends — deferred.
- broker_executor.py stub — Phase 8.
- Daily loss limit tripping circuit breaker — deferred per Phase 6 D-15.
