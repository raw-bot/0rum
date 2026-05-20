# Phase 7: Signal Mode & Monitoring — Pattern Map

**Mapped:** 2026-04-28
**Files analyzed:** 14 new/modified files
**Analogs found:** 13 / 14

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/execution/__init__.py` | config | — | `src/pipeline/__init__.py` | role-match |
| `src/execution/signal_sender.py` | service | request-response | `src/risk/runner.py` | role-match |
| `src/execution/executor.py` | service | request-response | `src/risk/runner.py` | role-match |
| `src/monitoring/notification_adapter.py` | service | event-driven | `src/risk/hooks.py` | role-match |
| `src/models/strategy_stats.py` | model | CRUD | `src/models/optimizer_result.py` | exact |
| `src/models/trade.py` | model | CRUD | `src/models/trade.py` (modify) | exact |
| `src/scheduler/jobs.py` | service | event-driven | `src/scheduler/jobs.py` (modify) | exact |
| `src/pipeline/runner.py` | service | request-response | `src/pipeline/runner.py` (modify) | exact |
| `src/monitoring/health.py` | controller | request-response | `src/monitoring/health.py` (modify) | exact |
| `src/main.py` | config | — | `src/main.py` (modify) | exact |
| `src/templates/dashboard.html` | component | request-response | none | no analog |
| `alembic/versions/0003_phase7_signal_mode.py` | migration | CRUD | `alembic/versions/0002_expand_optimizer_max_drawdown_precision.py` | role-match |
| `tests/test_execution/` (3 files) | test | — | `tests/test_pipeline/test_runner.py` | exact |
| `tests/test_monitoring/` (4 files) | test | — | `tests/test_monitoring/test_health_risk.py` | exact |

---

## Pattern Assignments

### `src/execution/__init__.py` (config)

**Analog:** `src/pipeline/__init__.py` (empty init file — Python package marker)

**Pattern:** Empty file — just `"""src/execution package."""` docstring. No imports. This matches every other `__init__.py` in the project (`src/pipeline/__init__.py`, `src/risk/__init__.py`, `src/monitoring/__init__.py`).

---

### `src/execution/signal_sender.py` (service, request-response)

**Analog:** `src/risk/runner.py`

**Imports pattern** (`src/risk/runner.py` lines 1–24):
```python
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.signal_data import CandidateSignal, MarketRegime
from src.risk.breaker import BreakerManager
from src.risk.events import RiskDecision
```

For `signal_sender.py`, adapt to:
```python
from decimal import Decimal
import structlog
from external notification channel import Bot
from external notification channel.constants import ParseMode

from src.config import get_settings
from src.models.signal_data import CandidateSignal

log = structlog.get_logger(__name__)
```

**Core class pattern** (`src/risk/runner.py` lines 27–30 — constructor injection):
```python
class RiskGateRunner:
    def __init__(self, breaker: BreakerManager | None = None):
        self.breaker = breaker or BreakerManager()
        self.settings = get_settings()
```

For `SignalSender`, inject `Bot` at construction (D-11):
```python
class SignalSender:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._settings = get_settings()
```

**Error handling pattern** (`src/scheduler/jobs.py` lines 63–64):
```python
except Exception as exc:
    log.error("jobs.refresh_failed", timeframe=timeframe, error=str(exc))
```

For `signal_sender.py`, on External notification channel send failure (D-15):
```python
except Exception as exc:
    log.error(
        "execution.send_failed",
        strategy=signal.strategy.value,
        direction=signal.direction.value,
        error=str(exc),
    )
    # Do NOT raise — caller keeps execution_status=PENDING
```

**Use `ParseMode.HTML` (not MARKDOWN_V2):** Research confirms HTML avoids escaping `.` in prices. See RESEARCH.md Pattern 2.

---

### `src/execution/executor.py` (service, request-response)

**Analog:** `src/risk/runner.py`

**Imports pattern** (`src/risk/runner.py` lines 1–24 — adapted):
```python
import structlog
from src.config import ExecutionMode, get_settings
from src.execution.signal_sender import SignalSender
from src.models.signal_data import CandidateSignal
from src.risk.events import RiskDecision

