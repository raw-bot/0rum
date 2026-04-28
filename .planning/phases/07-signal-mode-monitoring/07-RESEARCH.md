# Phase 7: Signal Mode & Monitoring — Research

**Researched:** 2026-04-28
**Domain:** Telegram signal delivery, theoretical trade lifecycle tracking, APScheduler jobs, FastAPI dashboard, PostgreSQL upsert
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Trade Lifecycle Monitor (SIG-02)**
- D-01: `monitor_trades` APScheduler job runs every 15 min, independent of `run_pipeline`. Evaluates latest completed M15 candle HIGH/LOW range for intra-candle level touches.
- D-02: H1 ATR(14) used for trailing stop distance after TP1_HIT. Trail distance = `1.0 × ATR(H1)`. Monitor job queries latest completed H1 candles from DB and calls `RegimeDetector._calculate_atr()`.
- D-03: New column `trailing_stop_price: Decimal nullable` added to `TradeORM` via Alembic migration. Ratchet only — update when new value is strictly better than stored value.
- D-04: After TP1_HIT, both tp2_price and trailing_stop_price are active simultaneously — whichever hits first closes the trade.
- D-05: Conservative candle resolution — pre-TP1_HIT: SL wins if both SL and TP1 touched in same M15 candle. Post-TP1_HIT: trailing stop wins if both trailing stop and TP2 touched in same M15 candle.
- D-06: Blended P&L: `pnl_pct = 0.5 × pnl_at_tp1_price + 0.5 × pnl_at_exit_price`. Single TradeORM row, one CLOSED event. No separate partial-close row.
- D-07: `BreakerManager.record_stop(trade_id, strategy)` called inline in monitor job immediately after setting `status=CLOSED, close_reason=SL`.
- D-08: Win definition: `pnl_pct > 0` on blended close → `BreakerManager.record_win()`. Includes TRAIL exits that close in profit.

**Module Structure**
- D-09: Phase 7 creates `src/execution/signal_sender.py` and `src/monitoring/telegram_bot.py`.
- D-10: `ExecutionRouter` in `src/execution/executor.py` — signal branch calls `signal_sender.send_signal(signal, size_lots)` then creates TradeORM row; auto branch raises `NotImplementedError`.
- D-11: Single `Bot(token=settings.telegram_bot_token)` instance in `main.py`. Injected into `SignalSender.__init__` and `TelegramBot.__init__`.
- D-12: CB alert hook registered at startup: `register_alert_hook(telegram_bot.send_circuit_breaker_alert)`.

**size_lots Threading**
- D-13: `risk_passed` list type changes to `list[tuple[CandidateSignal, float, RiskDecision]]`. Full `RiskDecision` threaded through to `_persist()` and `ExecutionRouter`.
- D-14: `TradeORM` row creation inside `_persist()`, in the same SQLAlchemy transaction as `ApprovedSignalORM` flush. Atomic pair — no approved signal without a corresponding trade.
- D-15: `ApprovedSignalORM.execution_status` lifecycle: PENDING on creation → SENT after successful send. On failure: keep PENDING, emit `execution.send_failed` structured log. No silent discard.

**SIG-03 Stats Storage**
- D-16: New `strategy_stats` table. PK = `strategy varchar(30)`. Lifetime cumulative. Columns: `strategy`, `trade_count`, `wins`, `losses`, `gross_profit_pct`, `gross_loss_pct`, `total_pnl_pct`, `win_rate`, `profit_factor`, `updated_at`.
- D-17: Stats upsert in same DB transaction as TradeORM status transition to CLOSED/STOPPED. Exactly-once.
- D-18: Strategy name via join: `TradeORM.approved_signal_id → ApprovedSignalORM.candidate_signal_id → CandidateSignalORM.strategy`.
- D-19: Daily summary content: signals sent today, trades closed by close_reason, daily pnl_pct, MTD pnl_pct, CB status, execution mode, top per-strategy lifetime win_rate and profit_factor.
- D-20: `/health` adds `strategies_active` count (COUNT(*) FROM optimizer_results WHERE is_active = TRUE). Full strategy_stats array NOT in /health.

**Operator Dashboard (UI)**
- D-21: `/dashboard` route — single Jinja2 template at `src/templates/dashboard.html`. No React, no bundler.
- D-22: Dashboard is read-only. No mutating forms or buttons. Only interactive element: Refresh button + auto-refresh every 30 seconds via `setInterval`.
- D-23: `/api/dashboard` JSON endpoint aggregates: bot health (DB + Redis), execution mode, CB state, latest 20 approved signals, open theoretical trades (status IN ('OPEN','TP1_HIT')), daily P&L, per-strategy stats.
- D-24: `/api/dashboard` uses `AsyncSessionLocal` — same DB session pattern as `/health`.
- D-25: No authentication on dashboard in Phase 7.
- D-26: Dark theme, minimal, self-contained single HTML file. No external CDN. System fonts only.

### Claude's Discretion
- Internal layout of `src/execution/` — defer `broker_executor.py` stub to Phase 8.
- `StrategyStatsORM` upsert pattern — use PostgreSQL `INSERT ... ON CONFLICT (strategy) DO UPDATE`.
- Monitor job ATR source — query DB for latest completed H1 candles; use `RegimeDetector._calculate_atr()` or lightweight ATR helper.
- Test layout for `tests/test_monitoring/` and `tests/test_execution/` — mirror `tests/test_pipeline/` structure; mock `Bot.send_message` for Telegram unit tests.

