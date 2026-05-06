# Phase 7 Web-Only Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Telegram completely from Phase 7 and make the web dashboard/API the only operator monitoring surface.

**Architecture:** Normal bot decisions stay local: persisted in PostgreSQL, logged with structlog, and exposed through `/api/dashboard` and `/dashboard`. `ExecutionRouter` no longer sends messages; in signal mode it records a local delivery/audit success so `ApprovedSignalORM.execution_status` can progress without an external dependency. Scheduler trade monitoring keeps lifecycle/stat updates but no longer emits notifications.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, APScheduler, Jinja2, vanilla HTML/CSS/JS, pytest.

---

## File Structure

- Modify `src/config.py`: remove Telegram settings.
- Modify `.env.example`: remove Telegram env vars.
- Modify `pyproject.toml` and `uv.lock`: remove `python-telegram-bot`.
- Modify `src/main.py`: remove Telegram Bot startup/shutdown and hook wiring.
- Modify `src/execution/executor.py`: remove `SignalSender` dependency and make signal mode local.
- Delete `src/execution/signal_sender.py`: Telegram-only adapter.
- Modify `src/execution/__init__.py`: remove SignalSender wording.
- Delete `src/monitoring/telegram_bot.py`: Telegram-only monitoring adapter.
- Modify `src/scheduler/jobs.py`: remove `_telegram_bot`, `_set_monitor_services`, lifecycle notification calls, and `daily_summary` Telegram job.
- Modify `src/risk/__init__.py`, `src/risk/events.py`, and `src/risk/hooks.py`: remove Telegram references from docstrings/comments while keeping generic alert hook support if still useful.
- Modify `src/monitoring/health.py`: rename Telegram-oriented comments around `signals_today`.
- Modify `src/monitoring/dashboard.py`: expose additional monitoring data needed by a web-only operator surface.
- Modify `src/templates/dashboard.html`: render the new web-only monitoring sections.
- Modify tests under `tests/`: remove Telegram env setup/tests, add web-only startup/router/scheduler/dashboard assertions.
- Modify `.planning/phases/07-signal-mode-monitoring/07-VERIFICATION.md` and `07-HUMAN-UAT.md`: replace Telegram UAT with web-only UAT.

---

### Task 1: Remove Telegram Configuration And Dependency

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`
- Modify: `tests/conftest.py`
- Modify: `tests/test_execution/test_executor.py`
- Modify: `tests/test_ingestion/test_market_client.py`
- Modify: `tests/test_ingestion/test_ig_client.py`
- Modify: `tests/test_ingestion/test_candle_fetcher.py`
- Modify: `tests/test_backtesting/conftest.py`
- Modify: `tests/test_backtesting/test_optimizer.py`
- Modify: `tests/test_backtesting/test_optimizer_integration.py`
- Modify: `tests/test_backtesting/test_scheduler_wiring.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Write config tests that prove Telegram is not required**

Add to `tests/test_config/test_settings.py`:

```python
def test_settings_do_not_require_telegram(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

    s = Settings()

    assert not hasattr(s, "telegram_bot_token")
    assert not hasattr(s, "telegram_chat_id")
```

- [ ] **Step 2: Run the new config test and verify it fails before code changes**

Run:

```bash
pytest tests/test_config/test_settings.py::test_settings_do_not_require_telegram -q
```

Expected before implementation: failure because `Settings` still has Telegram fields.

- [ ] **Step 3: Remove Telegram settings from config and env examples**

In `src/config.py`, delete:

```python
    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str
```

In `.env.example`, delete:

```env
# === TELEGRAM ===
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

In test files, delete only these setup lines when present:

```python
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")
os.environ.setdefault("TELEGRAM_CHAT_ID", "12345")
```

In `pyproject.toml`, remove:

```toml
    "python-telegram-bot>=21.0",
```

Regenerate `uv.lock` with:

```bash
uv lock
```

- [ ] **Step 4: Verify config passes**

Run:

```bash
pytest tests/test_config/test_settings.py -q
```

Expected: all config tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/config.py .env.example tests pyproject.toml uv.lock
git commit -m "refactor(07): remove telegram configuration"
```

---

### Task 2: Replace Telegram Signal Delivery With Local Execution Audit

