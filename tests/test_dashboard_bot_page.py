import json

from orum import dashboard


class _RouteCaptureHandler(dashboard.DashboardHandler):
    def __init__(self, path: str) -> None:
        self.path = path
        self.sent: tuple[int, bytes, str] | None = None

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.sent = status, body, content_type

    def _send_json(self, status: int, payload: dict) -> None:
        self.sent = status, json.dumps(payload, sort_keys=True).encode(), "application/json"


def _request(path: str) -> tuple[int, bytes, str]:
    handler = _RouteCaptureHandler(path)
    handler.do_GET()
    assert handler.sent is not None
    return handler.sent


def test_bot_page_and_script_are_served_from_real_static_root():
    bot_status, bot_body, bot_type = _request("/bot")
    script_status, script_body, script_type = _request("/assets/bot.js")

    assert bot_status == 200
    assert bot_type == "text/html; charset=utf-8"
    assert 'id="bot-unified"' in bot_body.decode()
    assert 'id="bot-runtime"' in bot_body.decode()
    assert 'id="bot-learning"' in bot_body.decode()
    assert "/assets/bot.js" in bot_body.decode()
    assert script_status == 200
    assert script_type == "application/javascript"
    assert 'fetch("/api/state"' in script_body.decode()
    assert "let pollInFlight = false;" in script_body.decode()
    assert "if (pollInFlight) return;" in script_body.decode()
    assert "finally {\n    pollInFlight = false;\n  }" in script_body.decode()


def test_opening_range_page_assets_and_dashboard_link_are_served():
    page_status, page_body, page_type = _request("/opening-range")
    css_status, _css_body, css_type = _request("/assets/opening-range.css")
    script_status, script_body, script_type = _request("/assets/opening-range.js")
    dashboard_status, dashboard_body, _dashboard_type = _request("/")

    page = page_body.decode()
    script = script_body.decode()
    assert page_status == 200
    assert page_type == "text/html; charset=utf-8"
    assert "NVDA" in page
    assert "LECTURE SEULE" in page
    assert "/assets/opening-range.css" in page
    assert "/assets/opening-range.js" in page
    assert css_status == 200
    assert css_type == "text/css; charset=utf-8"
    assert script_status == 200
    assert script_type == "application/javascript"
    assert 'fetch("/api/state"' in script
    assert 'fetch("/api/opening-range"' in script
    assert "/api/worker/" not in script
    assert "method:" not in script
    assert dashboard_status == 200
    assert 'href="/opening-range"' in dashboard_body.decode()


def test_opening_range_api_is_read_only_and_returns_provider_snapshot(monkeypatch):
    expected = {
        "status": "CONNECTED_PAPER",
        "connected": True,
        "symbol": "NVDA",
    }
    monkeypatch.setattr(dashboard, "opening_range_snapshot", lambda: expected)

    status, body, content_type = _request("/api/opening-range")

    assert status == 200
    assert content_type == "application/json"
    assert json.loads(body) == expected


def test_retired_legacy_worker_cannot_be_started(monkeypatch):
    monkeypatch.setattr(dashboard, "worker_running", lambda: False)

    result = dashboard.start_worker()

    assert result == {
        "ok": False,
        "running": False,
        "detail": "legacy engine retired; com.0rum.paper is authoritative",
    }


def test_unknown_asset_remains_a_json_404():
    status, body, content_type = _request("/assets/unknown.js")

    assert status == 404
    assert content_type == "application/json"
    assert json.loads(body) == {"error": "not found"}
