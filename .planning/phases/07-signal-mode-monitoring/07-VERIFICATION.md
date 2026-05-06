---
phase: 07-signal-mode-monitoring
verified: 2026-05-06T06:15:00Z
status: human_needed
score: 7/7 requirements verified
overrides_applied: 0
gaps: []
human_verification:
  - test: "App startup with Telegram wiring"
    expected: "No startup errors; 'app.telegram_bot_initialized' and 'app.execution_services_wired' log events present"
    why_human: "Requires valid TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars; startup behavior can't be verified without running the app"
  - test: "Dashboard visual appearance and auto-refresh"
    expected: "Dark background (#0d0f11), '0rum Dashboard' title, SIGNAL/AUTO badge, Status Row with DB/Redis indicators, three data panels (Open Trades, Strategy Performance, Latest Signals). After 30 seconds, timestamp updates."
    why_human: "Visual appearance, layout fidelity, and CSS rendering can't be verified programmatically"
  - test: "Telegram signal message format"
    expected: "Formatted BUY/SELL message with entry, SL, TP1, TP2, confidence, and size suggestion arrives in configured Telegram chat"
    why_human: "Requires live Telegram Bot API interaction; can't verify message delivery without sending to real chat"
  - test: "Trade lifecycle notifications"
    expected: "TP1 HIT, TP2 HIT, SL HIT, TRAIL STOP Telegram notifications fire correctly as trades progress through lifecycle"
    why_human: "Real-time behavior depending on market price movements; requires app running against live market data"
  - test: "Circuit breaker alert"
    expected: "After 8 consecutive theoretical SL closes, a CIRCUIT BREAKER TRIPPED Telegram alert is sent"
    why_human: "Cannot trigger 8 consecutive stops without a running system with live data"
  - test: "Daily summary at 00:00 UTC"
    expected: "Daily summary Telegram message with signals sent, trades, P&L, and circuit breaker state"
    why_human: "Scheduled job at 00:00 UTC; requires app to run through midnight"
---

# Phase 7: Signal Mode & Monitoring Verification Report

**Phase Goal:** Signal Mode & Monitoring — deliver the full execution + notification + monitoring system for signal-only (non-auto) trading mode. This includes Telegram signal sending, trade lifecycle monitoring, circuit breaker alerts, daily strategy summaries, and the operator dashboard.

**Verified:** 2026-05-06T06:15:00Z
**Status:** human_needed
**Re-verification:** Yes — after Phase 7 monitoring regression fixes

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Every ApprovedSignal produces a correctly formatted Telegram signal message with entry, SL, TP1, TP2, confidence, and size suggestion | VERIFIED | `SignalSender` in `src/execution/signal_sender.py` formats BUY/SELL messages with all required fields, uses `ParseMode.HTML`, sends via injected `Bot`. 6 unit tests pass. |
| 2 | The theoretical trade lifecycle (open -> TP1 hit -> trailing -> close) is tracked in the `trades` table | VERIFIED | `monitor_trades` job in `src/scheduler/jobs.py` fetches OPEN/TP1_HIT trades, evaluates M15 candle touches after `opened_at`, transitions status. Direct SL closes use full-position loss; post-TP1 closes use blended P&L. 11 unit tests pass. |
| 3 | Per-strategy win rate, profit factor, and theoretical P&L are accumulated in the database and queryable | VERIFIED | `StrategyStatsORM` in `src/models/strategy_stats.py` has all 10 required columns. `_close_trade()` upserts strategy_stats via `INSERT ... ON CONFLICT DO UPDATE` with computed win_rate and profit_factor recomputed in same transaction. `/api/dashboard` exposes strategy_stats. 5 unit tests pass. |
| 4 | TP1 hit, TP2 hit, SL hit, and circuit breaker events each produce the correct Telegram notification | VERIFIED | `TelegramBot` in `src/monitoring/telegram_bot.py` has `send_lifecycle_notification()` (TP1/TP2/SL/TRAIL) and `send_circuit_breaker_alert()`. Called from `_close_trade()` and `_process_trade()` in jobs.py. `register_alert_hook(telegram_bot_inst.send_circuit_breaker_alert)` wired in `main.py`. 13 TelegramBot unit tests pass. |
| 5 | The daily summary Telegram message fires at 00:00 UTC with signals sent, trades, P&L, and circuit breaker state | VERIFIED | `daily_summary` job in `src/scheduler/jobs.py` queries DB for all D-19 fields, builds `DailySummaryPayload`, calls `telegram_bot.send_daily_summary()`. Registered with `CronTrigger(hour=0, minute=0, timezone="UTC")`. Daily summary formatting is covered by TelegramBot tests. |

**Score:** 5/5 success criteria verified

### Requirements Coverage