log = structlog.get_logger(__name__)
```

**Routing branch pattern** — modeled on `src/risk/runner.py` lines 40–52 (early-return dispatch):
```python
class ExecutionRouter:
    def __init__(self, signal_sender: SignalSender) -> None:
        self._sender = signal_sender
        self._settings = get_settings()

    async def execute(
        self, signal: CandidateSignal, size_lots: Decimal
    ) -> bool:
        if self._settings.execution_mode == ExecutionMode.SIGNAL:
            return await self._execute_signal_mode(signal, size_lots)
        elif self._settings.execution_mode == ExecutionMode.AUTO:
            raise NotImplementedError("Auto mode not implemented — Phase 8")
        else:
            raise ValueError(f"Unknown execution mode: {self._settings.execution_mode}")
```

**Structlog event keys** — follow `src/risk/runner.py` lines 43–50 style:
```python
log.info(
    "execution.signal_sent",
    strategy=signal.strategy.value,
    direction=signal.direction.value,
    size_lots=str(size_lots),
)
```

---

### `src/monitoring/notification_adapter.py` (service, event-driven)

**Analog:** `src/risk/hooks.py`

`hooks.py` establishes the pattern: callable surface registered at startup, receives a DTO, fires async with error isolation. `notification_adapter.py` is the hook implementor that receives `CircuitBreakerAlert`.

**Imports pattern** (`src/risk/hooks.py` lines 1–26):
```python
from typing import Awaitable, Callable
import structlog
from src.risk.events import CircuitBreakerAlert

log = structlog.get_logger(__name__)
```

For `notification_adapter.py`, adapt to:
```python
import structlog
from external notification channel import Bot
from external notification channel.constants import ParseMode

from src.config import get_settings
from src.risk.events import CircuitBreakerAlert

log = structlog.get_logger(__name__)
```

**Class + constructor injection** (mirror `SignalSender` pattern above):
```python
class NotificationAdapter:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._settings = get_settings()
```

**Hook-compatible signature** (must satisfy `BreakerAlertHook = Callable[[CircuitBreakerAlert], Awaitable[None]]` from `src/risk/hooks.py` line 28):
```python
async def send_circuit_breaker_alert(self, alert: CircuitBreakerAlert) -> None:
    ...
```

**Error isolation pattern** (`src/risk/hooks.py` lines 43–54):
```python
for hook in _alert_hooks:
    try:
        await hook(alert)
    except Exception as exc:
        log.error("risk.alert_hook.failed", error=str(exc))
```

Apply same try/except to every `bot.send_message` call in `notification_adapter.py`:
```python
try:
    await self._bot.send_message(
        chat_id=self._settings.external_notification_chat_id,
        text=message,
        parse_mode=ParseMode.HTML,
    )
except Exception as exc:
    log.error("monitor.notification_send_failed", event=event_name, error=str(exc))
```

**Structlog event keys for lifecycle notifications** (follow `src/risk/breaker.py` lines 55/88):
- `monitor.tp1_hit` — TP1 hit notification
- `monitor.trade_closed` — final close notification (includes `close_reason`)
- `monitor.circuit_breaker_alert` — CB trip notification
- `monitor.daily_summary_sent`

---

### `src/models/strategy_stats.py` (model, CRUD)

**Analog:** `src/models/optimizer_result.py`

`optimizer_result.py` is a single-row-per-strategy cumulative ORM model — the closest structural match.

**Full ORM pattern** (`src/models/optimizer_result.py` lines 1–46):
```python
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class OptimizerResultORM(Base):
    __tablename__ = "optimizer_results"
    __table_args__ = (
        Index("idx_optimizer_strategy", "strategy", "created_at"),
        Index("idx_optimizer_active", "is_active", postgresql_where=text("is_active = TRUE")),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    strategy: Mapped[str] = mapped_column(String(30), nullable=False)
    ...
    win_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4), nullable=True)
    profit_factor: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), nullable=True)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ...
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
```

For `StrategyStatsORM`, the PK is `strategy` (varchar), not a UUID — no `id` column needed:
```python
class StrategyStatsORM(Base):
    __tablename__ = "strategy_stats"

    strategy: Mapped[str] = mapped_column(String(30), primary_key=True)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    wins: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    losses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    gross_profit_pct: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False, server_default="0")
    gross_loss_pct: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False, server_default="0")
    total_pnl_pct: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False, server_default="0")
    win_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, server_default="0")
    profit_factor: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

