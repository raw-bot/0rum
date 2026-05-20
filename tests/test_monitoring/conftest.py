"""Shared fixtures for Phase 7 monitoring tests."""

import pytest


@pytest.fixture
async def fake_redis():
    """FakeAsyncRedis with decode_responses=True for BreakerManager tests."""
    from fakeredis import FakeAsyncRedis

    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture(autouse=True)
def _reset_alert_hooks():
    """Clear _alert_hooks before and after each test to prevent cross-test bleed.

    alert adapter registrations must not persist into subsequent test functions.
    """
    try:
        from src.risk.hooks import _alert_hooks
    except ImportError:
        yield
        return
    _alert_hooks.clear()
    yield
    _alert_hooks.clear()
