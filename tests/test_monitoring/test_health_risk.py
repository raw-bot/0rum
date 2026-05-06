"""Tests for Phase 6 risk wiring in src/monitoring/health.py.

Verifies that GET /health returns live circuit_breaker, open_positions,
daily_pnl_pct, and signals_today values, and degrades gracefully when
the risk module is unavailable.
"""

import sys
import importlib
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _ensure_scheduler_mock():
    """Prefer the real scheduler module; stub only when optional imports are missing."""
    try:
        jobs_mod = importlib.import_module("src.scheduler.jobs")
        if hasattr(jobs_mod, "get_last_candle_fetch"):
            return
    except ModuleNotFoundError:
        pass

    if "src.scheduler.jobs" not in sys.modules:
        stub = ModuleType("src.scheduler.jobs")
        stub.get_last_candle_fetch = lambda: None
        sys.modules["src.scheduler"] = ModuleType("src.scheduler")
        sys.modules["src.scheduler.jobs"] = stub


_ensure_scheduler_mock()

from src.monitoring.health import health_check  # noqa: E402


def _mock_db():
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result_with_scalar(0))
    return db


def _result_with_scalar(value):
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


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


@pytest.mark.asyncio
async def test_health_signals_today_counts_sent_signals():
    """signals_today reflects today's SENT approved signals instead of a hardcoded zero."""
    with (
        patch("src.monitoring.health.aioredis") as mock_aioredis,
        patch("src.monitoring.health.BreakerManager") as mock_bm_cls,
        patch("src.monitoring.health.get_open_positions", new_callable=AsyncMock, return_value=0),
        patch("src.monitoring.health.get_daily_pnl_pct", new_callable=AsyncMock, return_value=0.0),
        patch("src.monitoring.health.get_settings") as mock_settings,
        patch("src.monitoring.health.get_last_candle_fetch", return_value=None),
    ):
        mock_aioredis.from_url.return_value = AsyncMock()
        mock_settings.return_value.redis_url = "redis://localhost"
        mock_settings.return_value.execution_mode.value = "signal"

        mock_bm = MagicMock()
        mock_bm.is_tripped = AsyncMock(return_value=False)
        mock_bm_cls.return_value = mock_bm

        db = _mock_db()
        db.execute = AsyncMock(side_effect=[
            _result_with_scalar(1),  # postgres check
            _result_with_scalar(4),  # strategies_active
            _result_with_scalar(3),  # signals_today
        ])

        result = await health_check(db=db)

    assert result["signals_today"] == 3
