"""Test helpers for tests/test_risk/ — not imported from outside this directory.

_mock_session builds a MagicMock AsyncSession that returns a configured scalar_one() result.
Defined here (not in conftest.py) so test files can import it directly without requiring a
top-level tests/__init__.py (which does not exist in this project).
"""

from unittest.mock import AsyncMock, MagicMock


def _mock_session(scalar_value):
    """Return a MagicMock AsyncSession whose execute() returns scalar_value via scalar_one().

    Matches the existing project idiom in tests/test_pipeline/test_quota.py.
    Gate tests in test_gates.py mock the session directly because gates accept an
    explicit session argument (per CONTEXT D-01 / Pitfall 4).
    """
    session = MagicMock()
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=scalar_value)
    session.execute = AsyncMock(return_value=result)
    return session