**Files:**
- Modify: `src/execution/executor.py`
- Modify: `src/execution/__init__.py`
- Delete: `src/execution/signal_sender.py`
- Modify: `tests/test_execution/test_executor.py`
- Delete: `tests/test_execution/test_signal_sender.py`

- [ ] **Step 1: Rewrite executor tests for local signal mode**

Replace `tests/test_execution/test_executor.py` with tests shaped like:

```python
"""Tests for ExecutionRouter local execution routing."""

from decimal import Decimal
from unittest.mock import patch

import pytest


def make_signal():
    from src.models.signal_data import CandidateSignal, Direction, StrategyName, Timeframe

    return CandidateSignal(
        strategy=StrategyName.LIQUIDITY_SWEEP,
        direction=Direction.BUY,
        entry_price=Decimal("2340.50"),
        sl_price=Decimal("2325.20"),
        tp1_price=Decimal("2358.80"),
        tp2_price=Decimal("2377.10"),
        confidence=0.82,
        timeframe=Timeframe.M15,
        params_snapshot={},
    )


@pytest.mark.asyncio
async def test_signal_mode_returns_true_without_external_sender():
    from src.config import ExecutionMode
    from src.execution.executor import ExecutionRouter

    router = ExecutionRouter()
    with patch("src.execution.executor.get_settings") as mock_settings:
        mock_settings.return_value.execution_mode = ExecutionMode.SIGNAL
        result = await router.execute(make_signal(), Decimal("0.10"))

    assert result is True


@pytest.mark.asyncio
async def test_auto_mode_raises_not_implemented():
    from src.config import ExecutionMode
    from src.execution.executor import ExecutionRouter

    router = ExecutionRouter()
    with patch("src.execution.executor.get_settings") as mock_settings:
        mock_settings.return_value.execution_mode = ExecutionMode.AUTO
        with pytest.raises(NotImplementedError):
            await router.execute(make_signal(), Decimal("0.10"))
```

- [ ] **Step 2: Run executor tests and verify they fail before code changes**

Run:

```bash
pytest tests/test_execution/test_executor.py -q
```

Expected before implementation: constructor failure because `ExecutionRouter` still requires `signal_sender`.

- [ ] **Step 3: Implement local-only ExecutionRouter**

Replace the Telegram-specific import and constructor in `src/execution/executor.py` with:

```python
from decimal import Decimal

import structlog

from src.config import ExecutionMode, get_settings
from src.models.signal_data import CandidateSignal

log = structlog.get_logger(__name__)


class ExecutionRouter:
    """Routes approved signals through the configured execution path.

    Phase 7 signal mode is local-only: decisions are persisted and exposed via
    the dashboard. No external notification channel is used.
    """

    async def execute(self, signal: CandidateSignal, size_lots: Decimal) -> bool:
        mode = get_settings().execution_mode
        if mode == ExecutionMode.SIGNAL:
            return await self._execute_signal_mode(signal, size_lots)
        if mode == ExecutionMode.AUTO:
            raise NotImplementedError(
                "Auto mode broker execution not implemented — Phase 8 extension point."
            )
        raise ValueError(f"Unknown execution mode: {mode!r}")

    async def _execute_signal_mode(
        self, signal: CandidateSignal, size_lots: Decimal
    ) -> bool:
        log.info(
            "execution.signal_mode.recorded",
            strategy=signal.strategy.value,
            direction=signal.direction.value,
            size_lots=str(size_lots),
        )
        return True
```

Update `src/execution/__init__.py` docstring to:

```python
"""src/execution package — local execution routing."""
```

Delete `src/execution/signal_sender.py` and `tests/test_execution/test_signal_sender.py`.

- [ ] **Step 4: Verify execution tests pass**

Run:

```bash
pytest tests/test_execution -q
```

Expected: execution tests pass with no Telegram imports.

- [ ] **Step 5: Commit**

```bash
git add src/execution tests/test_execution
git commit -m "refactor(07): make signal execution local"
```

---

### Task 3: Remove Telegram Startup And Scheduler Notification Wiring

