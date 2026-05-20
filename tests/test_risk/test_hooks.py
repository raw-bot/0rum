"""Tests for src/risk/hooks.py — register/publish + failure isolation.

Hook list reset is handled by the autouse _reset_alert_hooks fixture in
conftest.py, which clears _alert_hooks before and after each test so
registrations from one test never leak into the next.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

from src.risk.events import CircuitBreakerAlert
from src.risk.hooks import _publish_alert, register_alert_hook


def _make_alert() -> CircuitBreakerAlert:
    """Return a realistic CircuitBreakerAlert for use in tests."""
    return CircuitBreakerAlert(
        tripped_at=datetime.now(timezone.utc),
        consecutive_stops=8,
        cooldown_until=datetime.now(timezone.utc) + timedelta(hours=24),
        last_stop_strategy="liquidity_sweep",
        last_stop_trade_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_register_then_publish_invokes_hook():
    """Registering a hook then publishing an alert awaits the hook exactly once with that alert."""
    hook = AsyncMock()
    register_alert_hook(hook)
    alert = _make_alert()

    await _publish_alert(alert)

    hook.assert_awaited_once_with(alert)


@pytest.mark.asyncio
async def test_hook_failure_does_not_propagate():
    """A failing hook must not raise and must not prevent subsequent hooks from receiving the alert."""
    failing = AsyncMock(side_effect=RuntimeError("notification adapter down"))
    working = AsyncMock()
    register_alert_hook(failing)
    register_alert_hook(working)
    alert = _make_alert()

    # MUST NOT raise even though the first hook fails
    await _publish_alert(alert)

    # The working hook still receives the alert
    working.assert_awaited_once_with(alert)
    # The failing hook was called (it raised, but it was still invoked)
    assert failing.await_count == 1