**Column precision to reuse from optimizer_result.py:**
- `win_rate`: `Numeric(6, 4)` — line 38
- `profit_factor`: `Numeric(8, 4)` — line 36
- `trade_count`: `Integer` — line 40

---

### `src/models/trade.py` (model, CRUD — modify)

**Source:** `src/models/trade.py` (current state, lines 1–45)

**Single addition needed:** Add `trailing_stop_price` column after `tp2_price` (D-03):
```python
trailing_stop_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 5), nullable=True)
```

Same `Numeric(12, 5)` precision as `tp2_price` (line 37). Column goes between `tp2_price` and `size_lots` in the class body for readability. The `Optional[Decimal]` + `nullable=True` pattern matches `tp2_price` and `pnl` columns exactly.

---

### `src/scheduler/jobs.py` (service, event-driven — modify)

**Analog:** `src/scheduler/jobs.py` itself (current state, lines 1–220)

**New job function pattern** — copy `run_optimizer` (lines 87–100):
```python
async def run_optimizer() -> None:
    """Run walk-forward optimizer for all 4 strategies — called every 24h."""
    from src.backtesting.optimizer import WalkForwardOptimizer
    try:
        optimizer = WalkForwardOptimizer()
        await optimizer.run()
        log.info("jobs.optimizer.complete")
    except Exception as exc:
        log.error("jobs.optimizer.failed", error=str(exc))
```

For `monitor_trades`:
- Same deferred import pattern (inside `try` block)
- Same `log.error(f"jobs.X.failed", error=str(exc))` catch-all
- Same `async with AsyncSessionLocal() as session:` pattern (lines 127–139 in `run_pipeline`)
- Event key: `"jobs.monitor_trades.complete"` / `"jobs.monitor_trades.failed"`

For `daily_summary`:
- Same structure as `run_optimizer` — calls service object, logs completion
- Event key: `"jobs.daily_summary.complete"` / `"jobs.daily_summary.failed"`

**New job registration pattern** — copy from `create_scheduler()` (lines 152–219):

For `monitor_trades` (interval):
```python
scheduler.add_job(
    monitor_trades,
    trigger=IntervalTrigger(minutes=15),
    id="monitor_trades",
    name="Monitor open theoretical trades every 15 minutes",
    max_instances=1,
    replace_existing=True,
)
```

For `daily_summary` (cron at 00:00 UTC — matches `refresh_d1` cron pattern at lines 193–200):
```python
scheduler.add_job(
    daily_summary,
    trigger=CronTrigger(hour=0, minute=0, timezone="UTC"),
    id="daily_summary",
    name="Send daily External notification channel summary at 00:00 UTC",
    max_instances=1,
    replace_existing=True,
)
```

---

### `src/pipeline/runner.py` (service, request-response — modify)

**Source:** `src/pipeline/runner.py` (current state, lines 1–211)

**Three specific changes only:**

**Change 1 — D-13: risk_passed type annotation** (line 97):
```python
# Before:
risk_passed: list[tuple[CandidateSignal, float]] = []
# After:
risk_passed: list[tuple[CandidateSignal, float, RiskDecision]] = []
```

**Change 2 — D-13: tuple construction** (line 103):
```python
# Before:
risk_passed.append((sig, score))
# After:
risk_passed.append((sig, score, decision))
```

**Change 3 — D-13: ALL unpacking sites** (lines 100, 119, 198 and `_persist` signature):
```python
# Line 100 — existing loop stays 2-tuple before append:
for sig, score in approved_ranked:  # BEFORE risk step

# Line 119 — status_map build:
# Before:
for sig, _ in approved_ranked:
# After:
for sig, *_ in approved_ranked:

# _persist() line 138 — signature change:
# Before:
approved_ranked: list[tuple[CandidateSignal, float]],
# After:
approved_ranked: list[tuple[CandidateSignal, float, RiskDecision]],

# _persist() line 198 — loop over approved_ranked:
# Before:
for sig, score in approved_ranked:
# After:
for sig, score, decision in approved_ranked:
```