**Files:**
- Modify: `src/main.py`
- Modify: `src/scheduler/jobs.py`
- Delete: `src/monitoring/telegram_bot.py`
- Modify: `tests/test_backtesting/test_scheduler_wiring.py`
- Delete: `tests/test_monitoring/test_telegram_bot.py`
- Modify: `tests/test_monitoring/conftest.py`
- Modify: `tests/test_monitoring/test_monitor_trades.py`

- [ ] **Step 1: Add startup import smoke test**

Add to `tests/test_execution/test_scaffold.py`:

```python
def test_main_imports_without_telegram_dependency():
    import src.main as main

    assert main.app.title == "0rum"
```

- [ ] **Step 2: Run the smoke test and verify it fails or still imports through Telegram path**

Run:

```bash
pytest tests/test_execution/test_scaffold.py::test_main_imports_without_telegram_dependency -q
```

Expected before implementation: may pass on import because Telegram is imported in lifespan only; full suite still fails after dependency removal until startup code is changed.

- [ ] **Step 3: Remove Telegram from `src/main.py`**

In `src/main.py`, delete the block that imports `telegram.Bot`, initializes `bot`, creates `SignalSender` and `TelegramBot`, registers the Telegram circuit breaker hook, calls `_set_monitor_services`, and shuts the bot down.

Replace service wiring with:

```python
    from src.execution.executor import ExecutionRouter
    from src.scheduler.jobs import _set_pipeline_runner

    from src.pipeline.runner import PipelineRunner
    runner = PipelineRunner(router=ExecutionRouter())
    _set_pipeline_runner(runner)
    logger.info("app.execution_services_wired")
```

On shutdown, delete:

```python
    await bot.shutdown()
    logger.info("app.telegram_bot_shutdown")
```

- [ ] **Step 4: Remove Telegram service state from scheduler**

In `src/scheduler/jobs.py`, delete:

```python
_telegram_bot: "Any | None" = None
```

Delete `_set_monitor_services`.

Remove all blocks like:

```python
            if _telegram_bot is not None:
                await _telegram_bot.send_lifecycle_notification(...)
```

and:

```python
        if alert is not None and _telegram_bot is not None:
            await _telegram_bot.send_circuit_breaker_alert(alert)
```

Keep `BreakerManager.record_stop()` and `BreakerManager.record_win()` intact.

Delete `daily_summary()` completely and remove this scheduler job:

```python
    scheduler.add_job(
        daily_summary,
        trigger=CronTrigger(hour=0, minute=0, timezone="UTC"),
        id="daily_summary",
        name="Send daily Telegram summary at 00:00 UTC",
        max_instances=1,
        replace_existing=True,
    )
```

- [ ] **Step 5: Update scheduler tests**

In `tests/test_backtesting/test_scheduler_wiring.py`, remove `daily_summary` from the expected scheduler job IDs. Keep `monitor_trades`.

Delete `tests/test_monitoring/test_telegram_bot.py`.

In `tests/test_monitoring/conftest.py`, remove fixtures that reset alert hooks only for Telegram tests unless they are still used by `tests/test_risk/test_hooks.py`.

In `tests/test_monitoring/test_monitor_trades.py`, remove assertions that expect lifecycle notification calls. Keep assertions around trade status, P&L, trailing stop, circuit breaker counter, and strategy stats.

- [ ] **Step 6: Verify scheduler and monitoring tests pass**

Run:

```bash
pytest tests/test_backtesting/test_scheduler_wiring.py tests/test_monitoring/test_monitor_trades.py -q
```

Expected: pass with no Telegram adapter.

- [ ] **Step 7: Commit**

```bash
git add src/main.py src/scheduler/jobs.py src/monitoring tests/test_backtesting/test_scheduler_wiring.py tests/test_monitoring tests/test_execution/test_scaffold.py
git commit -m "refactor(07): remove telegram runtime wiring"
```

---

### Task 4: Expand Web Monitoring Payload

**Files:**
- Modify: `src/monitoring/dashboard.py`
- Modify: `tests/test_monitoring/test_dashboard.py`

- [ ] **Step 1: Add dashboard API shape tests**

Add tests asserting `/api/dashboard` includes these web-only monitoring fields:

```python
def test_dashboard_api_web_monitoring_shape(client):
    response = client.get("/api/dashboard")
    data = response.json()

    assert "closed_trades" in data
    assert "candidate_signals" in data
    assert "operational_events" in data
    assert isinstance(data["closed_trades"], list)
    assert isinstance(data["candidate_signals"], list)
    assert isinstance(data["operational_events"], list)
```

- [ ] **Step 2: Run dashboard tests and verify the new test fails**

Run:

```bash
pytest tests/test_monitoring/test_dashboard.py::TestDashboardApi::test_dashboard_api_web_monitoring_shape -q
```

Expected before implementation: failure because the keys are missing.

- [ ] **Step 3: Implement new API fields**

In `src/monitoring/dashboard.py`, add:

```python
    # --- Recently Closed Trades ---
    closed_trades = []
    try:
        closed_stmt = (
            select(TradeORM, CandidateSignalORM.strategy)
            .join(ApprovedSignalORM, TradeORM.approved_signal_id == ApprovedSignalORM.id)
            .join(
                CandidateSignalORM,
                ApprovedSignalORM.candidate_signal_id == CandidateSignalORM.id,
            )
            .where(TradeORM.status.in_(["CLOSED", "STOPPED"]))
            .order_by(TradeORM.closed_at.desc())
            .limit(20)
        )
        closed_result = await db.execute(closed_stmt)
        for trade, strategy in closed_result.all():
            closed_trades.append({
                "id": str(trade.id),
                "direction": trade.direction,
                "strategy": strategy,
                "entry_price": float(trade.entry_price),
                "close_reason": trade.close_reason,
                "pnl_pct": float(trade.pnl_pct) if trade.pnl_pct is not None else None,
                "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
                "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
            })
    except Exception as exc:
        log.warning("dashboard.closed_trades_failed", error=str(exc))
    result["closed_trades"] = closed_trades

    # --- Candidate Signal Decisions ---
    candidate_signals = []
    try:
        cand_stmt = (
            select(CandidateSignalORM)
            .order_by(CandidateSignalORM.created_at.desc())
            .limit(50)
        )
        cand_result = await db.execute(cand_stmt)
        for cand in cand_result.scalars().all():
            candidate_signals.append({
                "id": str(cand.id),
                "strategy": cand.strategy,
                "direction": cand.direction,
                "entry_price": float(cand.entry_price),
                "confidence": float(cand.confidence),
                "timeframe": cand.timeframe,
                "status": cand.status,
                "created_at": cand.created_at.isoformat() if cand.created_at else None,
            })
    except Exception as exc:
        log.warning("dashboard.candidate_signals_failed", error=str(exc))
    result["candidate_signals"] = candidate_signals

    result["operational_events"] = [
        {
            "level": "warning",
            "event": "database_unreachable",
            "message": "PostgreSQL is unavailable; dashboard data is degraded.",
        }
    ] if not db_ok else []
    if not redis_ok:
        result["operational_events"].append({
            "level": "warning",
            "event": "redis_unreachable",
            "message": "Redis is unavailable; circuit breaker state is degraded.",
        })
```

- [ ] **Step 4: Verify dashboard API tests pass**

Run:

```bash
pytest tests/test_monitoring/test_dashboard.py -q
```

Expected: dashboard tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/monitoring/dashboard.py tests/test_monitoring/test_dashboard.py
git commit -m "feat(07): expose web monitoring data"
```

---

### Task 5: Update Dashboard UI For Web-Only Monitoring

**Files:**
- Modify: `src/templates/dashboard.html`
- Modify: `tests/test_monitoring/test_dashboard.py`

- [ ] **Step 1: Add HTML tests for new sections and no Telegram wording**

Add tests:

```python
def test_dashboard_page_has_web_monitoring_sections(client):
    response = client.get("/dashboard")
    html = response.text

    assert "Closed Trades" in html
    assert "Candidate Decisions" in html
    assert "Operational Events" in html


def test_dashboard_page_does_not_mention_telegram(client):
    response = client.get("/dashboard")

    assert "Telegram" not in response.text
    assert "telegram" not in response.text