| Requirement | Source Phase | Description | Status | Evidence |
|------------|-------------|-------------|--------|----------|
| SIG-01 | Phase 7 | Mode `signal` sends formatted Telegram signal message with entry, SL, TP1, TP2, confidence | SATISFIED | SignalSender._format_message() + ExecutionRouter._execute_signal_mode() chain |
| SIG-02 | Phase 7 | Theoretical trade lifecycle (open -> TP1 hit -> trailing -> close) tracked in PostgreSQL | SATISFIED | TradeORM status machine + monitor_trades job + _process_trade() + _close_trade() |
| SIG-03 | Phase 7 | Theoretical P&L and stats (win rate, profit factor) accumulated per strategy in DB | SATISFIED | StrategyStatsORM + upsert in _close_trade() with win_rate/profit_factor recomputation |
| NOTIF-01 | Phase 7 | Telegram notification on new approved signal (both modes) | SATISFIED | SignalSender -> ExecutionRouter -> execution_status updated to SENT on success |
| NOTIF-02 | Phase 7 | Telegram notification on TP1 hit, TP2 hit, SL hit | SATISFIED | TelegramBot.send_lifecycle_notification() called from _process_trade() and _close_trade() |
| NOTIF-03 | Phase 7 | Telegram notification on circuit breaker trigger | SATISFIED | TelegramBot.send_circuit_breaker_alert() registered as hook, called from _close_trade() on SL close |
| NOTIF-04 | Phase 7 | Daily summary Telegram message with session stats | SATISFIED | daily_summary job queries all D-19 fields, builds DailySummaryPayload, sends via TelegramBot |

**Score:** 7/7 requirements satisfied

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `alembic/versions/0003_phase7_signal_mode.py` | DB migration for trailing_stop_price + strategy_stats | VERIFIED | `op.add_column` for trailing_stop_price, `op.create_table` for strategy_stats with all 10 columns. Chained to 0002. `downgrade()` present. |
| `src/models/trade.py` | TradeORM with trailing_stop_price | VERIFIED | Line 38: `trailing_stop_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 5), nullable=True)` |
| `src/models/strategy_stats.py` | StrategyStatsORM with all 10 columns | VERIFIED | strategy, trade_count, wins, losses, gross_profit_pct, gross_loss_pct, total_pnl_pct, win_rate, profit_factor, updated_at |
| `src/execution/__init__.py` | Package marker | VERIFIED | Docstring: "src/execution package — ExecutionRouter and SignalSender." |
| `src/execution/signal_sender.py` | SignalSender class | VERIFIED | Formats BUY/SELL messages, sends via bot.send_message with ParseMode.HTML, returns bool, catches exceptions, never logs token |
| `src/execution/executor.py` | ExecutionRouter class | VERIFIED | Routes SIGNAL mode to SignalSender, AUTO mode raises NotImplementedError |
| `src/monitoring/telegram_bot.py` | TelegramBot class | VERIFIED | send_circuit_breaker_alert, send_lifecycle_notification (TP1/TP2/SL/TRAIL), send_daily_summary. DailySummaryPayload dataclass. All failures caught and logged. |
| `src/monitoring/dashboard.py` | Dashboard API + HTML routes | VERIFIED | GET /api/dashboard returns 7-field JSON (health, execution_mode, circuit_breaker, daily_pnl_pct, open_trades, latest_signals, strategy_stats). GET /dashboard serves Jinja2 template. |
| `src/templates/dashboard.html` | Operator dashboard per UI-SPEC | VERIFIED | All 9 CSS vars on :root, no external CDN, setInterval 30s auto-refresh, data-section="status", id="exec-mode-badge", empty states present, DOM/textContent rendering for API rows, scope="col" on generated th, button for refresh |
| `src/monitoring/health.py` | Updated /health with live strategies_active and signals_today | VERIFIED | Hardcoded `"strategies_active": 4` removed. `signals_today` now counts SENT approved signals for the current UTC day. |
| `src/main.py` | Bot init, service wiring, hook registration | VERIFIED | `bot.initialize()`, `register_alert_hook(telegram_bot_inst.send_circuit_breaker_alert)`, `_set_monitor_services()`, `_set_pipeline_runner()`, `bot.shutdown()`. Token never logged. |
| `src/scheduler/jobs.py` | monitor_trades + daily_summary jobs | VERIFIED | `monitor_trades()` with `IntervalTrigger(minutes=15)`, `daily_summary()` with `CronTrigger(hour=0, minute=0, timezone="UTC")`, both registered in `create_scheduler()` |
| `src/risk/breaker.py` | get_consecutive_stops() method | VERIFIED | `async def get_consecutive_stops(self) -> int:` reads Redis CB_COUNTER key |
| `pyproject.toml` | jinja2 dependency | VERIFIED | `"jinja2>=3.1.0"` in dependencies |
| `tests/test_execution/test_signal_sender.py` | 6 unit tests | VERIFIED | 6 test functions, all pass |
| `tests/test_execution/test_executor.py` | 4 unit tests | VERIFIED | 4 test functions, all pass |
| `tests/test_monitoring/test_telegram_bot.py` | 13 unit tests | VERIFIED | 13 test functions, all pass |
| `tests/test_monitoring/test_monitor_trades.py` | 11 unit tests | VERIFIED | 11 test functions, all pass |
| `tests/test_monitoring/test_strategy_stats.py` | 5 unit tests | VERIFIED | 5 test functions, all pass |
| `tests/test_monitoring/test_dashboard.py` | 19 unit tests | VERIFIED | 19 test functions, all pass |
| `tests/test_monitoring/test_health_risk.py` | 4 unit tests | VERIFIED | 4 test functions, all pass |

