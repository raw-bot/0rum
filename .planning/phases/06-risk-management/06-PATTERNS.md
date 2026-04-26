# Phase 6: Risk Management - Pattern Map

**Mapped:** 2026-04-26
**Files analyzed:** 19 (15 NEW + 4 MODIFIED)
**Analogs found:** 19 / 19 (100%)

All NEW files have a strong analog in the existing codebase — Phase 6 is mostly assembly, not invention. Internal `src/risk/` modules borrow patterns from `src/pipeline/` siblings; tests mirror `tests/test_pipeline/` line-for-line.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `src/risk/__init__.py` | package-marker | n/a | `src/pipeline/__init__.py` | exact |
| `src/risk/runner.py` | orchestrator (class with `evaluate()`) | request-response (per-candidate) | `src/pipeline/runner.py` (`PipelineRunner.run`) | exact (orchestrator role + per-item iteration) |
| `src/risk/gates.py` | service (pure async DB-read functions) | request-response over `AsyncSession` | `src/pipeline/quota.py` (`_count_today_approvals`) | exact (async `select(func.count())` against ORM) |
| `src/risk/sizer.py` | utility (pure synchronous math) | transform | `src/pipeline/dedup.py` (`dedup_signals`) + `src/pipeline/conflict_filter.py` (`filter_conflicts`) | role-match (pure function, no I/O); also closest math analog is `src/pipeline/ranker.py:99-130` for the composite-formula style |
| `src/risk/breaker.py` | service (stateful Redis client wrapper) | event-driven + cache CRUD | `src/monitoring/health.py:5,42-50` (only existing Redis consumer) | role-match (Redis async client); novel state-machine layer on top |
| `src/risk/events.py` | model (Pydantic v2 DTOs) | n/a (data carrier) | `src/models/signal_data.py:93-119` (`CandidateSignal`, `MarketRegime`) | exact |
| `src/risk/hooks.py` | utility (in-process pub-sub list) | pub-sub | none in codebase | NO ANALOG (novel — see "No Analog Found" below) |
| `tests/test_risk/__init__.py` | test marker | n/a | `tests/test_pipeline/__init__.py` | exact |
| `tests/test_risk/conftest.py` | test fixtures | n/a | `tests/test_strategies/conftest.py` (fixture style) + RESEARCH.md §7 (fakeredis) | role-match |
| `tests/test_risk/test_gates.py` | test (async, mocked session) | request-response | `tests/test_pipeline/test_quota.py:37-73` | exact (async + `AsyncMock` against session) |
| `tests/test_risk/test_sizer.py` | test (pure function, no fixtures) | transform | `tests/test_pipeline/test_dedup.py` | exact (pure-function tests, factory helper) |
| `tests/test_risk/test_breaker.py` | test (fakeredis async fixture) | event-driven | RESEARCH.md §7 sketch (no codebase analog — closest scaffolding is `tests/test_pipeline/test_quota.py` for the async-mocking idiom) | role-match |
| `tests/test_risk/test_runner.py` | test (orchestration with mocks) | request-response | `tests/test_pipeline/test_runner.py:49-99` | exact |
| `tests/test_risk/test_hooks.py` | test (callback registration) | pub-sub | none in codebase | NO ANALOG (use `unittest.mock.AsyncMock` for fake hook) |
| `tests/test_pipeline/test_runner_risk_step.py` | integration test | request-response | `tests/test_pipeline/test_runner.py:57-99` | exact (extend existing pattern with risk-step mocks) |
| `src/config.py` | config (Pydantic Settings field add) | n/a | `src/config.py:56-65` (existing risk fields block) | exact (insert in same block) |
| `src/pipeline/runner.py` | orchestrator (insert one step) | request-response | `src/pipeline/runner.py:86-105` (existing quota → status_map → persist sequence) | exact (in-file pattern reuse) |
| `src/monitoring/health.py` | controller (FastAPI route) | request-response | `src/monitoring/health.py:22-76` (existing route to extend) | exact (replace 3 placeholder values in-file) |
| `pyproject.toml` | config (add dep) | n/a | `pyproject.toml:9-29` (existing `dependencies` list) | exact (append `fakeredis>=2.20`) |

---

## Pattern Assignments

### `src/risk/__init__.py` (package-marker)

**Analog:** `src/pipeline/__init__.py`

**Excerpt** (`src/pipeline/__init__.py:1`):
```python
"""Signal pipeline modules for 0rum — dedup, conflict filter, ranker, quota."""
```

**Apply:** One-line module docstring. Add re-exports for the public surface only:
```python
"""Risk management package — pre-execution gates, ATR sizer, Redis circuit breaker."""

from src.risk.runner import RiskGateRunner, register_alert_hook
from src.risk.breaker import BreakerManager
```
Keep `gates`, `sizer`, `events`, `hooks` un-exported — pipeline imports `RiskGateRunner` only (D-02).

---

### `src/risk/runner.py` (orchestrator, request-response)

**Analog:** `src/pipeline/runner.py:1-115` — class-based orchestrator with sequential steps + structlog at start/end + per-step structured events.

**Imports pattern** (`src/pipeline/runner.py:12-25`):
```python
import structlog

from src.backtesting.regime_detector import RegimeDetector
from src.config import get_settings
from src.database import AsyncSessionLocal
from src.models.signal import ApprovedSignalORM, CandidateSignalORM
from src.models.signal_data import CandidateSignal, MarketRegime
from src.pipeline.conflict_filter import filter_conflicts
from src.pipeline.dedup import dedup_signals
from src.pipeline.quota import apply_quota
from src.pipeline.ranker import rank_signals

log = structlog.get_logger(__name__)
```