**Change 4 — D-14: TradeORM creation inside `_persist()`** — add after `await session.flush()` at line 209 (after approved ORM created), within the same `session.begin()` block:
```python
# After session.add(approved_orm):
from src.models.trade import TradeORM
trade_orm = TradeORM(
    approved_signal_id=approved_orm.id,
    direction=sig.direction.value,
    entry_price=cand_orm.entry_price,
    sl_price=cand_orm.sl_price,
    tp1_price=cand_orm.tp1_price,
    tp2_price=cand_orm.tp2_price,
    size_lots=decision.sizing.size_lots,
    status="OPEN",
)
session.add(trade_orm)
```

**Change 5 — D-15: execution_status update** — add call to `ExecutionRouter.execute()` after `session.flush()`, update `approved_orm.execution_status` to `"SENT"` on success within the same transaction (or keep `"PENDING"` and log on failure).

---

### `src/monitoring/health.py` (controller, request-response — modify)

**Analog:** `src/monitoring/health.py` itself (current state, lines 1–92)

**Single change — D-20:** Replace hardcoded `"strategies_active": 4` (line 89) with a live DB query.

**Query pattern** — copy `session.execute(text("SELECT 1"))` pattern (lines 38–40) and adapt using `AsyncSessionLocal` (same as `run_pipeline` in jobs.py lines 127–140):
```python
from sqlalchemy import func, select
from src.models.optimizer_result import OptimizerResultORM

# Inside health_check, after postgres_connected check:
try:
    stmt = select(func.count()).where(
        OptimizerResultORM.is_active.is_(True)
    )
    result = await db.execute(stmt)
    strategies_active_val = result.scalar_one()
except Exception as exc:
    logger.warning("health.strategies_active.failed", error=str(exc))
    strategies_active_val = 0
```

Then in the return dict: `"strategies_active": strategies_active_val,`

**New `/api/dashboard` endpoint** — add to `health.py` or to a new `dashboard.py` in `src/monitoring/`. Pattern is identical to `health_check` (lines 24–92): `APIRouter`, `Depends(get_db)`, `AsyncSessionLocal` for extra queries, `structlog`, return dict. The endpoint performs no mutations — read-only aggregation matching the existing `/health` pattern.

---

### `src/main.py` (config — modify)

**Analog:** `src/main.py` itself (current state, lines 1–87)

**Three additions to `lifespan()`:**

**Addition 1 — D-11: Bot instantiation and initialization** (after `configure_structlog()`, before scheduler start):
```python
from external notification channel import Bot
bot = Bot(token=settings.external_notification_token)
await bot.initialize()  # opens HTTP session — must precede scheduler start
```

**Addition 2 — D-11/D-12: Service instantiation and hook registration** (after bot init):
```python
from src.execution.signal_sender import SignalSender
from src.monitoring.notification_adapter import NotificationAdapter
from src.risk.hooks import register_alert_hook

signal_sender = SignalSender(bot=bot)
notification_adapter = NotificationAdapter(bot=bot)
register_alert_hook(notification_adapter.send_circuit_breaker_alert)
```

**Addition 3 — Bot shutdown in lifespan teardown** (after `scheduler.shutdown()`):
```python
await bot.shutdown()
```

**Credential masking invariant** (line 48 existing pattern — do not log the token):
```python
# Good — existing pattern:
database_url=settings.database_url.split("@")[-1],
# Follow same for bot: never log settings.external_notification_token
```

**Addition 4 — Jinja2 templates mount** (after `app = FastAPI(...)`):
```python
from fastapi.templating import Jinja2Templates
templates = Jinja2Templates(directory="src/templates")
# Pass templates instance to dashboard router via dependency or module-level
```

**New router registration** (after `app.include_router(health_router)` line 86):
```python
from src.monitoring.health import dashboard_router  # or separate dashboard module
app.include_router(dashboard_router)
```

---

### `alembic/versions/0003_phase7_signal_mode.py` (migration, CRUD)

**Analog:** `alembic/versions/0002_expand_optimizer_max_drawdown_precision.py`

**Header pattern** (lines 1–14 of 0002):
```python
"""Expand optimizer_results.max_drawdown precision.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None
```

For 0003:
```python
revision = "0003"
down_revision = "0002"
```

**Column addition pattern** (0002 lines 17–23 — `op.alter_column`):
For `trailing_stop_price`, use `op.add_column` instead:
```python
def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column("trailing_stop_price", sa.Numeric(12, 5), nullable=True),
    )
```