### Deferred Ideas (OUT OF SCOPE)
- Auto-mode broker order placement, partial close at TP1, live trailing stop — Phase 8.
- Telegram bot command handling — Phase 8/v2.
- `strategies_performance` array in `/health` — deferred.
- Rolling window stats (30-day, 7-day) — lifetime cumulative only in Phase 7.
- Retry mechanism for failed Telegram sends.
- `broker_executor.py` stub in `src/execution/`.
- Daily loss limit tripping circuit breaker — deferred per Phase 6 D-15.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SIG-01 | Mode `signal` sends formatted Telegram signal message with entry, SL, TP1, TP2, confidence | `SignalSender.send_signal()` calls `Bot.send_message()`; message format from AGENTS.md §13.1 |
| SIG-02 | Theoretical trade lifecycle (open → TP1 hit → trailing → close) tracked in PostgreSQL | `monitor_trades` APScheduler job; TradeORM status machine; trailing_stop_price column via migration |
| SIG-03 | Theoretical P&L and stats (win rate, profit factor) accumulated per strategy in DB | `strategy_stats` table with PostgreSQL upsert in same transaction as TradeORM status update |
| NOTIF-01 | Telegram notification on new approved signal (both modes) | `SignalSender.send_signal()` with Bot.send_message; execution_status updated to SENT |
| NOTIF-02 | Telegram notification on TP1 hit, TP2 hit, SL hit | `TelegramBot` lifecycle notification methods called from `monitor_trades` job |
| NOTIF-03 | Telegram notification on circuit breaker trigger | `register_alert_hook(telegram_bot.send_circuit_breaker_alert)` at startup; hook already wired in Phase 6 |
| NOTIF-04 | Daily summary Telegram message with session stats | `daily_summary` APScheduler cron job at 00:00 UTC; format from AGENTS.md §14.3 |
</phase_requirements>

---

## Summary

Phase 7 delivers the complete signal-mode runtime: Telegram signal delivery, a 15-minute trade lifecycle monitor, per-strategy stats accumulation, all notification types, and a read-only operator dashboard. The codebase through Phase 6 is complete and stable. The core abstractions Phase 7 depends on (`BreakerManager`, `register_alert_hook`, `RiskDecision`, `TradeORM`) are verified present and working. No new external libraries are required — `python-telegram-bot>=21.0` is already in `pyproject.toml`. The primary new infrastructure is: two new modules (`src/execution/`, `src/monitoring/telegram_bot.py`), two new DB objects (`strategy_stats` table, `trailing_stop_price` column on `trades`), two new APScheduler jobs (`monitor_trades`, `daily_summary`), two new FastAPI routes (`/dashboard`, `/api/dashboard`), and one Jinja2 template.

The most structurally significant change is D-13: threading `RiskDecision` through `risk_passed` tuples changes the signature of `_persist()` and the status loop in `PipelineRunner.run()`. This must be done carefully to avoid breaking the existing pipeline. The second most complex piece is `monitor_trades` — it operates on completed M15 candles, performs Decimal comparisons across all open/TP1_HIT trades, calls `BreakerManager` on SL, and upserts `strategy_stats` atomically with the trade close — all within a single async session.

**Primary recommendation:** Implement in wave order: (1) DB migration + ORM for `strategy_stats` and `trailing_stop_price`, (2) Telegram delivery layer (`SignalSender` + `TelegramBot`), (3) `ExecutionRouter` + `_persist()` changes, (4) `monitor_trades` job, (5) `daily_summary` job, (6) dashboard endpoint + template. Tests should mock `Bot.send_message` using `AsyncMock` throughout.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Signal Telegram delivery | API / Backend (`SignalSender`) | — | One-way push from bot logic; no browser involved |
| Trade lifecycle monitoring | API / Backend (`monitor_trades` job) | Database / Storage | Scheduler job reads candles, writes TradeORM |
| Strategy stats accumulation | Database / Storage | API / Backend | Pure DB upsert within TradeORM close transaction |
| Circuit breaker Telegram alert | API / Backend (`TelegramBot`) | — | Hook surface already in Phase 6; Phase 7 registers receiver |
| Daily summary generation | API / Backend (`daily_summary` job) | Database / Storage | Aggregation queries feed Telegram message |
| Dashboard data aggregation | API / Backend (`/api/dashboard`) | Database / Storage | JSON endpoint; browser polls it |
| Dashboard HTML delivery | API / Backend (`/dashboard` route) | — | Jinja2 TemplateResponse from FastAPI |
| Operator display rendering | Browser / Client | — | Vanilla JS setInterval polling `/api/dashboard` |

---

## Standard Stack

### Core (all already in pyproject.toml)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| python-telegram-bot | >=21.0 [VERIFIED: pyproject.toml] | Telegram Bot API wrapper | Project dependency; `Bot` class used standalone (no Application needed for one-way sends) |
| SQLAlchemy | >=2.0.0 [VERIFIED: pyproject.toml] | ORM + async sessions | Project standard; `mapped_column`, `AsyncSessionLocal` pattern established |
| APScheduler | >=3.10.0 [VERIFIED: pyproject.toml] | Scheduled jobs | Project standard; `CronTrigger` and `IntervalTrigger` already used |
| FastAPI | >=0.111.0 [VERIFIED: pyproject.toml] | Web framework | Project standard; `APIRouter`, `Depends` patterns established |
| Jinja2 | Bundled with FastAPI [ASSUMED] | HTML template rendering | Required by D-21; FastAPI's `Jinja2Templates` class |
| Alembic | >=1.13.0 [VERIFIED: pyproject.toml] | DB migrations | Project standard; two migrations already exist |
| Pydantic v2 | >=2.7.0 [VERIFIED: pyproject.toml] | DTOs | Project standard; frozen models for cross-phase DTOs |