**Apply:** mirror this layout for `src/risk/runner.py`:
```python
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.signal_data import CandidateSignal, MarketRegime
from src.risk.breaker import BreakerManager
from src.risk.events import RiskDecision
from src.risk.gates import (
    count_same_direction_open,
    evaluate_daily_loss,
    evaluate_max_positions,
)
from src.risk.sizer import calculate_position_size

log = structlog.get_logger(__name__)
```

**Class skeleton + start/complete logging** (`src/pipeline/runner.py:28-62`, abbreviated):
```python
class PipelineRunner:
    """Orchestrates the full signal pipeline: dedup → conflict → regime → rank → quota → persist."""

    async def run(self, candidates: list[CandidateSignal], h1_candles: list) -> list[ApprovedSignalORM]:
        if not candidates:
            log.info("pipeline.runner.no_candidates")
            return []

        log.info("pipeline.runner.start", candidate_count=len(candidates))
        # ... sequential steps ...
        log.info("pipeline.runner.complete", total_candidates=len(candidates), approved=len(approved_orms), ...)
```

**Apply:** `RiskGateRunner.evaluate(...)` is per-candidate (single-item) so no `start/complete` envelope at this level — emit per-gate `risk.gate.rejected` events instead, mirroring `src/pipeline/quota.py:65-71`'s single structured event style. Class signature per RESEARCH §5 sketch.

**Per-step rejection-event pattern** (`src/pipeline/quota.py:64-71`):
```python
if rejected:
    log.info(
        "pipeline.quota_enforced",
        existing_today=existing,
        slots_remaining=slots_remaining,
        rejected_count=len(rejected),
    )
```

**Apply:** Use `log.info("risk.gate.rejected", gate=..., reason=..., strategy=..., direction=..., entry_price=..., signal_ref=id(candidate))`. The `signal_ref=id(...)` is consistent with the existing `status_map[id(sig)]` convention (`src/pipeline/runner.py:94-102`) — process-local only, never persisted (per CONTEXT D-05).

---

### `src/risk/gates.py` (service, request-response over AsyncSession)

**Analog:** `src/pipeline/quota.py:22-36` — async function taking no session arg (opens its own) using `select(func.count())`.

**Critical difference for this phase:** Phase 6 gates take an explicit `AsyncSession` parameter (per Pitfall 4 / RESEARCH §6). Do NOT replicate `src/pipeline/quota.py:29` (`async with AsyncSessionLocal() as session:`) inside each gate — the session is owned by the caller (`PipelineRunner.run`) and lives only for the risk loop, not for `_persist`.

**Imports pattern** (`src/pipeline/quota.py:1-20`):
```python
"""Quota gate for the 0rum signal pipeline.

Enforces MAX_SIGNALS_PER_DAY=5 per UTC calendar day per CLAUDE.md §10.4.
...
"""

from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select

from src.database import AsyncSessionLocal
from src.models.signal import ApprovedSignalORM
from src.models.signal_data import CandidateSignal

log = structlog.get_logger(__name__)
```

**Apply:**
```python
"""Risk gate query helpers — read-only against TradeORM.

Per D-01: TradeORM is the single source of truth for open positions, daily P&L,
and consecutive stops. Phase 6 NEVER inserts/updates TradeORM — Phase 7 will.
Empty TradeORM is a valid state (gates pass cleanly).
"""

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.trade import TradeORM

log = structlog.get_logger(__name__)
```

**Core async-aggregate pattern** (`src/pipeline/quota.py:22-36`):
```python
async def _count_today_approvals() -> int:
    today = datetime.now(timezone.utc).date()
    async with AsyncSessionLocal() as session:
        stmt = (
            select(func.count())
            .select_from(ApprovedSignalORM)
            .where(func.date(ApprovedSignalORM.created_at) == today)
        )
        result = await session.execute(stmt)
        return result.scalar_one()
```

**Apply** (RESEARCH §1, §2, §3): three async gate functions, each accepting a session argument; use `func.coalesce(func.sum(...), 0)` for SUM aggregates (Pitfall 3) and `select(func.count()).select_from(TradeORM)` for COUNT aggregates. Use `func.date_trunc('day', func.timezone('UTC', func.now()))` for the UTC-day boundary in `evaluate_daily_loss` (D-14).

**Boundary semantics (load-bearing):**
- RISK-01: `passed = daily_pnl_pct > daily_loss_limit` — at exactly `-0.03`, gate trips (`<=` is the trip per A3).
- RISK-02: `passed = open_count < max_positions` — at exactly `5`, gate trips (`>=` is the trip per A4).
- RISK-03: returns `int` count only; threshold check (`>= 4`) lives in the sizer, not in the gate (per D-04).

---

### `src/risk/sizer.py` (utility, pure transform)

**Analog (structure):** `src/pipeline/dedup.py` — pure sync function, in-memory only, no DB/Redis, no async.
**Analog (formula style):** `src/pipeline/ranker.py:99-130` — composite math with intermediate variables, structured log per item.

**Imports pattern** (`src/pipeline/dedup.py:1-15`):
```python
"""Signal deduplication filter for the 0rum pipeline.
...
No DB I/O — pure in-memory function. Callers track status externally.
"""

import structlog

from src.models.signal_data import CandidateSignal

log = structlog.get_logger(__name__)
```