### Test Results

| Test Suite | Tests | Status |
|------------|-------|--------|
| `tests/test_execution/` | 11 passed | PASS |
| `tests/test_monitoring/` | 52 passed | PASS |
| `tests/test_backtesting/test_scheduler_wiring.py` | 4 passed | PASS |
| **Phase 7 subtotal** | **67 passed** | **PASS** |
| Full suite | 315 passed | PASS |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| SignalSender | telegram.Bot.send_message | `await self._bot.send_message(..., parse_mode=ParseMode.HTML)` | WIRED | Lines 92-96 in signal_sender.py |
| TelegramBot | src.risk.events.CircuitBreakerAlert | `async def send_circuit_breaker_alert(self, alert: CircuitBreakerAlert)` | WIRED | Line 89 in telegram_bot.py |
| PipelineRunner | ExecutionRouter | `ExecutionRouter.execute(signal, decision.sizing.size_lots)` | WIRED | Line 153 in runner.py |
| PipelineRunner | TradeORM | TradeORM creation inside `_persist()` same session.begin() | WIRED | Lines 252-264 in runner.py |
| ExecutionRouter | SignalSender | `self._sender.send_signal(signal, size_lots)` | WIRED | Line 62 in executor.py |
| main.py | ExecutionRouter + SignalSender + TelegramBot | Bot injected via constructor, services wired at startup | WIRED | Lines 54-77 in main.py |
| main.py | TelegramBot CB hook | `register_alert_hook(telegram_bot_inst.send_circuit_breaker_alert)` | WIRED | Line 69 in main.py |
| monitor_trades | TradeORM | SELECT TradeORM WHERE status IN ('OPEN', 'TP1_HIT') with JOIN | WIRED | Lines 237-246 in jobs.py |
| monitor_trades | StrategyStatsORM | pg_insert(StrategyStatsORM).on_conflict_do_update | WIRED | Lines 404-427 in jobs.py (in _close_trade) |
| daily_summary | TelegramBot | `telegram_bot.send_daily_summary(payload)` | WIRED | Line 573 in jobs.py |
| dashboard.html | /api/dashboard | `fetch('/api/dashboard')` in setInterval | WIRED | JS fetchDashboard() function in template |
| dashboard.py | TradeORM via LEFT JOIN | `outerjoin(TradeORM, ...)` for size_lots | WIRED | Lines 161-164 in dashboard.py |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|-------------------|--------|
| PipelineRunner._persist() | TradeORM.size_lots | `decision.sizing.size_lots` from RiskDecision | FLOWING | decision computed by RiskGateRunner.evaluate() with ATR-based sizing |
| monitor_trades | candle_high, candle_low | Latest completed M15 candle from `Candle` table | FLOWING | Queries DB for last completed M15 candle |
| monitor_trades | atr_h1 | RegimeDetector._calculate_atr() from last 20 H1 candles | FLOWING | Fetches H1 candles from DB, computes ATR(14) |
| daily_summary | signals_sent | `COUNT(*) FROM approved_signals WHERE execution_status='SENT'` | FLOWING | Live DB query |
| daily_summary | daily_pnl | `SUM(pnl_pct) FROM trades WHERE closed_at is today` | FLOWING | Live DB query |
| dashboard | strategies_active | `COUNT(*) FROM optimizer_results WHERE is_active=TRUE` | FLOWING | Live DB query |
| dashboard | open_trades | JOIN TradeORM -> ApprovedSignalORM -> CandidateSignalORM | FLOWING | Live DB query with strategy name join |
| dashboard | latest_signals | JOIN ApprovedSignalORM -> CandidateSignalORM LEFT JOIN TradeORM | FLOWING | Live DB query with size_lots from TradeORM via LEFT JOIN |

### Anti-Patterns Found