### New Supporting Patterns (no new packages)

| Pattern | Purpose | When to Use |
|---------|---------|-------------|
| `sqlalchemy.dialects.postgresql.insert` | PostgreSQL upsert for `strategy_stats` | Any incremental accumulation into a single row per strategy |
| `fakeredis.FakeAsyncRedis` | Already used in Phase 6 tests; mock BreakerManager in monitor tests | Any test touching BreakerManager |
| `unittest.mock.AsyncMock` | Mock `Bot.send_message` in Telegram unit tests | All Telegram delivery tests |

### No New Packages Required

The full Phase 7 implementation requires zero new `pyproject.toml` entries. All libraries are already declared.

---

## Architecture Patterns

### System Architecture Diagram

```
APScheduler (15-min interval)
  └─► monitor_trades()
        │
        ├─► SELECT open/TP1_HIT trades FROM trades
        ├─► SELECT latest M15 candle (complete=True, desc limit 1)
        ├─► SELECT latest 15 H1 candles (for ATR)
        │
        ├─► For each OPEN trade:
        │     ├─► Check SL vs M15 [low..high]
        │     ├─► Check TP1 vs M15 [low..high]
        │     └─► Worst-case: SL wins if both touched
        │
        ├─► For each TP1_HIT trade:
        │     ├─► Compute new trail = entry ± 1.0×ATR(H1)
        │     ├─► Ratchet: update trailing_stop_price if strictly better
        │     ├─► Check trailing_stop vs M15 [low..high]
        │     ├─► Check TP2 vs M15 [low..high]
        │     └─► Worst-case: trailing stop wins if both touched
        │
        └─► On CLOSE event (single async session):
              ├─► TradeORM.status → CLOSED, pnl_pct = blended formula
              ├─► UPSERT strategy_stats (ON CONFLICT strategy DO UPDATE)
              ├─► BreakerManager.record_stop() if SL (may emit CircuitBreakerAlert)
              ├─► BreakerManager.record_win() if pnl_pct > 0
              └─► TelegramBot.send_lifecycle_notification(trade, close_reason)

APScheduler (cron 00:00 UTC)
  └─► daily_summary()
        ├─► DB queries: signals sent today, trades by close_reason, pnl
        ├─► BreakerManager.is_tripped()
        └─► TelegramBot.send_daily_summary(payload)

PipelineRunner._persist() [modified]
  ├─► ApprovedSignalORM (execution_status=PENDING)  ─┐
  ├─► TradeORM (status=OPEN, size_lots from RiskDecision) ─┘ same flush
  └─► ExecutionRouter.execute(signal, decision.sizing.size_lots)
        ├─► SIGNAL mode: SignalSender.send_signal() → Bot.send_message()
        │     └─► On success: UPDATE execution_status = SENT
        │     └─► On failure: log execution.send_failed, keep PENDING
        └─► AUTO mode: raise NotImplementedError

GET /dashboard
  └─► Jinja2Templates.TemplateResponse("dashboard.html", ...)
        └─► Template JS: setInterval(() => fetch('/api/dashboard'), 30000)

GET /api/dashboard
  └─► Aggregate from DB + Redis:
        health(db, redis, strategies_active)
        execution_mode
        circuit_breaker (is_tripped, consecutive_stops)
        daily_pnl_pct
        open_trades (status IN OPEN, TP1_HIT)
        latest_signals (last 20 approved_signals)
        strategy_stats (all rows)
```

### Recommended Project Structure (new files only)

```
src/
├── execution/
│   ├── __init__.py
│   ├── executor.py          # ExecutionRouter — signal/auto routing
│   └── signal_sender.py     # SignalSender — formats + sends Telegram signal
├── monitoring/
│   ├── telegram_bot.py      # TelegramBot — lifecycle + CB + daily_summary notifications
│   └── health.py            # MODIFIED: add strategies_active real query (D-20)
├── models/
│   ├── trade.py             # MODIFIED: add trailing_stop_price column
│   └── strategy_stats.py    # NEW: StrategyStatsORM
├── scheduler/
│   └── jobs.py              # MODIFIED: add monitor_trades + daily_summary jobs
├── pipeline/
│   └── runner.py            # MODIFIED: D-13 tuple type, D-14 TradeORM in _persist
├── templates/
│   └── dashboard.html       # NEW: Jinja2 template per UI-SPEC
└── main.py                  # MODIFIED: Bot instantiation, hook registration, templates mount

alembic/versions/
└── 0003_phase7_signal_mode.py   # NEW: trailing_stop_price + strategy_stats
```

### Pattern 1: Standalone Bot Usage (python-telegram-bot v21+)

In v21+, `Bot` requires initialization via `async with Bot(...) as bot:` OR explicit `await bot.initialize()` / `await bot.shutdown()`. For a long-lived FastAPI app, call `await bot.initialize()` in the lifespan startup and `await bot.shutdown()` in shutdown. [VERIFIED: Context7 /python-telegram-bot/python-telegram-bot]

```python
# src/main.py — lifespan startup
from telegram import Bot

bot = Bot(token=settings.telegram_bot_token)
await bot.initialize()  # opens HTTP session
# inject into SignalSender + TelegramBot

# lifespan shutdown
await bot.shutdown()
```

**Critical:** Do NOT use `async with Bot(...) as bot:` in a job or request handler — that opens and closes an HTTP session per call. Use the single long-lived initialized instance.

### Pattern 2: send_message with ParseMode