**Table creation pattern** — use `op.create_table` from `0001_initial_schema.py` lines 39–55 as template:
```python
    op.create_table(
        "strategy_stats",
        sa.Column("strategy", sa.String(30), primary_key=True),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gross_profit_pct", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("gross_loss_pct", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("total_pnl_pct", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Numeric(6, 4), nullable=False, server_default="0"),
        sa.Column("profit_factor", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("strategy"),
    )
```

**Downgrade pattern** (0002 lines 27–34 — always pair add with reverse):
```python
def downgrade() -> None:
    op.drop_table("strategy_stats")
    op.drop_column("trades", "trailing_stop_price")
```

---

### `src/templates/dashboard.html` (component, request-response)

**No analog in codebase.** Project has no existing templates directory or HTML files. Use RESEARCH.md Pattern 6 (FastAPI Jinja2Templates). Key constraints from decisions: dark theme, no external CDN, self-contained, `setInterval` polling `/api/dashboard` every 30 seconds (D-22, D-26).

---

## Test File Patterns

### `tests/test_execution/` (3 files)

**Analog:** `tests/test_pipeline/test_runner.py`

**Package setup** (`tests/test_pipeline/__init__.py` — empty):
```python
# tests/test_execution/__init__.py — empty file
```

**Imports and env setup pattern** (`tests/test_backtesting/test_scheduler_wiring.py` lines 1–9):
```python
import os
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("EXTERNAL_NOTIFICATION_TOKEN", "test-token")
os.environ.setdefault("EXTERNAL_NOTIFICATION_CHAT_ID", "test-chat")
```
Note: `tests/conftest.py` already sets these via `setdefault` — test files that import `src.*` at module level need the guard only if they might be collected before `conftest.py` runs.

**AsyncMock pattern for Bot.send_message** — follow `tests/test_pipeline/test_runner.py` lines 67–98 (patching via `patch` context manager):
```python
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

@pytest.mark.asyncio
async def test_signal_sender_calls_bot():
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    sender = SignalSender(bot=mock_bot)
    await sender.send_signal(signal=make_signal(), size_lots=Decimal("0.10"))
    mock_bot.send_message.assert_called_once()
```

**Session mock pattern** (`tests/test_pipeline/test_runner.py` lines 90–98):
```python
mock_session = MagicMock()
mock_session.__aenter__ = AsyncMock(return_value=mock_session)
mock_session.__aexit__ = AsyncMock(return_value=None)
mock_session.begin = MagicMock()
mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=None)
mock_session.add = MagicMock()
mock_session.flush = AsyncMock()
mock_session_cls.return_value = mock_session
```

### `tests/test_monitoring/` (4 new files + existing package)

**Analog:** `tests/test_monitoring/test_health_risk.py`

**Existing package** — `tests/test_monitoring/__init__.py` already exists. Do NOT recreate it.

**Direct call pattern** (`test_health_risk.py` lines 28–56):
```python
# Call the async function directly — no HTTP client needed for unit tests
result = await health_check(db=_mock_db())
assert result["circuit_breaker"] is True
```

**BreakerManager mock** (`test_health_risk.py` lines 42–53):
```python
patch("src.monitoring.health.BreakerManager") as mock_bm_cls,
...
mock_bm = MagicMock()
mock_bm.is_tripped = AsyncMock(return_value=True)
mock_bm_cls.return_value = mock_bm
```

**FakeAsyncRedis fixture** (`tests/test_risk/conftest.py` lines 22–33):
```python
@pytest.fixture
async def fake_redis():
    from fakeredis import FakeAsyncRedis
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()
```

Use this same fixture in `tests/test_monitoring/conftest.py` for monitor job tests that touch `BreakerManager`.

**autouse hook reset** (`tests/test_risk/conftest.py` lines 59–73):
```python
@pytest.fixture(autouse=True)
def _reset_alert_hooks():
    try:
        from src.risk.hooks import _alert_hooks
    except ImportError:
        yield
        return
    _alert_hooks.clear()
    yield
    _alert_hooks.clear()
```

Copy this into `tests/test_monitoring/conftest.py` — the `send_circuit_breaker_alert` hook registration in `test_notification_adapter.py` must not bleed across tests.

---

## Shared Patterns

### Async DB session (apply to: monitor_trades job, daily_summary job, `/api/dashboard` endpoint)

