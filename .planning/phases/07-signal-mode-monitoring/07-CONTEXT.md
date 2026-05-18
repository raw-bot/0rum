# Phase 7: signal-mode-monitoring - Context (Web-Only)

**Gathered:** 2026-04-28
**Updated:** 2026-05-18 (Web-Only Alignment)
**Status:** Executing

<domain>
## Phase Boundary

Phase 7 delivers the complete signal mode runtime plus a comprehensive read-only operator dashboard. Following the 2026-05-06 Monitoring Surface Override, the system is **web-only**:
1. Every `ApprovedSignal` is recorded locally and audited via structured logs (SIG-01 / NOTIF-01). Telegram sending is ABANDONED.
2. A theoretical trade lifecycle (OPEN → TP1_HIT → CLOSED) is tracked in `TradeORM` for every approved signal (SIG-02).
3. Per-strategy win rate, profit factor, and P&L are accumulated in a new `strategy_stats` table and queryable (SIG-03).
4. Monitoring is conducted via a local web dashboard (`/dashboard`) + JSON API (`/api/dashboard`). Telegram notifications are ABANDONED.
5. The dashboard displays bot health, execution mode, circuit breaker state, latest signals, open theoretical trades, recently closed trades, candidate signal decisions, per-strategy stats, and operational events.

What this phase is NOT:
- Auto-mode broker order placement (Phase 8).
- External messaging or notifications — all operator interactions are local.
- Real-time push (WebSocket/SSE) — dashboard polls `/api/dashboard` every 30s.

</domain>

<decisions>
## Implementation Decisions

### Trade Lifecycle Monitor (SIG-02)

- **D-01:** APScheduler job `monitor_trades` runs every 15 min. Job evaluates the latest **completed** M15 candle's HIGH and LOW range for intra-candle level touches.
- **D-02:** H1 ATR(14) used for trailing stop distance updates after `TP1_HIT`. Trailing distance = `1.0 × ATR(H1)`.
- **D-03:** New column `trailing_stop_price` in `TradeORM`. Ratchet rule: update only when new trail is strictly better.
- **D-04:** Post-`TP1_HIT`, both exits (TP2 and Trailing Stop) are active simultaneously.
- **D-05:** Conservative candle resolution: SL wins pre-TP1; Trailing Stop wins post-TP1 on tie.
- **D-06:** Blended P&L on final close: `pnl_pct = 0.5 × pnl_at_tp1 + 0.5 × pnl_at_exit`.
- **D-07:** `BreakerManager.record_stop()` called inline in monitor job after SL close.
- **D-08:** Circuit breaker reset on `pnl_pct > 0`.

### Module Structure (Web-Only)

- **D-09:** `src/execution/executor.py` implements local-only `ExecutionRouter`. `SignalSender` was REMOVED.
- **D-10:** `src/monitoring/dashboard.py` implements the web-only monitoring surface. `TelegramBot` was REMOVED.
- **D-11:** Startup logic in `main.py` is Telegram-free. No `Bot` instantiation or external wiring.
- **D-12:** Circuit breaker alerts are visible via the dashboard's CB-BADGE and operational events banner. Alert hooks are local-only or internal.

### Operator Dashboard (UI)

- **D-21:** Route `/dashboard` served by Jinja2 template `src/templates/dashboard.html`.
- **D-22:** Dashboard is **read-only** with 30s polling.
- **D-23:** `/api/dashboard` payload includes:
  - `health` (DB, Redis, strategies_active)
  - `execution_mode`
  - `circuit_breaker` (tripped, consecutive_stops)
  - `daily_pnl_pct`
  - `open_trades` (JOIN with strategy name)
  - `closed_trades` (Last 20)
  - `candidate_signals` (Last 50 decisions)
  - `latest_signals` (Last 20 approved)
  - `strategy_stats`
  - `operational_events` (connectivity warnings)
- **D-26:** Dashboard styling: dark theme, functional aesthetic, no external CDN dependencies. Dynamic rows rendered via DOM text nodes for safety.

</decisions>

<canonical_refs>
## Canonical References

### Primary Spec
- `AGENTS.md` 2026-05-06 Override — Telegram abandoned; UI web locale prioritaire.
- `AGENTS.md` §13.1 — Signal mode (local-only behavior).
- `AGENTS.md` §13.2 — Trailing stop behavior.

### Project Constraints
- `CLAUDE.md` — Invariants for dashboard safety and local-only execution.
- `.planning/REQUIREMENTS.md` — SIG-01..03, NOTIF-01..04 (reinterpreted as local).

</canonical_refs>

---

*Phase: 07-signal-mode-monitoring*
*Alignment: Web-Only Monitoring*