```python
# Source: Context7 /python-telegram-bot/python-telegram-bot
from telegram.constants import ParseMode

await bot.send_message(
    chat_id=settings.telegram_chat_id,
    text=message_text,
    parse_mode=ParseMode.HTML,  # simpler escaping than MarkdownV2
)
```

Use `ParseMode.HTML` rather than `ParseMode.MARKDOWN_V2`. MarkdownV2 requires escaping many characters (`.`, `!`, `(`, `)`, `-`, etc.) that appear frequently in price strings like `2340.50`. HTML only requires `<`, `>`, `&` to be escaped — much simpler for price/percentage formatting.

### Pattern 3: PostgreSQL Upsert for strategy_stats

```python
# Source: SQLAlchemy 2.0 docs — dialects/postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert
from src.models.strategy_stats import StrategyStatsORM

stmt = pg_insert(StrategyStatsORM).values(
    strategy=strategy_name,
    trade_count=1,
    wins=1 if is_win else 0,
    losses=0 if is_win else 1,
    gross_profit_pct=profit if is_win else Decimal("0"),
    gross_loss_pct=Decimal("0") if is_win else abs(loss),
    total_pnl_pct=pnl,
    win_rate=Decimal("1.0") if is_win else Decimal("0.0"),
    profit_factor=Decimal("0"),  # recomputed in DO UPDATE
    updated_at=datetime.now(timezone.utc),
).on_conflict_do_update(
    index_elements=["strategy"],
    set_={
        "trade_count": StrategyStatsORM.trade_count + 1,
        "wins": StrategyStatsORM.wins + (1 if is_win else 0),
        "losses": StrategyStatsORM.losses + (0 if is_win else 1),
        "gross_profit_pct": StrategyStatsORM.gross_profit_pct + (profit if is_win else Decimal("0")),
        "gross_loss_pct": StrategyStatsORM.gross_loss_pct + (Decimal("0") if is_win else abs(loss)),
        "total_pnl_pct": StrategyStatsORM.total_pnl_pct + pnl,
        # win_rate and profit_factor computed from updated raw values
        "updated_at": datetime.now(timezone.utc),
    }
)
await session.execute(stmt)
```

The `profit_factor` requires division (`gross_profit / gross_loss`) which cannot be expressed cleanly in a single `ON CONFLICT DO UPDATE` without a CASE for zero-division. Two options: (a) compute in Python after the upsert with a separate UPDATE, or (b) leave `profit_factor` as a computed column populated by a separate query. Recommended: upsert the raw accumulators first, then UPDATE `win_rate` and `profit_factor` in the same transaction using a second statement with Python-computed values.

### Pattern 4: TradeORM Alembic Migration

New migration `0003_phase7_signal_mode.py` must:
1. Add `trailing_stop_price NUMERIC(12,5) NULLABLE` to `trades`.
2. Create `strategy_stats` table with PK `strategy VARCHAR(30)`, cumulative columns, `updated_at TIMESTAMP`.

```python
# alembic/versions/0003_phase7_signal_mode.py
def upgrade():
    op.add_column("trades",
        sa.Column("trailing_stop_price", sa.Numeric(12, 5), nullable=True)
    )
    op.create_table(
        "strategy_stats",
        sa.Column("strategy", sa.String(30), primary_key=True),
        sa.Column("trade_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer, nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer, nullable=False, server_default="0"),
        sa.Column("gross_profit_pct", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("gross_loss_pct", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("total_pnl_pct", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Numeric(6, 4), nullable=False, server_default="0"),
        sa.Column("profit_factor", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
```

### Pattern 5: monitor_trades — ATR for Trail Distance

The monitor job queries the last 15+ completed H1 candles from DB, then calls `RegimeDetector()._calculate_atr(h1_candles)` (period=14). This reuses the existing implementation without shared mutable state between jobs. [VERIFIED: src/backtesting/regime_detector.py line 81]

```python
async with AsyncSessionLocal() as session:
    stmt = (
        select(Candle)
        .where(Candle.instrument == "XAUUSD", Candle.timeframe == "H1", Candle.complete.is_(True))
        .order_by(Candle.timestamp.desc())
        .limit(20)
    )
    result = await session.execute(stmt)
    h1_rows = list(reversed(result.scalars().all()))
atr_h1 = Decimal(str(RegimeDetector()._calculate_atr(h1_candles=h1_rows, period=14)))
trail_distance = atr_h1  # 1.0 × ATR(H1)
```

### Pattern 6: FastAPI Jinja2Templates

```python
# Source: FastAPI docs via Context7
from fastapi.templating import Jinja2Templates
from fastapi import Request
from fastapi.responses import HTMLResponse

templates = Jinja2Templates(directory="src/templates")

@dashboard_router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html", context={})
```

The dashboard template itself fetches `/api/dashboard` via JS — no server-side data injection needed at render time. The `context={}` is acceptable. The template must include `request=request` in TemplateResponse for url_for to work (FastAPI >= 0.108 signature).

### Pattern 7: RiskDecision Threading (D-13)

Current `runner.py` line 97: `risk_passed: list[tuple[CandidateSignal, float]] = []`

Must become: `risk_passed: list[tuple[CandidateSignal, float, RiskDecision]] = []`

And line 103: `risk_passed.append((sig, score))` → `risk_passed.append((sig, score, decision))`

And `_persist()` signature: add `approved_ranked: list[tuple[CandidateSignal, float, RiskDecision]]`

All unpacking of `for sig, score in approved_ranked:` must become `for sig, score, decision in approved_ranked:`.

**Status_map loop is safe:** The status_map build loop at line 119 uses `for sig, _ in approved_ranked:` — must update to `for sig, _, __ in approved_ranked:` or use `for sig, *_ in approved_ranked:`.