**Apply:**
```python
"""ATR-based position sizer (RISK-04) — pure synchronous function.

Per AGENTS.md §12.2 + CONTEXT D-07:
  1. risk_pct = risk_per_trade × vol_factor
  2. risk_pct = min(risk_pct, hard_cap)              # cap AFTER vol bump
  3. risk_pct *= 0.5 if same_direction_open_count >= 4

No I/O — caller passes equity/regime/trade-context as kwargs.
"""

from decimal import Decimal

from src.risk.events import PositionSizing
```

**Note:** No structlog at the sizer level — the orchestrator (`src/risk/runner.py`) emits `risk.sizing.calculated` after calling the sizer (matches the way `src/pipeline/runner.py:107-114` logs `pipeline.runner.complete` rather than each substep doing its own envelope-log).

**Composite-formula style** (`src/pipeline/ranker.py:103-142`):
```python
ranked: list[tuple[CandidateSignal, float]] = []

for signal in signals:
    confidence = signal.confidence
    sl_distance = abs(signal.entry_price - signal.sl_price)
    if sl_distance == 0:
        rr_norm = 0.0
    else:
        rr_ratio = abs(signal.tp1_price - signal.entry_price) / sl_distance
        rr_norm = min(rr_ratio, 4.0) / 4.0
    # ...
    score = (
        confidence * 0.40
        + rr_norm * 0.30
        + wfe * 0.20
        + regime_alignment * 0.10
    )
```

**Apply:** mirror the `if sl_distance == 0: ... else: ...` guard. RESEARCH §sizer (lines 350-403) gives the full body. Pin the order-of-operations comments inline so a future refactor cannot accidentally flip cap-then-vol (Pitfall 2).

**Critical Decimal/float discipline (Pitfall 5):** `equity` is `Decimal`, `risk_per_trade` is `float`. Multiply via `equity * Decimal(str(risk_pct))` — never `Decimal(risk_pct)` directly (captures float imprecision).

---

### `src/risk/breaker.py` (service, Redis state machine)

**Analog (Redis client only):** `src/monitoring/health.py:5,42-50` — the project's only existing Redis consumer.

**Redis client pattern** (`src/monitoring/health.py:5,42-50`):
```python
import redis.asyncio as aioredis
# ...
try:
    r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
    await r.ping()
    await r.aclose()
    redis_connected = True
except Exception as exc:
    logger.error("health.redis_check_failed", error=str(exc))
```

**Apply:**
```python
import redis.asyncio as aioredis
import structlog

from src.config import get_settings

log = structlog.get_logger(__name__)
```

Use `aioredis.from_url(settings.redis_url, decode_responses=True)` — note `decode_responses=True` (RESEARCH §breaker:432) so reads return `str`, not `bytes`. `health.py` does NOT use `decode_responses=True` because it only calls `ping()`. The breaker reads/parses ISO timestamps so `decode_responses` is mandatory.

**Constructor injection pattern** (RESEARCH §breaker:430-434):
```python
class BreakerManager:
    def __init__(self, redis: aioredis.Redis | None = None):
        settings = get_settings()
        self._redis = redis or aioredis.from_url(settings.redis_url, decode_responses=True)
        self._stops_threshold = settings.circuit_breaker_stops
        self._cooldown_seconds = settings.circuit_breaker_cooldown_hours * 3600
```

**Apply:** Constructor-injected redis client (default = real Redis) is mandatory so tests can pass `FakeAsyncRedis()` (RESEARCH §7).

**Module-level Redis-key constants** (RESEARCH §breaker:424-426):
```python
CB_COUNTER = "risk:cb:consecutive_stops"
CB_TRIPPED_AT = "risk:cb:tripped_at"
CB_COOLDOWN_UNTIL = "risk:cb:cooldown_until"
```

**Apply:** Define at module top, NOT inside methods. Tests import these by name (RESEARCH §7 `from src.risk.breaker import BreakerManager, CB_COOLDOWN_UNTIL, CB_COUNTER`).

**State-machine method bodies:** Use RESEARCH §breaker:436-482 verbatim — covers `is_tripped`, `reset_if_expired`, `record_stop` (atomic `INCR` + conditional trip + TTL via `ex=`), `record_win`. Match structlog event names: `risk.circuit_breaker.reset`, `risk.circuit_breaker.tripped`, `risk.circuit_breaker.counter_reset`. These align with the `<package>.<component>.<event>` pattern used by `src/pipeline/runner.py:62,107` (`pipeline.runner.start`, `pipeline.runner.complete`).

---

### `src/risk/events.py` (Pydantic v2 DTOs)

**Analog:** `src/models/signal_data.py:93-119` (`CandidateSignal`) — Pydantic v2 BaseModel with typed fields and `Field(ge=...)` constraints; `:176-191` (`MarketRegime`) for the timestamp-bearing pattern.

**Imports pattern** (`src/models/signal_data.py:1-13`):
```python
"""Pydantic data classes for signals, enums, and candle data.

These are in-memory data transfer objects — distinct from the ORM models in signal.py.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field
```

**Apply:**
```python
"""Pydantic v2 DTOs emitted by the risk module.

Distinct from ORM (Phase 6 does NOT mutate any ORM table).
Phase 7 NOTIF-03 consumes CircuitBreakerAlert via the BreakerAlertHook surface.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
```

**DTO field-style pattern** (`src/models/signal_data.py:93-119`):
```python
class CandidateSignal(BaseModel):
    """In-memory candidate signal produced by a strategy (not ORM)."""

    strategy: StrategyName
    direction: Direction
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: Optional[float] = None
    confidence: float = Field(ge=0.0, le=1.0)
    timeframe: Timeframe
    params_snapshot: dict
```

