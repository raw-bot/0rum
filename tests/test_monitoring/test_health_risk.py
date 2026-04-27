"""Tests for Phase 6 risk wiring in src/monitoring/health.py.

Verifies that GET /health returns live circuit_breaker, open_positions, and daily_pnl_pct
values from the risk module, and degrades gracefully when the risk module is unavailable.

Calls health_check() directly (not via HTTP) because src.main requires apscheduler,
which is not installed in the test environment.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _ensure_scheduler_mock():
    """Inject a stub for src.scheduler.jobs so health.py can import without apscheduler."""
    if "src.scheduler.jobs" not in sys.modules:
        stub = ModuleType("src.scheduler.jobs")
        stub.get_last_candle_fetch = lambda: None
        sys.modules["src.scheduler"] = ModuleType("src.scheduler")
        sys.modules["src.scheduler.jobs"] = stub


_ensure_scheduler_mock()

from src.monitoring.health import health_check  # noqa: E402


def _mock_db():
    db = MagicMock()
    db.execute = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_health_circuit_breaker_true_when_tripped():
    """circuit_breaker=True is returned when BreakerManager.is_tripped() returns True."""
    with (
        patch("src.monitoring.health.aioredis") as mock_aioredis,
        patch("src.monitoring.health.BreakerManager") as mock_bm_cls,
        patch("src.monitoring.health.get_open_positions", new_callable=AsyncMock, return_value=2),
        patch("src.monitoring.health.get_daily_pnl_pct", new_callable=AsyncMock, return_value=0.01),
        patch("src.monitoring.health.get_settings") as mock_settings,
        patch("src.monitoring.health.get_last_candle_fetch", return_value=None),
    ):
        mock_aioredis.from_url.return_value = AsyncMock()
        mock_settings.return_value.redis_url = "redis://localhost"
        mock_settings.return_value.execution_mode.value = "signal"

        mock_bm = MagicMock()
        mock_bm.is_tripped = AsyncMock(return_value=True)
        mock_bm_cls.return_value = mock_bm

        result = await health_check(db=_mock_db())

    assert result["circuit_breaker"] is True
    assert result["open_positions"] == 2
    assert result["daily_pnl_pct"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_health_open_positions_and_pnl_from_db():
    """open_positions and daily_pnl_pct reflect live DB query results."""
    with (
        patch("src.monitoring.health.aioredis") as mock_aioredis,
        patch("src.monitoring.health.BreakerManager") as mock_bm_cls,
        patch("src.monitoring.health.get_open_positions", new_callable=AsyncMock, return_value=3),
        patch("src.monitoring.health.get_daily_pnl_pct", new_callable=AsyncMock, return_value=-0.015),
        patch("src.monitoring.health.get_settings") as mock_settings,
        patch("src.monitoring.health.get_last_candle_fetch", return_value=None),
    ):
        mock_aioredis.from_url.return_value = AsyncMock()
        mock_settings.return_value.redis_url = "redis://localhost"
        mock_settings.return_value.execution_mode.value = "signal"

        mock_bm = MagicMock()
        mock_bm.is_tripped = AsyncMock(return_value=False)
        mock_bm_cls.return_value = mock_bm

        result = await health_check(db=_mock_db())

    assert result["circuit_breaker"] is False
    assert result["open_positions"] == 3
    assert result["daily_pnl_pct"] == pytest.approx(-0.015)


@pytest.mark.asyncio
async def test_health_degrades_gracefully_when_risk_module_unavailable():
    """When the risk module raises, /health returns placeholders (no 500)."""
    with (
        patch("src.monitoring.health.aioredis") as mock_aioredis,
        patch("src.monitoring.health.BreakerManager") as mock_bm_cls,
        patch("src.monitoring.health.get_settings") as mock_settings,
        patch("src.monitoring.health.get_last_candle_fetch", return_value=None),
    ):
        mock_aioredis.from_url.return_value = AsyncMock()
        mock_settings.return_value.redis_url = "redis://localhost"
        mock_settings.return_value.execution_mode.value = "signal"

        mock_bm = MagicMock()
        mock_bm.is_tripped = AsyncMock(side_effect=ConnectionError("redis down"))
        mock_bm_cls.return_value = mock_bm

        result = await health_check(db=_mock_db())

    assert result["circuit_breaker"] is False
    assert result["open_positions"] == 0
    assert result["daily_pnl_pct"] == 0.0