### Anti-Patterns to Avoid

- **Opening `Bot` as async context manager per message:** Never `async with Bot(token=...) as bot: await bot.send_message(...)` inside a job or handler. This creates a new HTTP session per call and has ~100–200ms overhead. Use the singleton initialized at startup.
- **Decimal/float mixing in price comparisons:** All level comparisons (`sl_price`, `tp1_price`, `trailing_stop_price`, candle high/low) must use `Decimal`. Never compare `Decimal` to `float`. Candle ORM fields (`high`, `low`, `close`) are `Numeric` mapped to Python as `Decimal` by SQLAlchemy.
- **Separate TradeORM from ApprovedSignalORM flush:** D-14 requires both in the same transaction. Do not create TradeORM after the session closes — use `session.flush()` between candidate and approved inserts, then add TradeORM before the final `session.commit()` (implicit via `session.begin()`).
- **`strategy_stats` profit_factor division by zero:** If `gross_loss_pct == 0` and wins exist, profit_factor is theoretically infinite. Store as a capped sentinel (e.g., `Decimal("999.9999")`) or `None`. Make this decision explicit in the implementation.
- **`monitor_trades` running concurrent with `run_pipeline`:** Both are 15-min interval jobs. APScheduler's `max_instances=1` per job prevents self-overlap, but the two jobs CAN run concurrently with each other. This is fine — `monitor_trades` reads trades that `_persist()` has already committed. The only risk is if `monitor_trades` processes a trade in the same 15-min cycle it was created. This is safe: the trade starts as OPEN, monitor_trades checks its prices, and it won't have a completed M15 candle yet for that cycle.
- **Using `ParseMode.MARKDOWN_V2` for price strings:** Decimal prices contain `.` which must be escaped as `\.` in MarkdownV2. Use HTML parse mode instead.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Telegram message delivery | Custom HTTP client to Telegram API | `python-telegram-bot Bot.send_message` | Rate limiting, error handling, connection pooling already solved |
| PostgreSQL upsert | SELECT + UPDATE logic with race condition | `sqlalchemy.dialects.postgresql.insert().on_conflict_do_update()` | Single atomic statement, no TOCTOU race |
| Scheduled jobs | Custom asyncio.create_task loop with sleep | APScheduler `IntervalTrigger` / `CronTrigger` | Already in use; `max_instances=1` prevents pile-up |
| HTML escaping in Telegram | Manual string replacement | `html.escape()` or `ParseMode.HTML` | All special characters handled correctly |
| ATR calculation | New implementation | `RegimeDetector()._calculate_atr()` | Already tested, period-14 simple ATR matches AGENTS.md |

**Key insight:** Every new piece of infrastructure this phase needs (Telegram, upsert, scheduling, ATR) is either already in the project or solved by existing project dependencies. Phase 7 is integration, not novel infrastructure.

---

## Common Pitfalls

### Pitfall 1: Bot Instance Not Initialized Before Use

**What goes wrong:** `TelegramError: Bot is not initialized` or `RuntimeError: Bot is not initialized` when calling `bot.send_message()` without first calling `await bot.initialize()`.

**Why it happens:** python-telegram-bot v20+ requires explicit initialization of the HTTP session. Calling `bot.send_message()` on an uninitialized Bot raises at the network layer.

**How to avoid:** In `main.py` lifespan, call `await bot.initialize()` before the scheduler starts. Call `await bot.shutdown()` in the shutdown block after `scheduler.shutdown()`.

**Warning signs:** Tests pass (if they mock `bot.send_message`) but integration fails immediately on first Telegram send.

### Pitfall 2: Decimal Comparison With Float Candle Fields

**What goes wrong:** `TypeError: '>' not supported between instances of 'Decimal' and 'float'` in monitor_trades level comparison.

**Why it happens:** SQLAlchemy maps `Numeric` columns to Python `Decimal`, but ORM attribute access sometimes returns `float` depending on driver. asyncpg returns `Decimal` for `Numeric` columns — confirmed safe. [VERIFIED: src/models/trade.py — all price columns use Numeric(12,5)]

**How to avoid:** Cast candle high/low to `Decimal` explicitly: `Decimal(str(candle.high))`. Never use raw float arithmetic on price levels.

**Warning signs:** Works with asyncpg in production but fails with aiosqlite in tests.

### Pitfall 3: D-13 Tuple Unpacking Breaks Status Map

**What goes wrong:** `ValueError: too many values to unpack` or `AttributeError` when the status_map loop encounters 3-tuples.

**Why it happens:** After D-13, `approved_ranked` contains `(CandidateSignal, float, RiskDecision)` but the status_map build still uses `for sig, _ in approved_ranked:`.

**How to avoid:** Update ALL loops over `approved_ranked` simultaneously: the status_map loop (line 119), the `_persist()` signature, and any logging. Use `for sig, score, decision in approved_ranked:` everywhere.

**Warning signs:** Pipeline tests pass (they mock `approved_ranked` as 2-tuples) but integration test with real risk gate fails.

### Pitfall 4: monitor_trades Job Has No TradeORM Data to Process

**What goes wrong:** Monitor job runs silently without errors but no trades are ever updated.

**Why it happens:** `TradeORM` creation in `_persist()` (D-14) happens only if D-13 is implemented first. If the `risk_passed` tuple change is incomplete, `_persist()` receives `None` for `decision` and skips TradeORM creation.

**How to avoid:** Test `_persist()` directly with a mock `RiskDecision` that has `sizing.size_lots = Decimal("0.1")`. Verify the TradeORM row is created in the same transaction as the ApprovedSignalORM.