**Source:** `src/scheduler/jobs.py` lines 127–140 (run_pipeline) and `src/pipeline/runner.py` lines 162–210 (_persist)

```python
# Read-only query pattern:
async with AsyncSessionLocal() as session:
    stmt = (
        select(Candle)
        .where(Candle.instrument == "XAUUSD", Candle.timeframe == "H1", Candle.complete.is_(True))
        .order_by(Candle.timestamp.desc())
        .limit(200)
    )
    result = await session.execute(stmt)
    rows = list(reversed(result.scalars().all()))

# Write pattern (atomically):
async with AsyncSessionLocal() as session:
    async with session.begin():
        session.add(orm_object)
        await session.flush()
```

### Structlog (apply to: all new service files)

**Source:** Every file in `src/` — consistent module-level pattern:
```python
import structlog
log = structlog.get_logger(__name__)
```

Event key naming convention (derived from existing files):
- `jobs.X.complete` / `jobs.X.failed` — scheduler jobs (`src/scheduler/jobs.py` lines 58, 64, 99, 100)
- `risk.X.Y` — risk module events (`src/risk/runner.py` lines 43, 60, 76)
- `monitor.X` — new monitoring events (follow `risk.` prefix convention)
- `execution.X` — new execution events

### Decimal-only price arithmetic (apply to: monitor_trades, signal_sender, executor)

**Source:** `src/risk/runner.py` lines 95–101

```python
sizing = calculate_position_size(
    equity=self.settings.theoretical_equity_usd,  # Decimal
    entry_price=Decimal(str(candidate.entry_price)),  # cast str→Decimal
    sl_price=Decimal(str(candidate.sl_price)),
    atr_value=Decimal(str(regime.atr_value)),
    ...
)
```

Always cast candle ORM numeric fields via `Decimal(str(value))` before arithmetic. Never compare `Decimal` to `float`.

### Settings access (apply to: all new service files)

**Source:** `src/risk/runner.py` line 29, `src/monitoring/health.py` line 34:
```python
from src.config import get_settings
settings = get_settings()  # called in __init__ or at function start
```

### Error degradation (apply to: `/api/dashboard`, health additions)

**Source:** `src/monitoring/health.py` lines 61–71:
```python
try:
    r_risk = aioredis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
    circuit_breaker_val = await BreakerManager(redis=r_risk).is_tripped()
    await r_risk.aclose()
    ...
except Exception as exc:
    logger.warning("health.risk_wiring.failed", error=str(exc))
    circuit_breaker_val = False
    ...
```

### PostgreSQL upsert (apply to: monitor_trades trade-close transaction)

**Source:** RESEARCH.md Pattern 3 (no existing codebase analog):
```python
from sqlalchemy.dialects.postgresql import insert as pg_insert
from src.models.strategy_stats import StrategyStatsORM

stmt = pg_insert(StrategyStatsORM).values(...).on_conflict_do_update(
    index_elements=["strategy"],
    set_={
        "trade_count": StrategyStatsORM.trade_count + 1,
        ...
    }
)
await session.execute(stmt)
```

Execute this upsert in the **same** `session.begin()` block as the `TradeORM` status transition to `CLOSED`.

### Explicit JOIN for async ORM (apply to: monitor_trades strategy name lookup)

**Source:** RESEARCH.md Pitfall 5 (no existing codebase analog — but required):
```python
from sqlalchemy import select
from src.models.trade import TradeORM
from src.models.signal import ApprovedSignalORM, CandidateSignalORM

stmt = (
    select(TradeORM, CandidateSignalORM.strategy)
    .join(ApprovedSignalORM, TradeORM.approved_signal_id == ApprovedSignalORM.id)
    .join(CandidateSignalORM, ApprovedSignalORM.candidate_signal_id == CandidateSignalORM.id)
    .where(TradeORM.status.in_(["OPEN", "TP1_HIT"]))
)
```

Do NOT access `trade.approved_signal.candidate_signal.strategy` via attribute traversal — lazy loading raises in async sessions.

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `src/templates/dashboard.html` | component | request-response | No existing HTML templates; project is API-only through Phase 6 |

---

## Metadata

**Analog search scope:** `src/`, `tests/`, `alembic/versions/`
**Files scanned:** 47 source files, 42 test files, 2 migration files
**Pattern extraction date:** 2026-04-28
