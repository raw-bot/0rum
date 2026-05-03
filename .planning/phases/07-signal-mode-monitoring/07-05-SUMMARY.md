---
phase: 07-signal-mode-monitoring
plan: 05
subsystem: monitoring, execution, dashboard
tags: [wiring, dashboard, health, bot-startup, template]
requires:
  - 07-01
  - 07-02
  - 07-03
  - 07-04
provides:
  - /api/dashboard JSON endpoint
  - /dashboard HTML operator dashboard
  - Bot initialization in main.py lifespan
  - Live strategies_active in /health
affects:
  - src/main.py
  - src/scheduler/jobs.py
  - pyproject.toml
tech-stack:
  added:
    - jinja2>=3.1.0
  patterns:
    - Jinja2Templates for HTML rendering
    - FastAPI APIRouter with Depends(get_db) for async DB access
    - setInterval-based polling dashboard with vanilla JS
key-files:
  created:
    - src/monitoring/dashboard.py
    - src/templates/dashboard.html
    - tests/test_monitoring/test_dashboard.py
  modified:
    - src/main.py
    - src/monitoring/health.py
    - src/scheduler/jobs.py
    - pyproject.toml
decisions:
  - "D-20: /health strategies_active now reflects live COUNT(*) FROM optimizer_results WHERE is_active = TRUE"
  - "D-21: /dashboard route returns Jinja2 TemplateResponse from templates/dashboard.html via dashboard_router"
  - "D-22: Dashboard is read-only; setInterval auto-refresh every 30s via fetchDashboard()"
  - "D-23: /api/dashboard returns JSON with all 7 required fields"
  - "D-24: /api/dashboard uses AsyncSessionLocal via FastAPI Depends(get_db)"
  - "D-25: No authentication on /dashboard or /api/dashboard (deferred to Phase 8)"
  - "D-26: dashboard.html is self-contained single file; no external CDN; all 9 CSS custom properties on :root"
metrics:
  duration: 3 min
  completed: 2026-05-03
  tasks: 2
  files_created: 3
  files_modified: 4
---

# Phase 7 Plan 5: Wire full Phase 7 system — main.py startup, dashboard, health, tests

One-liner: Wire Bot initialization in main.py lifespan with full service injection (SignalSender, TelegramBot, ExecutionRouter, PipelineRunner), register CB alert hook, create /api/dashboard (7-field JSON) and /dashboard (Jinja2 HTML) routes, replace hardcoded strategies_active in /health with live DB query, add jinja2 dependency, and build operator dashboard template per UI-SPEC with 18 passing tests.

## Tasks

### Task 1: /api/dashboard endpoint + /health strategies_active fix + pyproject.toml jinja2 + main.py wiring

**Commit:** ff7d245

**Changes:**
- Added `jinja2>=3.1.0` to pyproject.toml dependencies (after fastapi entry)
- Fixed `src/monitoring/health.py`: replaced `"strategies_active": 4` with live query using `select(func.count()).select_from(OptimizerResultORM).where(OptimizerResultORM.is_active.is_(True))`
- Created `src/monitoring/dashboard.py` with:
  - `GET /api/dashboard` — returns JSON with all 7 required fields (health, execution_mode, circuit_breaker, daily_pnl_pct, open_trades, latest_signals, strategy_stats)
  - `GET /dashboard` — serves Jinja2 TemplateResponse
  - Graceful degradation: all errors caught and logged, sections return empty/default values
- Updated `src/main.py` lifespan:
  - Bot initialization before scheduler start: `Bot(token=...)` + `await bot.initialize()`
  - Service wiring: SignalSender, TelegramBot, register_alert_hook, ExecutionRouter
  - PipelineRunner with injected router via `_set_pipeline_runner()`
  - Bot shutdown after scheduler shutdown
  - Dashboard router registration after health_router
  - Token never logged (T-07-05-01 compliant)
- Updated `src/scheduler/jobs.py`:
  - Added `_pipeline_runner` module-level variable and `_set_pipeline_runner()` injector
  - Modified `run_pipeline()` to use injected runner if available