**Apply:** Three DTOs per RESEARCH §4 (`PositionSizing`, `RiskDecision`, `CircuitBreakerAlert`). One deviation from `signal_data.py`: add `model_config = ConfigDict(frozen=True)` for risk DTOs since they cross phase boundaries (RESEARCH §4 footnote). `signal_data.py` itself omits `frozen=True`; the risk DTOs are stricter on purpose. This is intentional — flag for verifier.

**`Decimal` field type:** `risk_amount_usd: Decimal` and `size_lots: Decimal` (matches `TradeORM.pnl_pct: Decimal` in `src/models/trade.py:41` — Phase 7 will likely persist these fields, and the project's money-math convention is `Decimal`).

---

### `src/risk/hooks.py` (in-process pub-sub) — NO CODEBASE ANALOG

**No analog.** The existing codebase has no event-bus / hook / pub-sub primitive — every module communicates via direct function calls. Closest mental model is FastAPI's `APIRouter` registration in `src/monitoring/health.py:16` (`health_router = APIRouter()` then routes append themselves), but that's framework-provided, not a hand-rolled list.

**Apply pattern from RESEARCH §4:**
```python
"""In-process hook surface for cross-phase event delivery.

Phase 6 publishes CircuitBreakerAlert via _publish_alert(); Phase 7 NOTIF-03
appends a Telegram-sender callable to _alert_hooks. No telegram import lives here.
"""

from typing import Awaitable, Callable

import structlog

from src.risk.events import CircuitBreakerAlert

log = structlog.get_logger(__name__)

BreakerAlertHook = Callable[[CircuitBreakerAlert], Awaitable[None]]
_alert_hooks: list[BreakerAlertHook] = []


def register_alert_hook(hook: BreakerAlertHook) -> None:
    """Phase 7 NOTIF-03 calls this at startup to register the Telegram sender."""
    _alert_hooks.append(hook)


async def _publish_alert(alert: CircuitBreakerAlert) -> None:
    for hook in _alert_hooks:
        try:
            await hook(alert)
        except Exception as exc:  # noqa: BLE001 — hook failures must not break the runner
            log.error("risk.alert_hook.failed", error=str(exc))
```

**Caveat:** The `_alert_hooks` list is module-global, which means hooks registered in one test leak into the next. Add a `_reset_hooks()` private helper used only from `tests/test_risk/conftest.py` to clear the list per-test (mirrors the function-scoped `fake_redis` fixture rationale from Pitfall 8).

**Why a separate file (not in `runner.py`):** RESEARCH §4 puts the hook surface in `runner.py`. Putting it in `hooks.py` keeps `runner.py` focused on per-candidate orchestration and gives Phase 7 a more obvious import target (`from src.risk.hooks import register_alert_hook`). Either choice is acceptable; this PATTERNS.md recommends the split, matching the existing `src/pipeline/` one-concern-per-file convention.

---

### `tests/test_risk/__init__.py` (test marker)

**Analog:** `tests/test_pipeline/__init__.py` (currently empty file).

**Apply:** Empty file or single-line comment. Match exactly.

---

### `tests/test_risk/conftest.py` (test fixtures)

**Analog (style):** `tests/test_strategies/conftest.py:14,76-117` — fixture-style, function-scoped, MagicMock-heavy.
**Analog (fakeredis-specific):** RESEARCH §7:862-873 (no codebase analog — `fakeredis` is being introduced this phase).

**Imports + fixture pattern** (`tests/test_strategies/conftest.py:7-15,76-86`):
```python
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def candles_h1() -> list:
    """500 H1 candles — uptrend after index 300 ensures EMA50 > EMA200 at the end."""
    return make_candles("H1", 500)
```

**Apply** (per RESEARCH §7, with critical Pitfall 8 mitigation — function-scoped, NOT session-scoped):
```python
"""Fixtures for risk-module tests.

Every Redis fixture is FUNCTION-SCOPED (the default) — DO NOT use scope="session"
or scope="module" with FakeAsyncRedis. fakeredis-py issue #292 documents that
session-scoped instances bind to one event loop and break cross-test reuse.
"""

import pytest
from fakeredis import FakeAsyncRedis

from src.risk.breaker import BreakerManager


@pytest.fixture
async def fake_redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def breaker(fake_redis):
    return BreakerManager(redis=fake_redis)
```

**Mocked-session helper** (mirrored from RESEARCH §8 — no existing codebase analog because no other test mocks `AsyncSession.execute()` directly; existing tests mock the helper functions instead, e.g., `tests/test_pipeline/test_quota.py:41`):
```python
from unittest.mock import AsyncMock, MagicMock


def _mock_session(scalar_value):
    """Build a MagicMock AsyncSession that returns scalar_value from scalar_one()."""
    session = MagicMock()
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=scalar_value)
    session.execute = AsyncMock(return_value=result)
    return session
```

**Hook-list reset fixture** (autouse, function-scoped):
```python
@pytest.fixture(autouse=True)
def _reset_alert_hooks():
    from src.risk.hooks import _alert_hooks
    _alert_hooks.clear()
    yield
    _alert_hooks.clear()
```

---

### `tests/test_risk/test_gates.py` (test, async with mocked session)

**Analog:** `tests/test_pipeline/test_quota.py:37-73` — async test pattern with `AsyncMock` patching the DB-fetch helper.

**Imports + signal factory** (`tests/test_pipeline/test_quota.py:1-34`):
```python
"""Unit tests for src/pipeline/quota.py — MAX_SIGNALS_PER_DAY quota gate."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from src.models.signal_data import (
    CandidateSignal,
    Direction,
    StrategyName,
    Timeframe,
)
from src.pipeline.quota import apply_quota
```

**Apply:** Mirror imports for `src/risk/gates`. Reuse the `_mock_session(scalar_value)` helper from `conftest.py` (above). `make_signal()` factory is needed for runner tests but not for gate tests — gates take session+threshold only.

**Test-naming + body pattern** (`tests/test_pipeline/test_quota.py:37-44`):
```python
@pytest.mark.asyncio
async def test_quota_all_pass_when_slots_available():
    """0 existing approvals today + 3 ranked signals + max_per_day=5 → all 3 approved."""
    sigs = [(make_signal(), 0.8), (make_signal(), 0.7), (make_signal(), 0.6)]
    with patch("src.pipeline.quota._count_today_approvals", AsyncMock(return_value=0)):
        approved, rejected = await apply_quota(sigs, max_per_day=5)
    assert len(approved) == 3
    assert len(rejected) == 0
```

**Apply:** RESEARCH §8 sketch covers 6 of the required 9 tests. Add the boundary-explicit empty-table tests for each gate (per Validation Architecture rows for RISK-01/RISK-02 "passes when empty"). Note: the `@pytest.mark.asyncio` decorator is used in existing tests even though `asyncio_mode = "auto"` is set in `pyproject.toml:38` — matches the redundant-but-explicit project convention.

---

### `tests/test_risk/test_sizer.py` (test, pure function)

**Analog:** `tests/test_pipeline/test_dedup.py` — pure-function tests with a `make_signal()` factory and parametric assertions; no fixtures, no `@pytest.mark.asyncio`.

**Imports + helper pattern** (`tests/test_pipeline/test_dedup.py:1-30`):
```python
"""Unit tests for src/pipeline/dedup.py — signal deduplication logic."""

from src.models.signal_data import (
    CandidateSignal,
    Direction,
    StrategyName,
    Timeframe,
)
from src.pipeline.dedup import dedup_signals


def make_signal(...) -> CandidateSignal: ...
```

**Test body pattern** (`tests/test_pipeline/test_dedup.py:33-41`):
```python
def test_dedup_same_strategy_direction_removes_earlier():
    """Identical strategy+direction+close entry price → earlier signal deduped, later survives."""
    sig1 = make_signal(entry_price=2340.0)
    sig2 = make_signal(entry_price=2340.5)
    survivors, deduped = dedup_signals([sig1, sig2])
    assert sig2 in survivors
```

**Apply:** RESEARCH §9 sketch (lines 990-1055) covers all 7 required tests. Use `pytest.approx(...)` for float comparisons (matches RESEARCH §9). Pull the `COMMON` kwargs dict to top of file (RESEARCH §9 lines 992-1000) so each test only varies the parameters it cares about.

---

### `tests/test_risk/test_breaker.py` (test, fakeredis async)

**Analog:** RESEARCH §7 (lines 851-919) is the primary reference. No close codebase analog because `fakeredis` is being introduced.

**Closest in-codebase async-test idiom** (`tests/test_pipeline/test_runner.py:1-15`):
```python
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.signal_data import (...)
from src.pipeline.runner import PipelineRunner
```

**Apply** per RESEARCH §7 verbatim. Critical points to preserve:
1. `@pytest.fixture` with NO `scope=` argument (defaults to function — Pitfall 8).
2. `await r.aclose()` in fixture teardown (NOT `await r.close()`, deprecated).
3. Manually expire cooldown by writing a past timestamp into Redis directly: `await fake_redis.set(CB_COOLDOWN_UNTIL, past)` — avoids `time.sleep()` and keeps tests fast.

---

### `tests/test_risk/test_runner.py` (test, orchestration with mocks)

**Analog:** `tests/test_pipeline/test_runner.py:49-99` — orchestration test mocking each downstream step with `patch(...)`.

**Multi-patch pattern** (`tests/test_pipeline/test_runner.py:66-93`):
```python
with (
    patch("src.pipeline.runner.RegimeDetector") as mock_rd_cls,
    patch("src.pipeline.runner.rank_signals", new_callable=AsyncMock) as mock_rank,
    patch("src.pipeline.runner.apply_quota", new_callable=AsyncMock) as mock_quota,
    patch("src.pipeline.runner.AsyncSessionLocal") as mock_session_cls,
    patch("src.pipeline.runner.get_settings") as mock_settings,
):
    mock_rd = AsyncMock()
    mock_rd.detect = AsyncMock(return_value=mock_regime)
    mock_rd_cls.return_value = mock_rd

    mock_rank.return_value = [(buy, 0.72)]
    mock_quota.return_value = ([(buy, 0.72)], [])
    # ...
```

**Apply** for `test_runner.py` (covers RiskGateRunner, not pipeline):
```python
with (
    patch("src.risk.runner.evaluate_daily_loss", new_callable=AsyncMock) as mock_dl,
    patch("src.risk.runner.evaluate_max_positions", new_callable=AsyncMock) as mock_mp,
    patch("src.risk.runner.count_same_direction_open", new_callable=AsyncMock) as mock_conc,
    patch("src.risk.runner.calculate_position_size") as mock_sizer,
):
    # configure return values per-test
```

**Required tests** (per Validation Architecture Wave 0):
- `test_breaker_short_circuits_all_gates` — when `breaker.is_tripped()` returns True, the three gate mocks and `calculate_position_size` are NOT called.
- `test_concentration_passes_with_reduced_size` — `same_dir_count >= 4` returns `RiskDecision(passed=True, concentration_reduced=True)`.
- `test_alert_hook_invoked_on_trip` — register an `AsyncMock` as a hook, force `record_stop` to return an alert, assert hook was awaited once.
- `test_daily_loss_emits_structured_log` — use `caplog` or `structlog.testing.capture_logs()` to assert the `risk.gate.rejected` event with correct fields.
- `test_daily_loss_does_not_trip_breaker` (D-15) — assert `record_stop` was never called after a daily-loss rejection.

**Note:** existing tests use plain `caplog` only sparingly; the project ships `structlog>=24.2.0` which has `structlog.testing.capture_logs()` as a context manager. RESEARCH does not specify a preference. Recommended: `structlog.testing.capture_logs()` for the explicit "structured event" assertions, since structlog routes through its own pipeline.

---

### `tests/test_risk/test_hooks.py` (test, pub-sub) — NO ANALOG

**No codebase analog.** Apply by parallel reasoning:

```python
import pytest
from unittest.mock import AsyncMock
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from src.risk.events import CircuitBreakerAlert
from src.risk.hooks import _publish_alert, register_alert_hook


@pytest.mark.asyncio
async def test_register_then_publish_invokes_hook():
    hook = AsyncMock()
    register_alert_hook(hook)
    alert = CircuitBreakerAlert(
        tripped_at=datetime.now(timezone.utc),
        consecutive_stops=8,
        cooldown_until=datetime.now(timezone.utc) + timedelta(hours=24),
        last_stop_strategy="liquidity_sweep",
        last_stop_trade_id=uuid4(),
    )
    await _publish_alert(alert)
    hook.assert_awaited_once_with(alert)


@pytest.mark.asyncio
async def test_hook_failure_does_not_propagate():
    failing = AsyncMock(side_effect=RuntimeError("telegram down"))
    working = AsyncMock()
    register_alert_hook(failing)
    register_alert_hook(working)
    alert = CircuitBreakerAlert(...)
    await _publish_alert(alert)  # MUST NOT raise
    working.assert_awaited_once()
```

The `_reset_alert_hooks` autouse fixture from `conftest.py` ensures isolation between these tests.

---

### `tests/test_pipeline/test_runner_risk_step.py` (integration test)

**Analog:** `tests/test_pipeline/test_runner.py:57-99` — full pipeline run with all dependencies mocked.

**Apply:** Extend the existing pattern by adding `patch("src.pipeline.runner.RiskGateRunner")` to the multi-patch block. Required scenarios per Validation Architecture:
- `test_risk_rejection_no_approved_orm` — when `RiskGateRunner.evaluate` returns `passed=False`, no `ApprovedSignalORM` is `session.add()`-ed for that candidate.
- `test_risk01_rejection_propagates_to_status_map` — `CandidateSignalORM.status='REJECTED'` for rejected candidates.

**Why placed under `tests/test_pipeline/`** (not `tests/test_risk/`): the test exercises `src/pipeline/runner.py`'s integration with the new step; co-locating with other pipeline-runner tests follows the project convention that integration tests live next to the orchestrator they exercise. RESEARCH §1136 places this under `tests/test_risk/test_pipeline_integration.py` instead — either is acceptable, but `tests/test_pipeline/` is closer to the existing `test_runner.py`. Flag this as a planner discretion call.

---

### `src/config.py` (config, MODIFIED)

**Analog (in-file):** `src/config.py:56-65` — existing risk fields block.

**Existing block** (`src/config.py:56-65`):
```python
# Risk (TOUS FIXÉS — pas dans l'optimizer)
risk_per_trade: float = 0.01
daily_loss_limit: float = -0.03
max_positions: int = 5
max_signals_per_day: int = 5
circuit_breaker_stops: int = 8
circuit_breaker_cooldown_hours: int = 24
atr_high_vol_percentile: int = 90
atr_low_vol_percentile: int = 10
hard_cap_risk: float = 0.02
```

**Apply:** Insert `theoretical_equity_usd: Decimal = Decimal("10000")` inside this block (per D-09). Add `from decimal import Decimal` import at top. Verify in a settings test that `Settings(_env_file=None).theoretical_equity_usd == Decimal("10000")` and that `THEORETICAL_EQUITY_USD=20000` env override yields `Decimal("20000")` (Open Question 3).

`redis_url: str = "redis://redis:6379/0"` already exists (`src/config.py:47`) — no change needed.

---

### `src/pipeline/runner.py` (orchestrator, MODIFIED)

**Insertion point:** between line 90 (end of `apply_quota` return assignment) and line 92 (start of `status_map` build).

**In-file context to mirror** (`src/pipeline/runner.py:86-105`):
```python
# Step 5: Quota
settings = get_settings()
approved_ranked, quota_rejected = await apply_quota(
    ranked, max_per_day=settings.max_signals_per_day
)

# Build status map: id(signal) → final status string
status_map: dict[int, str] = {}
for sig in deduped:
    status_map[id(sig)] = "DEDUPED"
for sig in conflict_rejected:
    status_map[id(sig)] = "REJECTED"
for sig in quota_rejected:
    status_map[id(sig)] = "REJECTED"
for sig, _ in approved_ranked:
    status_map[id(sig)] = "APPROVED"

# Step 6: Persist all data in a single atomic transaction (D-04)
approved_orms = await self._persist(candidates, approved_ranked, regime, status_map)
```

**Apply:** Insert per RESEARCH §6 (lines 805-846). Critical points:
1. `RiskGateRunner` instance construction is per-pipeline-run (matches per-call construction of `RegimeDetector()` at line 81).
2. Open ONE `async with AsyncSessionLocal() as risk_session:` for the whole risk loop, then close it BEFORE `_persist()` opens its own session+transaction (Pitfall 4).
3. Insert `for sig in risk_rejected: status_map[id(sig)] = "REJECTED"` into the `status_map` build, before the `APPROVED` loop (so a risk-rejected sig that somehow appears in `approved_ranked` is overwritten — defensive, but cheap).
4. Replace `approved_ranked = risk_passed` so `_persist` only writes `ApprovedSignalORM` for risk-passed candidates.
5. Add `risk_rejected=len(risk_rejected)` to the final `pipeline.runner.complete` log event (line 107-114) for symmetry with existing `deduped`, `conflict_rejected`, `quota_rejected` fields.

**Add to imports block:**
```python
from src.risk.runner import RiskGateRunner
```

---

### `src/monitoring/health.py` (controller, MODIFIED)

**Analog (in-file):** `src/monitoring/health.py:64-76` — existing JSON response block with placeholders.

**Existing pattern to extend** (`src/monitoring/health.py:64-76`):
```python
return {
    "status": overall_status,
    "uptime_hours": uptime_hours,
    "execution_mode": settings.execution_mode.value,
    "circuit_breaker": False,        # ← REPLACE
    "open_positions": 0,             # ← REPLACE
    "daily_pnl_pct": 0.0,            # ← REPLACE
    "signals_today": 0,
    "last_candle_fetch": get_last_candle_fetch(),
    "strategies_active": 4,
    "redis_connected": redis_connected,
    "postgres_connected": postgres_connected,
}
```

**Apply:** Replace the three placeholders with real values. Per Open Question 4, the cleanest split is:
1. Add a thin helper `async def get_open_positions(session) -> int` AND `async def get_daily_pnl_pct(session) -> float` to `src/risk/gates.py` (or expose the existing gate functions to return just the count/pnl when called without thresholds — but a thin wrapper is cleaner for this purpose).
2. Health route imports both, plus `BreakerManager`:
```python
from src.risk.breaker import BreakerManager
from src.risk.gates import get_daily_pnl_pct, get_open_positions
```
3. Inside the route, after the postgres connectivity check:
```python
breaker_tripped = False
open_positions = 0
daily_pnl_pct = 0.0
try:
    breaker_tripped = await BreakerManager().is_tripped()
    open_positions = await get_open_positions(db)
    daily_pnl_pct = await get_daily_pnl_pct(db)
except Exception as exc:
    logger.error("health.risk_check_failed", error=str(exc))
```

**Critical:** wrap in a single `try/except` so a Redis or Postgres glitch degrades health to `degraded` but does NOT 500 the endpoint. The existing pattern at lines 35-50 already follows this (`postgres_connected = False` default + try/except). Mirror exactly.

**`signals_today` placeholder stays** — that's quota-counted approvals; not Phase 6's responsibility (per CONTEXT canonical_refs final bullet).

**`strategies_active` stays hardcoded** — known gap per CLAUDE.md "Known Gaps", not Phase 6 scope.

---

### `pyproject.toml` (config, MODIFIED)

**Analog (in-file):** `pyproject.toml:9-29` — existing `dependencies` list.

**Existing pattern** (`pyproject.toml:9-29`):
```toml
dependencies = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.30.0",
    # ...
    "pytest>=8.2.0",
    "pytest-asyncio>=0.23.0",
    "aiosqlite>=0.19.0",
    "respx>=0.20.0",
]
```

**Apply:** Append `"fakeredis>=2.20",` to the list (alphabetical order suggests inserting between `"asyncpg>=0.29.0"` and another dep, but the existing file is not alphabetized — it groups by category. Match precedent: append at the end with the other test-only deps `pytest`, `pytest-asyncio`, `aiosqlite`, `respx`). Per RESEARCH A8: the project keeps test deps in main `dependencies` (no `[project.optional-dependencies.test]` group exists), so `fakeredis` goes in main `dependencies`. Document the choice in the commit message.

---

## Shared Patterns

### Async-everywhere on I/O paths
**Source:** `src/pipeline/runner.py` (every method `async def`), `src/pipeline/quota.py:22,39`, `src/pipeline/ranker.py:34,71`.
**Apply to:** `src/risk/gates.py` (all 3 functions), `src/risk/runner.py` (`evaluate`), `src/risk/breaker.py` (all methods), `src/risk/hooks.py` (`_publish_alert`).
**Excerpt** (`src/pipeline/quota.py:22-29`):
```python
async def _count_today_approvals() -> int:
    today = datetime.now(timezone.utc).date()
    async with AsyncSessionLocal() as session:
        ...
```

### Module-level structlog logger
**Source:** every module that logs — e.g. `src/pipeline/quota.py:19`, `src/pipeline/runner.py:25`, `src/monitoring/health.py:15`.
**Apply to:** `src/risk/runner.py`, `src/risk/gates.py`, `src/risk/breaker.py`, `src/risk/hooks.py`. Sizer does NOT log (pure function — orchestrator logs the result).
**Excerpt:**
```python
import structlog
log = structlog.get_logger(__name__)
```

### Structured event naming convention
**Source:** `src/pipeline/runner.py:62,66,74,107` — `<package>.<component>.<event>` (`pipeline.runner.start`, `pipeline.runner.after_dedup`, `pipeline.runner.complete`).
**Apply to:** `risk.gate.rejected`, `risk.sizing.calculated`, `risk.circuit_breaker.tripped`, `risk.circuit_breaker.reset`, `risk.circuit_breaker.counter_reset`, `risk.alert_hook.failed`. Maintain the dot-separated three-segment style.

### Pydantic v2 DTO style (immutable variant for risk)
**Source:** `src/models/signal_data.py:93-119` (`CandidateSignal`).
**Apply to:** `src/risk/events.py` (3 DTOs).
**Deviation from source:** add `model_config = ConfigDict(frozen=True)` because risk DTOs cross phase boundaries (Phase 6 emits, Phase 7 consumes the alert). `signal_data.py` omits `frozen=True` since pipeline DTOs are constructed and mutated in-process. Flag this deviation explicitly to the verifier.

### ORM/DTO strict separation
**Source:** CLAUDE.md "Invariants" — `src/models/signal.py` is ORM, `src/models/signal_data.py` is Pydantic.
**Apply to:** `src/risk/events.py` is Pydantic ONLY. No ORM imports. Phase 6 reads `TradeORM` (through gate query helpers) but never writes any ORM (per D-01); never adds a new ORM class.

### Async DB read with `select(func.count())` / `func.coalesce(func.sum(...), 0)`
**Source:** `src/pipeline/quota.py:29-36` for COUNT; RESEARCH §1 for SUM-with-COALESCE (no existing analog because no other code aggregates `TradeORM`).
**Apply to:** all three functions in `src/risk/gates.py`.
**Critical (Pitfall 3):** never use bare `func.sum(...)` on a possibly-empty table — always `func.coalesce(..., 0)`.

### Test environment-variable bootstrap
**Source:** `tests/conftest.py:11-13` — `os.environ.setdefault(...)` before any `src.*` import.
**Apply to:** Phase 6 needs no additional env vars at test bootstrap (`THEORETICAL_EQUITY_USD` has a default). If a settings-override test wants to verify env loading, set `os.environ["THEORETICAL_EQUITY_USD"]` inside the test body (NOT in conftest) and instantiate a fresh `Settings()` (since `get_settings()` is not actually cached per CLAUDE.md "Known Gaps").

### Mocked-async-helper testing idiom
**Source:** `tests/test_pipeline/test_quota.py:41` — `with patch("src.pipeline.quota._count_today_approvals", AsyncMock(return_value=0)):`.
**Apply to:** `tests/test_risk/test_runner.py` for mocking gate/sizer functions; `tests/test_risk/test_breaker.py` does not need this (uses real `fakeredis`).

### Multi-patch context-manager idiom for orchestrator tests
**Source:** `tests/test_pipeline/test_runner.py:66-93` — parenthesized `with (patch(...), patch(...), ...):`.
**Apply to:** `tests/test_risk/test_runner.py` and `tests/test_pipeline/test_runner_risk_step.py`.

### Per-test factory for `CandidateSignal`
**Source:** identical `make_signal()` defined in `tests/test_pipeline/test_dedup.py:12-30`, `test_quota.py:16-34`, `test_conflict_filter.py:12-30`, `test_runner.py:18-36`.
**Apply to:** `tests/test_risk/test_runner.py` and `tests/test_pipeline/test_runner_risk_step.py`. Sizer tests do NOT need it (pass kwargs directly per RESEARCH §9). Resist the urge to extract this into `conftest.py` — the project precedent is to duplicate it per test file (5+ existing copies). One PATTERNS observation: this duplication is a smell, but staying consistent with precedent has zero technical risk and avoids a refactor not in Phase 6 scope.

---

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `src/risk/hooks.py` | utility (in-process pub-sub list) | pub-sub | No event-bus, observer, or hook primitive exists in the codebase. Use the RESEARCH §4 sketch verbatim. |
| `tests/test_risk/test_hooks.py` | test (callback registration) | pub-sub | No precedent for testing module-level callable lists. Use `unittest.mock.AsyncMock` per the body shown in `tests/test_risk/test_hooks.py` section above. |

The `BreakerManager` class itself uses Redis (analog: `src/monitoring/health.py:5,42-50`) but the state-machine layer over Redis (counters, TTLs, atomic INCR) has no analog. RESEARCH §breaker:411-483 is the authoritative pattern source for that.

---

## Metadata

**Analog search scope:**
- `src/pipeline/` (all 5 files) — primary analog source for orchestrator, gates, sizer, status-map integration.
- `src/monitoring/health.py` — only existing Redis client usage; primary analog for `BreakerManager` import + connection style.
- `src/models/signal_data.py`, `src/models/signal.py`, `src/models/trade.py`, `src/models/regime.py` — DTO and ORM patterns.
- `src/config.py` — settings field placement.
- `tests/test_pipeline/` (all 6 files) — test idioms (factories, multi-patch, async mocking).
- `tests/test_strategies/conftest.py` — fixture style.
- `tests/conftest.py` — env-var bootstrap.

**Files scanned:** ~25 (read in full or in relevant sections).

**Pattern extraction date:** 2026-04-26.

**Classification confidence:** HIGH for all 19 files. The two no-analog files (`hooks.py`, `test_hooks.py`) have explicit RESEARCH-derived patterns to fall back on.
