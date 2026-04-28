# Phase 7: signal-mode-monitoring - Context

**Gathered:** 2026-04-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 7 delivers the complete signal mode runtime plus a minimal read-only operator dashboard:
1. Every `ApprovedSignal` produces a formatted Telegram signal message (SIG-01 / NOTIF-01).
2. A theoretical trade lifecycle (OPEN → TP1_HIT → CLOSED) is tracked in `TradeORM` for every approved signal (SIG-02).
3. Per-strategy win rate, profit factor, and P&L are accumulated in a new `strategy_stats` table and queryable (SIG-03).
4. All notification types fire correctly: TP1, TP2, SL, trailing close, circuit breaker, and daily summary (NOTIF-01..04).
5. A read-only operator dashboard (`/dashboard`) displays bot health, execution mode, circuit breaker state, latest signals, open theoretical trades, daily P&L, and per-strategy stats. No trading controls — display only.

What this phase is NOT:
- Auto-mode broker order placement (Phase 8). `ExecutionRouter` auto branch raises `NotImplementedError` here.
- Telegram bot command handling — one-way notifications only.
- Trading controls of any kind in the dashboard — read-only display only.
- Real-time push (WebSocket/SSE) — dashboard polls or is manually refreshed; Telegram handles real-time alerts.

</domain>

<decisions>
## Implementation Decisions

### Trade Lifecycle Monitor (SIG-02)

- **D-01:** New APScheduler job `monitor_trades` runs every 15 min, independent of `run_pipeline`. Clean separation — no coupling to the signal generation flow. Job evaluates the latest **completed** M15 candle's HIGH and LOW range (not close price only) so intra-candle level touches are detected.
- **D-02:** H1 ATR(14) used exclusively for trailing stop distance updates after `TP1_HIT`. Same `regime.atr_value` already computed by `RegimeDetector`; trailing distance = `1.0 × ATR(H1)` per AGENTS.md §13.2.
- **D-03:** New column `trailing_stop_price: Decimal nullable` added to `TradeORM` via Alembic migration. Ratchet rule: update only when new computed trail is better (higher for BUY, lower for SELL) than the stored value. Persistent across restarts; no Redis dependency for trail state.
- **D-04:** After `TP1_HIT`, both exits are active simultaneously — whichever hits first: `tp2_price` reached → `close_reason=TP2`; `trailing_stop_price` breached → `close_reason=TRAIL`.
- **D-05:** Conservative candle resolution (no intrabar order available):
  - Pre-`TP1_HIT`: if both SL and TP1 levels touched in same M15 candle → **SL wins** (`close_reason=SL`, `status=CLOSED`). Worst-case assumption.
  - Post-`TP1_HIT`: if both trailing stop and TP2 touched in same M15 candle → **trailing stop wins** (`close_reason=TRAIL`, `status=CLOSED`). Worst-case assumption.
  - All price level comparisons use `Decimal` (no float).
- **D-06:** Blended P&L on final close: `pnl_pct = 0.5 × pnl_at_tp1_price + 0.5 × pnl_at_exit_price`. Single `TradeORM` row, one `CLOSED` event. No separate partial-close row.
- **D-07:** `BreakerManager.record_stop(trade_id, strategy)` called inline in the monitor job immediately after setting `status=CLOSED, close_reason=SL`. Same code path, same job execution.
- **D-08:** Win definition for circuit breaker reset: `pnl_pct > 0` on blended close → `BreakerManager.record_win()`. Includes TRAIL exits that close in profit — consistent with AGENTS.md §12.3 "first winning trade resets counter."

### Module Structure

- **D-09:** Phase 7 creates both `src/execution/` and `src/monitoring/telegram_bot.py`:
  - `src/execution/signal_sender.py` — formats and sends the initial BUY/SELL Telegram signal message (SIG-01 / NOTIF-01). Called by `ExecutionRouter`.
  - `src/monitoring/telegram_bot.py` — handles all lifecycle notifications (TP1 HIT, TP2 HIT, SL, TRAIL), circuit breaker alert (NOTIF-03), and daily summary (NOTIF-04). Called by the monitor job.
