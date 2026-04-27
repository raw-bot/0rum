"""Shared fixtures for tests/test_risk/.

Per Pitfall 8 (06-RESEARCH §Common Pitfalls): every fakeredis fixture is
FUNCTION-SCOPED (the default). Do NOT use scope="session" or scope="module"
with FakeAsyncRedis — fakeredis-py issue #292 documents that session-scoped
instances bind to one event loop and break cross-test reuse with
pytest-asyncio's asyncio_mode = "auto".

The autouse _reset_alert_hooks fixture clears src.risk.hooks._alert_hooks
between tests so hook registrations from one test do not leak into the next.
The hooks module is imported lazily inside the fixture body so that test
collection still works for tests that run BEFORE plan 02 lands the module.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
async def fake_redis():
    """Function-scoped FakeAsyncRedis with decode_responses=True.

    decode_responses=True is REQUIRED so reads return str, not bytes — the
    breaker stores ISO-8601 timestamps (06-RESEARCH §breaker:432).
    """
    from fakeredis import FakeAsyncRedis

    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def breaker(fake_redis):
    """BreakerManager wired to the in-memory fakeredis instance."""
    from src.risk.breaker import BreakerManager  # lazy import — plan 06 lands this

    return BreakerManager(redis=fake_redis)


def _mock_session(scalar_value):
    """Build a MagicMock AsyncSession that returns scalar_value from scalar_one().

    Matches the existing project idiom in tests/test_pipeline/test_quota.py
    (which patches the helper function instead of the session). Gate tests in
    tests/test_risk/test_gates.py mock the session directly because gates take
    an explicit session argument (per CONTEXT D-01 / Pitfall 4).
    """
    session = MagicMock()
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=scalar_value)
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.fixture(autouse=True)
def _reset_alert_hooks():
    """Clear src.risk.hooks._alert_hooks between tests (per 06-PATTERNS hooks section).

    Module-global lists leak state across tests. Lazy import means tests that
    do not yet need the hooks module (Plans 01, 03, 04, 05 tests) still run.
    """
    try:
        from src.risk.hooks import _alert_hooks
    except ImportError:
        yield
        return
    _alert_hooks.clear()
    yield
    _alert_hooks.clear()