### Pitfall 5: strategy_stats Join Requires Eager Load or Explicit Join

**What goes wrong:** `MissingGreenlet` or lazy-load error when accessing `trade.approved_signal.candidate_signal.strategy` inside an async session.

**Why it happens:** SQLAlchemy async sessions do not support lazy loading. Accessing related objects without `joinedload` or `selectinload` raises inside an async context. [VERIFIED: SQLAlchemy 2.0 async docs — lazy loading not supported]

**How to avoid:** Use an explicit JOIN query to retrieve the strategy name in the same SELECT as the trade:

```python
stmt = (
    select(TradeORM, CandidateSignalORM.strategy)
    .join(ApprovedSignalORM, TradeORM.approved_signal_id == ApprovedSignalORM.id)
    .join(CandidateSignalORM, ApprovedSignalORM.candidate_signal_id == CandidateSignalORM.id)
    .where(TradeORM.status.in_(["OPEN", "TP1_HIT"]))
)
```

### Pitfall 6: daily_summary at 00:00 UTC Conflicts With Other Jobs

**What goes wrong:** Daily summary fires at 00:00 UTC exactly when D1 refresh fires at 00:05 UTC and the pipeline fires every 15 minutes. No actual conflict — they are independent — but the daily summary queries `DATE(created_at) = today` which at 00:00:00 UTC queries the just-completed day correctly.

**How to avoid:** Use server-side UTC date truncation consistent with `evaluate_daily_loss` pattern: `func.date_trunc("day", func.timezone("UTC", func.now()))`. Do not use Python `datetime.today()` for date boundaries — clock skew.

### Pitfall 7: Jinja2Templates Requires `jinja2` Package

**What goes wrong:** `ImportError: Jinja2 must be installed to use Jinja2Templates` at startup.

**Why it happens:** FastAPI does not bundle Jinja2 — it is a separate package. `python-multipart` is similarly separate from FastAPI.

**How to avoid:** Add `jinja2>=3.1.0` to `pyproject.toml` dependencies. [ASSUMED — not currently listed in pyproject.toml. Must verify before plan is written.]

### Pitfall 8: BreakerManager Redis Client Needs `decode_responses=True`

**What goes wrong:** `TypeError: datetime.fromisoformat() argument must be str, not bytes` in `BreakerManager.is_tripped()`.

**Why it happens:** `BreakerManager.__init__` docstring: "decode_responses=True is REQUIRED". The monitor job instantiates a fresh `BreakerManager` — it must pass `decode_responses=True` to the Redis client or inject the existing Redis instance. [VERIFIED: src/risk/breaker.py line 34 — default constructor handles this, but injected clients must also have it]

**How to avoid:** Follow the pattern from `health.py` — instantiate `aioredis.from_url(settings.redis_url, decode_responses=True)` and inject. Or let BreakerManager create its own client (it defaults correctly).

---

## Code Examples

### Signal Message Format (AGENTS.md §13.1)

```
🟢 BUY XAUUSD
Entry: 2340.50
SL: 2325.20 (-0.65%)
TP1: 2358.80 (+0.78%)
TP2: 2377.10 (+1.56%)
Strategy: Liquidity Sweep
Confidence: 0.82
Size suggestion: 0.15 lots (1% risk)
```

The SL percentage is `(sl_price - entry_price) / entry_price × 100` (negative for BUY). TP percentages are `(tp_price - entry_price) / entry_price × 100`. All computed in Decimal, formatted to 2 decimal places.

### Daily Summary Format (AGENTS.md §14.3)

```
📊 Daily Summary — 2024-01-15
━━━━━━━━━━━━━━━
Signals: 4 sent | 2 approved
Trades: 2 opened | 1 TP1 | 0 stopped
P&L: +1.2% (daily) | +4.8% (MTD)
Circuit Breaker: OFF (0/8 consecutive stops)
Mode: SIGNAL
━━━━━━━━━━━━━━━
```

### Blended P&L Formula (D-06)

```python
# direction_sign = +1 for BUY, -1 for SELL
tp1_pnl = (tp1_price - entry_price) / entry_price * direction_sign
final_pnl = (exit_price - entry_price) / entry_price * direction_sign
blended_pnl_pct = Decimal("0.5") * tp1_pnl + Decimal("0.5") * final_pnl
```

Where `exit_price` is `trailing_stop_price` for TRAIL closes, `tp2_price` for TP2 closes, `sl_price` for SL closes.

### Trailing Stop Ratchet (D-03)

```python
new_trail = entry_price - trail_distance  # for BUY: trail below entry
if trade.direction == "BUY":
    if trade.trailing_stop_price is None or new_trail > trade.trailing_stop_price:
        trade.trailing_stop_price = new_trail
elif trade.direction == "SELL":
    new_trail = entry_price + trail_distance  # SELL: trail above entry
    if trade.trailing_stop_price is None or new_trail < trade.trailing_stop_price:
        trade.trailing_stop_price = new_trail
```

Note: Trail distance anchors off `entry_price` relative to ATR, NOT off the current price. AGENTS.md §13.2 specifies `1.0 × ATR(H1)` as the distance, but does not specify the anchor point explicitly. [ASSUMED: Trail computed as `current_price ± 1.0 × ATR` at the time of the TP1_HIT candle, then ratcheted. Confirm with AGENTS.md interpretation before finalizing.] [A1]

### Candle Level Touch Detection

