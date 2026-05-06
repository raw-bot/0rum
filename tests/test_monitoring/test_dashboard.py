"""Tests for /api/dashboard and /dashboard endpoints.

Uses FastAPI TestClient with dependency overrides to avoid requiring a live
PostgreSQL or Redis instance. All DB and Redis interactions are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.monitoring.dashboard import dashboard_router


@pytest.fixture
def app():
    """Create a minimal FastAPI app with dashboard_router and DB dependency override."""
    app = FastAPI()
    app.include_router(dashboard_router)
    return app


@pytest.fixture
def mock_db():
    """Create a mock AsyncSession that returns empty/default values for all queries.

    Each call to mock_db.execute returns an AsyncMock that resolves to empty lists
    or default values depending on the query. Individual tests can override specific
    execute calls if needed.
    """
    db = AsyncMock(spec=AsyncSession)

    # Default execute response — scalar_one returns None, scalars().all() returns []
    default_result = MagicMock()
    default_result.scalar_one.return_value = 0
    default_result.scalar_one_or_none.return_value = None
    default_result.all.return_value = []
    default_result.scalars.return_value.all.return_value = []

    db.execute.return_value = default_result
    return db


@pytest.fixture
def client(app, mock_db):
    """Override get_db dependency with mock and return TestClient."""
    from src.database import get_db
    app.dependency_overrides[get_db] = lambda: mock_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


class TestDashboardApi:
    """Tests for the /api/dashboard JSON endpoint."""

    def test_dashboard_api_returns_200(self, client):
        """GET /api/dashboard returns 200 OK."""
        response = client.get("/api/dashboard")
        assert response.status_code == 200

    def test_dashboard_api_shape(self, client):
        """Response JSON contains all 7 required keys."""
        response = client.get("/api/dashboard")
        data = response.json()
        assert "health" in data
        assert "execution_mode" in data
        assert "circuit_breaker" in data
        assert "daily_pnl_pct" in data
        assert "open_trades" in data
        assert "latest_signals" in data
        assert "strategy_stats" in data

    def test_dashboard_api_health_has_required_fields(self, client):
        """health dict contains db, redis, strategies_active."""
        response = client.get("/api/dashboard")
        health = response.json()["health"]
        assert "db" in health
        assert "redis" in health
        assert "strategies_active" in health

    def test_dashboard_api_returns_correct_types(self, client):
        """Verify value types for top-level fields."""
        response = client.get("/api/dashboard")
        data = response.json()
        assert isinstance(data["health"], dict)
        assert isinstance(data["execution_mode"], str)
        assert isinstance(data["circuit_breaker"], dict)
        assert isinstance(data["daily_pnl_pct"], float | int)
        assert isinstance(data["open_trades"], list)
        assert isinstance(data["latest_signals"], list)
        assert isinstance(data["strategy_stats"], list)

    def test_dashboard_api_health_types(self, client):
        """Health sub-fields have correct types."""
        response = client.get("/api/dashboard")
        health = response.json()["health"]
        assert isinstance(health["db"], bool)
        assert isinstance(health["redis"], bool)
        assert isinstance(health["strategies_active"], int)

    def test_dashboard_api_circuit_breaker_types(self, client):
        """Circuit breaker sub-fields have correct types."""
        response = client.get("/api/dashboard")
        cb = response.json()["circuit_breaker"]
        assert isinstance(cb["tripped"], bool)
        assert isinstance(cb["consecutive_stops"], int)

    def test_dashboard_api_execution_mode_is_string(self, client):
        """Execution mode is a non-empty string."""
        response = client.get("/api/dashboard")
        mode = response.json()["execution_mode"]
        assert isinstance(mode, str)
        assert len(mode) > 0


class TestDashboardPage:
    """Tests for the /dashboard HTML page."""

    def test_dashboard_page_returns_html(self, client):
        """GET /dashboard returns 200 with text/html content type."""
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def test_dashboard_page_contains_title(self, client):
        """Response body contains '0rum Dashboard'."""
        response = client.get("/dashboard")
        assert "0rum Dashboard" in response.text

    def test_dashboard_page_no_cdn(self, client):
        """Response body does not contain CDN URLs."""
        response = client.get("/dashboard")
        body = response.text
        assert "cdn." not in body
        assert "googleapis" not in body
        assert "jsdelivr" not in body
        assert "unpkg" not in body
        assert "https://" not in body

    def test_dashboard_page_has_set_interval(self, client):
        """Dashboard page contains setInterval for auto-refresh."""
        response = client.get("/dashboard")
        assert "setInterval(fetchDashboard, 30000)" in response.text or \
               "setInterval(fetchDashboard, REFRESH_INTERVAL)" in response.text

    def test_dashboard_page_has_css_custom_properties(self, client):
        """Dashboard page declares all 9 CSS custom properties."""
        response = client.get("/dashboard")
        body = response.text
        assert "--bg:" in body.replace(" ", "")
        assert "--surface:" in body.replace(" ", "")
        assert "--accent:" in body.replace(" ", "")
        assert "--destructive:" in body.replace(" ", "")
        assert "--warning:" in body.replace(" ", "")
        assert "--muted:" in body.replace(" ", "")
        assert "--border:" in body.replace(" ", "")
        assert "--text:" in body.replace(" ", "")
        assert "--text-2:" in body.replace(" ", "")

    def test_dashboard_page_html_lang(self, client):
        """Dashboard has <html lang="en">."""
        response = client.get("/dashboard")
        assert '<html lang="en">' in response.text

    def test_dashboard_page_has_refresh_button(self, client):
        """Dashboard has a <button id="refresh-btn">."""
        response = client.get("/dashboard")
        assert 'id="refresh-btn"' in response.text

    def test_dashboard_page_has_empty_states(self, client):
        """Dashboard contains required empty state text."""
        response = client.get("/dashboard")
        assert "No open theoretical trades" in response.text
        assert "No strategy stats yet" in response.text
        assert "No signals recorded yet" in response.text

    def test_dashboard_page_has_data_section_status(self, client):
        """Status row section has data-section='status' attribute."""
        response = client.get("/dashboard")
        assert 'data-section="status"' in response.text

    def test_dashboard_page_has_exec_mode_badge(self, client):
        """Dashboard has id='exec-mode-badge' on execution mode element."""
        response = client.get("/dashboard")
        assert 'id="exec-mode-badge"' in response.text

    def test_dashboard_page_th_has_scope_col(self, client):
        """At least one <th> has scope='col'."""
        response = client.get("/dashboard")
        assert "th.scope = 'col'" in response.text

    def test_dashboard_page_does_not_render_api_rows_with_inner_html(self, client):
        """Dynamic API table rows must be rendered via DOM text nodes, not HTML strings."""
        response = client.get("/dashboard")
        body = response.text
        assert "container.innerHTML = html" not in body
        assert "textContent" in body