```

- [ ] **Step 2: Run the new HTML tests and verify they fail before template changes**

Run:

```bash
pytest tests/test_monitoring/test_dashboard.py::TestDashboardPage::test_dashboard_page_has_web_monitoring_sections tests/test_monitoring/test_dashboard.py::TestDashboardPage::test_dashboard_page_does_not_mention_telegram -q
```

Expected before implementation: at least the new section test fails.

- [ ] **Step 3: Add sections with DOM-safe rendering**

In `src/templates/dashboard.html`, add panels for:

- `Closed Trades`
- `Candidate Decisions`
- `Operational Events`

Render rows using `document.createElement` and `textContent`, following the existing safe rendering pattern. Do not introduce `container.innerHTML = html` for API-derived row content.

Add empty states:

- `No closed trades yet`
- `No candidate decisions recorded yet`
- `No operational events`

- [ ] **Step 4: Verify dashboard HTML tests pass**

Run:

```bash
pytest tests/test_monitoring/test_dashboard.py -q
```

Expected: all dashboard HTML tests pass, including existing `innerHTML` safety tests.

- [ ] **Step 5: Commit**

```bash
git add src/templates/dashboard.html tests/test_monitoring/test_dashboard.py
git commit -m "feat(07): show autonomous bot monitoring sections"
```

---

### Task 6: Update Phase 7 Verification And UAT Docs

**Files:**
- Modify: `.planning/phases/07-signal-mode-monitoring/07-VERIFICATION.md`
- Modify: `.planning/phases/07-signal-mode-monitoring/07-HUMAN-UAT.md`
- Modify: `.planning/phases/07-signal-mode-monitoring/07-CONTEXT.md`

- [ ] **Step 1: Replace Telegram validation with web-only validation**

Update docs so human/UAT checks are:

- app startup with no Telegram env vars;
- `/dashboard` renders;
- `/api/dashboard` returns web monitoring data;
- `/health` returns healthy/degraded based on DB/Redis;
- no Telegram secret or chat is required.

Remove checks for:

- Telegram signal message format;
- trade lifecycle Telegram notifications;
- circuit breaker Telegram alert;
- daily summary Telegram message.

- [ ] **Step 2: Verify docs contain no live Telegram requirements**

Run:

```bash
rg -n "TELEGRAM|Telegram|telegram|chat" .planning/phases/07-signal-mode-monitoring/07-VERIFICATION.md .planning/phases/07-signal-mode-monitoring/07-HUMAN-UAT.md
```

Expected: no matches except historical notes explicitly marked as removed, if any.

- [ ] **Step 3: Commit**

```bash
git add .planning/phases/07-signal-mode-monitoring
git commit -m "docs(07): update UAT for web-only monitoring"
```

---

### Task 7: Final Verification

**Files:**
- All modified files.

- [ ] **Step 1: Confirm no Telegram imports remain**

Run:

```bash
rg -n "from telegram|import telegram|python-telegram-bot|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID" .
```

Expected: no matches.

- [ ] **Step 2: Run focused Phase 7 tests**

Run:

```bash
pytest tests/test_execution tests/test_monitoring tests/test_backtesting/test_scheduler_wiring.py tests/test_config -q
```

Expected: all pass.

- [ ] **Step 3: Run full suite**

Run:

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run local UAT without lifespan external dependencies**

Run the current local UAT path used previously against `127.0.0.1:8010` with lifespan off and verify:

- `/dashboard` returns `200 text/html`;
- `/api/dashboard` returns `200` with web-only monitoring keys;
- `/health` returns `200`;
- startup and endpoint checks do not require Telegram env vars.

- [ ] **Step 5: Final commit if verification docs were updated with UAT evidence**

```bash
git add .planning/phases/07-signal-mode-monitoring
git commit -m "test(07): record web-only monitoring UAT"
```

---

## Self-Review

- Spec coverage: `07-REALIGNMENT.md` requires removal of Telegram runtime, settings, dependency, UAT, and normal decision notifications. Tasks 1-3 remove runtime/config/dependency/tests. Tasks 4-5 make the dashboard the monitoring surface. Task 6 updates verification/UAT. Task 7 verifies no Telegram imports/secrets remain.
- Placeholder scan: no `TBD`, `TODO`, or unspecified implementation steps remain.
- Type consistency: `ExecutionRouter.execute(signal: CandidateSignal, size_lots: Decimal) -> bool` remains compatible with `PipelineRunner`. Dashboard additions use existing ORM models and JSON-safe primitives.