- **D-10:** `ExecutionRouter` (`src/execution/executor.py`) is created in Phase 7 with:
  - Signal mode branch: calls `signal_sender.send_signal(signal, size_lots)` then creates `TradeORM` row (via `_persist`).
  - Auto mode branch: raises `NotImplementedError` — Phase 8 extension point.
  - Reads `settings.execution_mode` to route.
- **D-11:** Single `Bot(token=settings.telegram_bot_token)` instance instantiated in `main.py` at startup. Injected into `SignalSender.__init__` and `TelegramBot.__init__`. One HTTP connection pool, no duplicate token reads.
- **D-12:** Circuit breaker alert hook registered at app startup in `main.py`: `register_alert_hook(telegram_bot.send_circuit_breaker_alert)`. Wires Phase 6's `CircuitBreakerAlert` event surface to Phase 7's Telegram delivery.

### size_lots Threading

- **D-13:** `risk_passed` list type changes from `list[tuple[CandidateSignal, float]]` to `list[tuple[CandidateSignal, float, RiskDecision]]`. The full `RiskDecision` (including `sizing.size_lots`, `sizing.vol_factor`, `sizing.concentration_reduced`) is threaded through to `_persist()` and to `ExecutionRouter`. All downstream code unpacks the tuple accordingly.
- **D-14:** `TradeORM` row creation moves inside `_persist()`, in the **same SQLAlchemy transaction** as `ApprovedSignalORM` flush. Atomic: no approved signal row exists without a corresponding trade row. `TradeORM.size_lots` is populated from `decision.sizing.size_lots`.
- **D-15:** `ApprovedSignalORM.execution_status` lifecycle:
  - Set to `PENDING` on creation (existing behavior).
  - Updated to `SENT` after successful `signal_sender.send_signal()` call (AGENTS.md value).
  - If Telegram send fails: keep `PENDING`, emit structured `execution.send_failed` log event with strategy, direction, error. No silent discard.

### SIG-03 Stats Storage

- **D-16:** New `strategy_stats` table with one row per strategy (PK = `strategy: varchar(30)`), lifetime cumulative:
  - Columns: `strategy`, `trade_count`, `wins`, `losses`, `gross_profit_pct`, `gross_loss_pct`, `total_pnl_pct`, `win_rate`, `profit_factor`, `updated_at`.
  - Stores raw inputs (`gross_profit_pct`, `gross_loss_pct`) so `profit_factor` can always be recomputed correctly. Never store only derived values.
- **D-17:** Stats upsert happens in the **same DB transaction** as the `TradeORM` status transition to `CLOSED`/`STOPPED`. Exactly-once counting guaranteed — no separate job or eventual consistency.
- **D-18:** Strategy name source: join `TradeORM.approved_signal_id → ApprovedSignalORM.candidate_signal_id → CandidateSignalORM.strategy`. `ApprovedSignalORM` has no strategy field. `strategy_stats.strategy` stores the string value from this join.
- **D-19:** Daily summary (NOTIF-04, APScheduler `daily_summary` job at 00:00 UTC) content:
  - Signals sent today: `COUNT(*) FROM approved_signals WHERE DATE(created_at) = today AND execution_status = 'SENT'`
  - Trades closed today by `close_reason`: `GROUP BY close_reason FROM trades WHERE DATE(closed_at) = today`
  - Daily `pnl_pct`: sum of `pnl_pct` from today's closed trades
  - MTD `pnl_pct` if cheap (single aggregate query from start of current month)
  - Circuit breaker status (from `BreakerManager.is_tripped()`)
  - Execution mode (from settings)
  - Top per-strategy lifetime `win_rate` and `profit_factor` from `strategy_stats`