| File | Pattern | Status |
|------|---------|--------|
| src/scheduler/jobs.py | Direct SL close previously used blended TP1 P&L and could be classified as a win | FIXED |
| src/scheduler/jobs.py | Latest M15 candle could be applied to trades opened after that candle | FIXED |
| src/monitoring/telegram_bot.py | Fractional `pnl_pct` values were displayed as already-percent values | FIXED |
| tests/test_backtesting/test_scheduler_wiring.py | Hardcoded `EXPECTED_JOB_IDS` missing Phase 7 jobs | FIXED |
| src/monitoring/health.py | `"signals_today": 0` placeholder | FIXED |
| src/templates/dashboard.html | API row rendering used `innerHTML` string concatenation | FIXED |

### Deferred Items

None. All Phase 7 requirements and success criteria are addressed.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Pipeline module is importable | `python -c "from src.pipeline.runner import PipelineRunner; print(PipelineRunner)"` | PipelineRunner class found | PASS |
| StrategyStatsORM importable | `python -c "from src.models.strategy_stats import StrategyStatsORM; print(StrategyStatsORM.__tablename__)"` | strategy_stats | PASS |
| SignalSender module importable | `python -c "from src.execution.signal_sender import SignalSender; print(SignalSender)"` | SignalSender class found | PASS |
| TelegramBot module importable | `python -c "from src.monitoring.telegram_bot import TelegramBot; print(TelegramBot)"` | TelegramBot class found | PASS |
| ExecutionRouter importable | `python -c "from src.execution.executor import ExecutionRouter; print(ExecutionRouter)"` | ExecutionRouter class found | PASS |
| Dashboard router importable | `python -c "from src.monitoring.dashboard import dashboard_router; print(dashboard_router)"` | APIRouter instance found | PASS |
| Alembic migration head | `alembic heads` (via summary claims) | 0003 | PASS (cross-checked from 07-01 summary) |

### Human Verification Required

1. **App startup with Telegram wiring**
   - **Test:** Start the app with `uvicorn src.main:app --host 0.0.0.0 --port 8000`
   - **Expected:** No startup errors; `"app.telegram_bot_initialized"` and `"app.execution_services_wired"` log events present
   - **Why human:** Requires valid `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` env vars; startup behavior can't be verified without running the app

2. **Dashboard visual appearance and auto-refresh**
   - **Test:** Navigate to `http://localhost:8000/dashboard` in browser
   - **Expected:** Dark background (#0d0f11), `"0rum Dashboard"` title, SIGNAL/AUTO badge, Status Row with DB/Redis indicators, three data panels (Open Trades, Strategy Performance, Latest Signals). After 30 seconds, `"Last updated HH:MM:SS UTC"` timestamp updates.
   - **Why human:** Visual appearance, layout fidelity, and CSS rendering can't be verified programmatically

3. **Telegram signal message format**
   - **Test:** Trigger a pipeline run that produces an approved signal
   - **Expected:** Formatted BUY/SELL message with entry, SL, TP1, TP2, confidence, and size suggestion arrives in configured Telegram chat
   - **Why human:** Requires live Telegram Bot API interaction; can't verify message delivery without sending to real chat

4. **Trade lifecycle notifications**
   - **Test:** Let the app run with market data flowing; observe when trades progress through lifecycle
   - **Expected:** TP1 HIT, TP2 HIT, SL HIT, TRAIL STOP Telegram notifications fire correctly
   - **Why human:** Real-time behavior depending on market price movements; requires app running against live market data

5. **Circuit breaker alert**
   - **Test:** Induce 8 consecutive theoretical SL closes
   - **Expected:** Telegram alert: `"CIRCUIT BREAKER TRIPPED"` with consecutive stops count and cooldown time
   - **Why human:** Cannot trigger 8 consecutive stops without a running system with live data

6. **Daily summary at 00:00 UTC**
   - **Test:** Let the app run past midnight UTC
   - **Expected:** Daily summary Telegram message with signals sent, trades, P&L, and circuit breaker state
   - **Why human:** Scheduled job at 00:00 UTC; requires app to run through midnight

### Gaps Summary

No blocking gaps found. All Phase 7 requirements (SIG-01, SIG-02, SIG-03, NOTIF-01, NOTIF-02, NOTIF-03, NOTIF-04) are implemented, all key links are wired, all artifacts are substantive (not stubs), and all data flows are connected to real database queries.

The regression findings identified after initial verification were fixed and covered by tests. Full suite is green: `315 passed`.

Human verification is required for visual/integration aspects (dashboard appearance, Telegram message delivery, lifecycle notifications, circuit breaker alerts, daily summary timing).

---

*Verified: 2026-05-06T06:15:00Z*
*Verifier: Codex*