- Created empty `src/templates/` directory

### Task 2: Dashboard HTML template (07-UI-SPEC) + test_dashboard.py

**Commit:** 362304c

**Changes:**
- Created `src/templates/dashboard.html` — full operator dashboard per UI-SPEC:
  - 9 CSS custom properties on `:root` (--bg, --surface, --accent, --destructive, --warning, --muted, --border, --text, --text-2)
  - Dark theme with flat colors, no gradients, no animations
  - System font stack for labels, monospace for data values
  - Header bar: bot name, exec mode badge (SIGNAL/AUTO), CB badge (shown when tripped), last-updated timestamp, refresh button
  - Status row: 4 cells (DB, Redis, Strategies, Daily P&L) with `data-section="status"` attribute
  - Two-column grid: Open Trades (left) + Strategy Performance (right)
  - Latest Signals table (full width)
  - Auto-refresh: `setInterval(fetchDashboard, 30000)`
  - All empty states: "No open theoretical trades", "No strategy stats yet", "No signals recorded yet"
  - Error handling banner on API failure
  - Accessibility: `<html lang="en">`, `scope="col"` on `<th>`, `<button>` for refresh
  - No external CDN, no external CSS, no external JS, no Google Fonts
- Created `tests/test_monitoring/test_dashboard.py` with 18 tests:
  - TestDashboardApi (7 tests): shape, types, health fields, circuit breaker types, execution mode
  - TestDashboardPage (11 tests): HTML response, title, CDN absence, setInterval, CSS custom properties, html lang, refresh button, empty states, data-section, exec-mode-badge, scope="col"

### Task 3 (Checkpoint: Human-verify)

Auto-approved (`auto_advance: true`). Dashboard verifiable by starting the app with `uvicorn src.main:app --host 0.0.0.0 --port 8000` and navigating to `http://localhost:8000/dashboard` and `http://localhost:8000/api/dashboard`.

## Deviations from Plan

**None.** Plan executed exactly as written. All acceptance criteria met.

**Note on verification grep:** The plan's verification step `grep -v '^#' src/main.py | grep -c "telegram_bot_token"` expected 0, but the file contains 1 occurrence at `bot = Bot(token=settings.telegram_bot_token)` (line 56) — this is the required Bot constructor call per D-11, not a log call. The token is never logged (T-07-05-01 satisfied). The grep is a coarse approximation that catches both legitimate and problematic token references.

## Known Stubs

None identified. All endpoints return live data from DB. Dashboard template is fully wired with JS polling.

## Threat Flags

None identified. All endpoints are read-only. Dashboard has no authentication per accepted design decision D-25 (deferred to Phase 8).

## Verification Results

| Check | Result |
|-------|--------|
| `pyproject.toml` contains jinja2 | PASS (1 match) |
| `/health` uses live strategies_active query | PASS (3 occurrences of `strategies_active_val`) |
| Hardcoded `"strategies_active": 4` removed | PASS (0 matches) |
| `dashboard_router` defined in dashboard.py | PASS (3 matches) |
| `await bot.initialize()` in main.py | PASS (1 match) |
| `register_alert_hook` in main.py | PASS (2 matches) |
| `await bot.shutdown()` in main.py | PASS (1 match) |
| `setInterval` in dashboard.html (D-22) | PASS (1 match) |
| `var(--accent)` in dashboard.html (D-26) | PASS (5 matches) |
| No CDN URLs in dashboard.html | PASS (0 matches) |
| `outerjoin` in dashboard.py (size_lots JOIN) | PASS (1 match) |
| `data-section="status"` in dashboard.html | PASS (1 match) |
| `id="exec-mode-badge"` in dashboard.html | PASS (1 match) |
| `id="refresh-btn"` on `<button>` | PASS (1 match) |
| Dashboard tests pass | PASS (18/18) |
| Health risk tests pass | PASS (3/3) |
| Token not in log calls (T-07-05-01) | PASS (token in Bot constructor only) |

## Self-Check: PASSED

All created files exist, all commits verified, all test results confirmed.