- **D-20:** `/health` endpoint stays lightweight. Phase 7 adds only `strategies_active` count (number of `optimizer_results` rows WHERE `is_active = TRUE`) if not already present. Full `strategy_stats` array is NOT exposed via `/health`.

### Operator Dashboard (UI)

- **D-21:** New route `/dashboard` served by FastAPI as a single HTML page (Jinja2 template or inline HTML with `HTMLResponse`). No separate frontend build step — plain HTML + minimal CSS + vanilla JS (or Alpine.js). No React, no bundler.
- **D-22:** Dashboard is **read-only**. No forms, no buttons that mutate state. The only interactive element is a manual refresh button (or auto-refresh via `<meta http-equiv="refresh">` or `setInterval` fetch).
- **D-23:** Dashboard data is served by a new `/api/dashboard` JSON endpoint that aggregates: bot health (DB + Redis reachability), execution mode (from settings), circuit breaker state (`BreakerManager.is_tripped()`), latest 20 approved signals (from `approved_signals` table), open theoretical trades (from `trades WHERE status IN ('OPEN','TP1_HIT')`), daily P&L (sum of today's closed `pnl_pct`), per-strategy stats (from `strategy_stats`).
- **D-24:** `/api/dashboard` reuses the same DB session pattern as `/health` (`AsyncSessionLocal`). No new DB connection pool.
- **D-25:** No authentication on the dashboard in Phase 7 — operator tool, runs locally or behind a firewall. Authentication is a Phase 8/v2 concern.
- **D-26:** Dashboard styling: dark theme, minimal. Functional over beautiful — a trading terminal aesthetic, not a marketing page. No external CDN dependencies in production; self-contained single file.

### Claude's Discretion

- Internal layout of `src/execution/` (whether `broker_executor.py` stub is created now or deferred to Phase 8). Recommended: defer — Phase 8 adds it when auto mode is implemented.
- Whether `StrategyStatsORM` uses `ON CONFLICT DO UPDATE` (upsert via PostgreSQL) or SELECT-then-UPDATE pattern. Recommended: PostgreSQL upsert (`INSERT ... ON CONFLICT (strategy) DO UPDATE`) — matches async SQLAlchemy 2.0 pattern.
- Whether monitor job fetches the latest H1 candle ATR from DB or reuses the in-memory regime from the last pipeline run. Recommended: query DB for the latest completed H1 candle and compute ATR via the existing `RegimeDetector` or a lightweight ATR helper — avoids shared mutable state between jobs.
- Test layout for `tests/test_monitoring/` and `tests/test_execution/`. Recommended: mirror existing `tests/test_pipeline/` structure; mock `Bot.send_message` for Telegram unit tests.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Primary Spec (signal mode behavior + message formats)
- `AGENTS.md` §13.1 — Signal mode behavior: signal sending, theoretical tracking start, `execution_status = 'SENT'` on delivery.
- `AGENTS.md` §13.2 — Trailing stop behavior post-TP1: trail distance = `1.0 × ATR(H1)`, ratchet only, updated per H1 candle.
- `AGENTS.md` §13.3 — Transition signal → auto criteria (not implemented in Phase 7; context only).
- `AGENTS.md` §13.4 — `ExecutionRouter` skeleton: `ExecutionMode.SIGNAL` branch and `ExecutionMode.AUTO` branch structure.
- `AGENTS.md` §14.1 — Telegram notification message types and emoji format strings (BUY/SELL signal, TP1 HIT, TP2 HIT, STOPPED, CIRCUIT BREAKER, daily summary).
- `AGENTS.md` §14.3 — Daily summary format and fields.
- `AGENTS.md` §15 — APScheduler job table: `daily_summary` at 00:00 UTC. **Note: `monitor_trades` job (every 15 min) is NOT in §15 — add it as a new job.**

### Risk Integration (Phase 6 contracts Phase 7 must honor)
- `.planning/phases/06-risk-management/06-CONTEXT.md` D-01 — `TradeORM` is Phase 7's to write; Phase 6 reads only.
- `.planning/phases/06-risk-management/06-CONTEXT.md` D-08 — `PositionSizing.size_lots` is logged by Phase 6 but NOT persisted to DB; Phase 7 persists it to `TradeORM.size_lots`.
- `.planning/phases/06-risk-management/06-CONTEXT.md` D-11 — `BreakerManager` public interface: `record_stop(trade_id, strategy)`, `record_win()`, `is_tripped()`.
- `.planning/phases/06-risk-management/06-CONTEXT.md` D-13 — `CircuitBreakerAlert` Pydantic DTO fields; `register_alert_hook()` callable surface.

### Project Constraints
- `CLAUDE.md` (root) — "Not implemented yet: src/execution/, src/monitoring/telegram_bot.py." Phase 7 creates both. Safe-modification rules and import invariants.
- `.planning/PROJECT.md` — Signal mode ships before auto (4-week validation gate). Risk parameters are env vars, not code.
- `.planning/REQUIREMENTS.md` — SIG-01, SIG-02, SIG-03, NOTIF-01, NOTIF-02, NOTIF-03, NOTIF-04.
- `.planning/ROADMAP.md` — Phase 7 success criteria (4 items).

### Existing Code to Read Before Implementing
- `src/pipeline/runner.py` — Integration point for D-13 (risk_passed tuple type change) and D-14 (TradeORM creation in `_persist()`). Lines ~94–135: risk step, `approved_ranked`, `_persist()` call.
- `src/models/signal.py` — `ApprovedSignalORM.execution_status` (default `PENDING`; update to `SENT` post-send). `CandidateSignalORM.strategy` — source for `strategy_stats.strategy` via join.
- `src/models/trade.py` — `TradeORM` fields: `status` (OPEN, TP1_HIT, CLOSED, STOPPED), `close_reason` (TP1, TP2, SL, TRAIL, MANUAL, CIRCUIT_BREAKER), `pnl`, `pnl_pct`. Add `trailing_stop_price: Decimal nullable` via migration (D-03).
- `src/risk/__init__.py` — `BreakerManager`, `register_alert_hook` public surface.
- `src/risk/events.py` — `RiskDecision` (contains `sizing: PositionSizing | None`), `CircuitBreakerAlert` DTO.
- `src/monitoring/health.py` — Add `strategies_active` count from `optimizer_results WHERE is_active = TRUE` (D-20).
- `src/config.py` — `telegram_bot_token`, `telegram_chat_id`, `execution_mode` already present (lines ~236–237, ~944 area).
- `src/main.py` — App startup: instantiate `Bot`, inject into `SignalSender` + `TelegramBot`, register CB hook, register `monitor_trades` scheduler job.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/risk/events.py:RiskDecision` — contains `sizing.size_lots` (Decimal), `sizing.vol_factor`, `sizing.concentration_reduced`. Thread this through `risk_passed` tuples to `_persist()` (D-13).
- `src/risk/breaker.py:BreakerManager` — `record_stop()`, `record_win()`, `is_tripped()` ready for Phase 7 to call from monitor job.
- `src/risk/hooks.py:register_alert_hook()` — CB alert hook surface, register at startup with `telegram_bot.send_circuit_breaker_alert`.
- `src/backtesting/regime_detector.py:RegimeDetector` — ATR(14) on H1 already computed; monitor job queries latest completed H1 candle and uses this or a lightweight ATR helper for trail updates.
- `src/models/trade.py:TradeORM` — Fields for lifecycle: `status`, `close_reason`, `pnl`, `pnl_pct`, `closed_at`. Add `trailing_stop_price` via migration.
- `src/models/optimizer_result.py` — `is_active` flag; query `COUNT(*) WHERE is_active = TRUE` for `strategies_active` in health.
- `AsyncSessionLocal` — used by risk gates and health; monitor job follows the same async session pattern.

### Established Patterns
- Async everywhere (`async def` on every I/O method in scheduler jobs and ORM operations).
- structlog at module level; `execution.send_failed`, `monitor.trade_closed`, `monitor.tp1_hit`, etc. as event keys.
- APScheduler jobs in `src/scheduler/jobs.py` — add `monitor_trades` (interval 15 min) and `daily_summary` (cron 00:00 UTC) here.
- Pydantic v2 for new DTOs (`TelegramSignalMessage`, `DailySummaryPayload` if needed).
- DTO/ORM separation: signal format logic in `SignalSender` (no ORM imports), ORM writes in `_persist()` and monitor job.
- PostgreSQL upsert (`INSERT ... ON CONFLICT DO UPDATE`) for `strategy_stats` incremental accumulation.

### Integration Points
- `src/pipeline/runner.py:_persist()` — Add `TradeORM` row creation here (D-14). Receive `RiskDecision` from threaded tuple (D-13). Update `execution_status` to `SENT` after `ExecutionRouter.execute()` returns successfully.
- `src/scheduler/jobs.py` — New `monitor_trades` job (15-min interval). Existing `run_pipeline` and `run_optimizer` jobs stay unchanged.
- `src/main.py` — Startup sequence: validate settings → instantiate `Bot` → create `SignalSender(bot)` + `TelegramBot(bot)` → `register_alert_hook(telegram_bot.send_circuit_breaker_alert)` → start scheduler with all jobs.
- `src/monitoring/health.py` — Add `strategies_active` field from optimizer query.

</code_context>

<specifics>
## Specific Ideas

- Telegram message format per AGENTS.md §14.1: `🟢 BUY XAUUSD`, entry/SL/TP1/TP2 prices, confidence score, size suggestion in lots (from `sizing.size_lots`).
- Daily summary format per AGENTS.md §14.3: signals count, trades closed by reason, P&L (daily + MTD), CB status, mode, per-strategy lifetime win_rate/PF.
- Conservative candle resolution: no coin-flip when both SL and TP levels touched intra-candle. SL always wins pre-TP1_HIT; trailing stop always wins post-TP1_HIT. This understates performance vs overstating — correct bias for theoretical tracking.
- Trailing stop ratchet: stored `trailing_stop_price` is only overwritten when the new value is strictly better (higher for BUY, lower for SELL). Price comparison in Decimal throughout.
- Blended P&L formula: `pnl_pct = 0.5 × ((exit_tp1 - entry) / entry × direction_sign) + 0.5 × ((exit_final - entry) / entry × direction_sign)` where direction_sign = +1 for BUY, -1 for SELL.
- `strategy_stats` upsert pattern: `INSERT INTO strategy_stats (...) VALUES (...) ON CONFLICT (strategy) DO UPDATE SET wins = strategy_stats.wins + EXCLUDED.wins, ...` — atomic, no SELECT-then-UPDATE race.

</specifics>

<deferred>
## Deferred Ideas

- Auto-mode broker order placement, partial close at TP1, live trailing stop — Phase 8.
- Telegram bot command handling (interactive commands) — Phase 8 / v2.
- `strategies_performance` array in `/health` response — deferred; too heavy for a health probe.
- Rolling window stats (30-day, 7-day) in `strategy_stats` — lifetime cumulative only in Phase 7; windowed view is a Phase 8/v2 enhancement.
- Retry mechanism for failed Telegram sends — Phase 7 logs `PENDING` + error; retry logic (e.g., dead-letter queue) deferred.
- `broker_executor.py` stub in `src/execution/` — Phase 8 adds it when auto mode is implemented.
- Daily loss limit tripping the circuit breaker (AGENTS.md §12.1 Gate 1 hint) — explicitly deferred per Phase 6 D-15.

</deferred>

---

*Phase: 07-signal-mode-monitoring*
*Context gathered: 2026-04-28*