```python
# latest completed M15 candle
candle_high = Decimal(str(latest_m15.high))
candle_low = Decimal(str(latest_m15.low))

# Pre-TP1_HIT: check SL and TP1
sl_touched = candle_low <= trade.sl_price <= candle_high or candle_low <= trade.sl_price
tp1_touched = candle_low <= trade.tp1_price <= candle_high or trade.tp1_price <= candle_high

# Conservative: level touched if candle range crosses the price
sl_touched = candle_low <= trade.sl_price  # SL is always below entry for BUY
tp1_touched = candle_high >= trade.tp1_price  # TP1 is above entry for BUY
```

The correct test for a BUY: SL touched if `candle_low <= sl_price`; TP1 touched if `candle_high >= tp1_price`. For SELL: SL touched if `candle_high >= sl_price`; TP1 touched if `candle_low <= tp1_price`. This is simpler than a range membership check and handles all intracandle scenarios.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `python-telegram-bot` v13 `Updater` | v20+ `Bot` standalone without Updater | v20 (2022) | No polling loop needed for one-way sends; use `Bot` directly |
| SQLAlchemy `Column()` | `mapped_column()` with `Mapped[]` type annotations | SA 2.0 | Project already uses new style; must continue |
| `from_orm()` Pydantic | `model_validate()` | Pydantic v2 | Project already uses new style |

**Confirmed active in project:**
- `asyncio_mode = "auto"` in pytest — all test functions can be `async def` without `@pytest.mark.asyncio` decorator.
- `FakeAsyncRedis` with `decode_responses=True` for breaker tests.
- `AsyncMock` for async method mocking throughout test suite.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Trailing stop trail distance anchors off entry_price (not current price), computed at TP1_HIT event | Code Examples — Trailing Stop Ratchet | If anchored to current price, monitor job needs the current candle close to compute trail each cycle — adds complexity |
| A2 | `jinja2` package is not yet in `pyproject.toml` | Common Pitfalls #7 | If already present (e.g., installed transitively), no action needed; if absent, plan must add it |
| A3 | `profit_factor` when `gross_loss_pct == 0` should be stored as a sentinel (e.g., 999.9999) | Architecture Patterns — Pattern 3 | If stored as NULL, dashboard rendering needs a null check; plan must decide |

---

## Open Questions

1. **Trailing stop anchor point**
   - What we know: AGENTS.md §13.2 says "trail distance = 1.0 × ATR(H1), updated per H1 candle, ratchet only"
   - What's unclear: whether trail = `current_price ± ATR` (floating) or `entry_price ± ATR` (fixed anchor) or `TP1_price ± ATR` (post-TP1 anchor)
   - Recommendation: Assume `current_candle_high/low ± ATR` is the computed trail level, and the ratchet only accepts improvements. The most natural interpretation for "trailing stop" is that it follows the favorable price move.

2. **profit_factor sentinel for zero-loss strategies**
   - What we know: D-16 says store `gross_profit_pct` and `gross_loss_pct` raw so `profit_factor` can always be recomputed correctly
   - What's unclear: what to store in `profit_factor` column when `gross_loss_pct = 0`
   - Recommendation: Store `Decimal("0")` until there is at least one loss; recompute after every update.

3. **`consecutive_stops` in circuit_breaker dashboard payload**
   - What we know: UI-SPEC requires `"circuit_breaker": {"tripped": false, "consecutive_stops": 2}` in `/api/dashboard` JSON
   - What's unclear: `BreakerManager` only exposes `is_tripped()` — the counter is stored in Redis as `CB_COUNTER` key but there is no public `get_count()` method
   - Recommendation: Phase 7 adds `BreakerManager.get_consecutive_stops() -> int` method that reads `CB_COUNTER` from Redis, or `/api/dashboard` reads the Redis key directly.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PostgreSQL | All ORM + migrations | Project-level (Docker Compose) | Not checked in shell | None — required |
| Redis | BreakerManager, health | Project-level (Docker Compose) | Not checked in shell | None — required |
| python-telegram-bot | Telegram sends | Listed in pyproject.toml | >=21.0 declared | None — required |
| jinja2 | Dashboard template | Not in pyproject.toml [ASSUMED] | Unknown | None — must add |
| APScheduler | monitor_trades, daily_summary | Listed in pyproject.toml | >=3.10.0 declared | None — required |
| alembic | Migration 0003 | Listed in pyproject.toml | >=1.13.0 declared | None — required |

**Missing dependencies with no fallback:**
- `jinja2` — not listed in `pyproject.toml` [ASSUMED]. Must verify and add `jinja2>=3.1.0` to dependencies if absent.

**Missing dependencies with fallback:**
- None (all others confirmed present)

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio 0.23+ |
| Config file | `[tool.pytest.ini_options]` in `pyproject.toml`; `asyncio_mode = "auto"` |
| Quick run command | `pytest tests/test_execution/ tests/test_monitoring/ -x` |
| Full suite command | `pytest` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SIG-01 | `SignalSender.send_signal()` formats message and calls `Bot.send_message` | unit | `pytest tests/test_execution/test_signal_sender.py -x` | Wave 0 |
| SIG-02 | `monitor_trades` detects TP1_HIT, TRAIL, TP2, SL on M15 candle range | unit | `pytest tests/test_monitoring/test_monitor_trades.py -x` | Wave 0 |
| SIG-02 | Trailing stop ratchet: only update when strictly better | unit | `pytest tests/test_monitoring/test_monitor_trades.py::test_trail_ratchet -x` | Wave 0 |
| SIG-02 | D-05 conservative resolution: SL wins pre-TP1_HIT | unit | `pytest tests/test_monitoring/test_monitor_trades.py::test_sl_wins_pre_tp1 -x` | Wave 0 |
| SIG-03 | `strategy_stats` upsert increments wins/losses/pnl atomically | unit | `pytest tests/test_monitoring/test_strategy_stats.py -x` | Wave 0 |
| NOTIF-01 | `execution_status` updated to SENT after successful send | unit | `pytest tests/test_execution/test_executor.py::test_status_sent_on_success -x` | Wave 0 |
| NOTIF-01 | `execution_status` stays PENDING and logs on failure | unit | `pytest tests/test_execution/test_executor.py::test_status_pending_on_failure -x` | Wave 0 |
| NOTIF-02 | TP1/TP2/SL/TRAIL lifecycle notifications sent | unit | `pytest tests/test_monitoring/test_telegram_bot.py -x` | Wave 0 |
| NOTIF-03 | CB alert hook fires `send_circuit_breaker_alert` | unit | `pytest tests/test_monitoring/test_telegram_bot.py::test_cb_alert -x` | Wave 0 |
| NOTIF-04 | Daily summary queries correct fields and sends | unit | `pytest tests/test_monitoring/test_telegram_bot.py::test_daily_summary -x` | Wave 0 |

### Sampling Rate

- Per task commit: `pytest tests/test_execution/ tests/test_monitoring/ -x --tb=short`
- Per wave merge: `pytest`
- Phase gate: Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/test_execution/__init__.py` — new test package
- [ ] `tests/test_execution/test_signal_sender.py` — SIG-01, NOTIF-01
- [ ] `tests/test_execution/test_executor.py` — NOTIF-01, D-13/D-14/D-15
- [ ] `tests/test_monitoring/test_monitor_trades.py` — SIG-02, NOTIF-02
- [ ] `tests/test_monitoring/test_strategy_stats.py` — SIG-03
- [ ] `tests/test_monitoring/test_telegram_bot.py` — NOTIF-02, NOTIF-03, NOTIF-04
- [ ] `tests/test_monitoring/test_dashboard.py` — /dashboard, /api/dashboard endpoints

Note: `tests/test_monitoring/__init__.py` and `tests/test_monitoring/test_health_risk.py` already exist — add to existing package.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Dashboard has no auth (D-25) — operator tool, local/firewall only |
| V3 Session Management | No | No user sessions |
| V4 Access Control | No | No user-facing access control in Phase 7 |
| V5 Input Validation | Yes (low risk) | No user input on dashboard; `/api/dashboard` is read-only; Telegram messages are bot-generated only |
| V6 Cryptography | No | Telegram bot token read from env; no crypto operations |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Telegram token in logs | Information Disclosure | `settings.telegram_bot_token` must never appear in structlog output; already gated by `main.py` pattern of masking credentials |
| Dashboard SSRF via `/api/dashboard` | Tampering | Endpoint is read-only; no URL parameters; no external requests triggered by dashboard fetch |
| Bot token exposure via error messages | Information Disclosure | `execution.send_failed` log event must log error string but NOT the token; `telegram.error.TelegramError` messages from PTB do not expose the token |

---

## Sources

### Primary (HIGH confidence)

- `src/pipeline/runner.py` — Verified exact current signature of `_persist()`, `risk_passed` type, and all integration points for D-13/D-14
- `src/risk/events.py` — Verified `RiskDecision` fields: `passed`, `reason`, `sizing`, `concentration_reduced`; `PositionSizing.size_lots: Decimal`
- `src/models/trade.py` — Verified `TradeORM` columns: `trailing_stop_price` absent (needs migration), `status`, `close_reason`, `pnl_pct` present
- `src/models/signal.py` — Verified `CandidateSignalORM.strategy` and `ApprovedSignalORM.execution_status` fields
- `src/risk/breaker.py` — Verified `BreakerManager` public API: `record_stop`, `record_win`, `is_tripped`, `reset_if_expired`
- `src/risk/hooks.py` — Verified `register_alert_hook`, `_publish_alert`, `BreakerAlertHook` type alias
- `src/config.py` — Verified `telegram_bot_token`, `telegram_chat_id`, `execution_mode` (ExecutionMode enum) present
- `src/scheduler/jobs.py` — Verified current job list; `monitor_trades` and `daily_summary` absent
- `src/main.py` — Verified lifespan structure; Bot instantiation absent; hook registration absent
- `pyproject.toml` — Verified `python-telegram-bot>=21.0`, `apscheduler>=3.10.0`, `alembic>=1.13.0` present; `jinja2` not listed
- `AGENTS.md §13.1` — Signal message format (BUY/SELL template)
- `AGENTS.md §14.3` — Daily summary format
- `07-CONTEXT.md` — All 26 decisions verified against codebase
- `07-UI-SPEC.md` — Dashboard data contract: `/api/dashboard` JSON shape, component spec

### Secondary (MEDIUM confidence)

- Context7 `/python-telegram-bot/python-telegram-bot` — Bot.send_message with ParseMode.HTML, Bot initialization pattern without Application
- Context7 `/websites/sqlalchemy_en_20` — `sqlalchemy.dialects.postgresql.insert().on_conflict_do_update()` API
- Context7 `/fastapi/fastapi` — `Jinja2Templates`, `TemplateResponse(request=request, name=..., context={})`

### Tertiary (LOW confidence)

- None

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages verified in pyproject.toml; one gap (jinja2) flagged
- Architecture: HIGH — all integration points verified against actual source files
- Pitfalls: HIGH — derived from direct code inspection plus Context7-verified library behavior
- Message formats: HIGH — verified against AGENTS.md §13.1 and §14.3

**Research date:** 2026-04-28
**Valid until:** 2026-05-28 (stable stack — no fast-moving dependencies introduced)
