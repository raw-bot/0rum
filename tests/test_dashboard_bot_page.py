from pathlib import Path
from unittest.mock import patch

from orum import dashboard


class _RouteCaptureHandler(dashboard.DashboardHandler):
    def __init__(self, path: str) -> None:
        self.path = path
        self.sent: tuple[int, bytes, str] | None = None

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.sent = status, body, content_type

    def _send_json(self, status: int, payload: dict) -> None:
        raise AssertionError(f"Unexpected JSON response: {status} {payload}")


def _request(path: str) -> tuple[int, bytes, str]:
    handler = _RouteCaptureHandler(path)
    handler.do_GET()
    assert handler.sent is not None
    return handler.sent


def test_bot_page_and_script_are_served_from_static_root(tmp_path):
    (tmp_path / "bot.html").write_text("<main>opérations du bot</main>", encoding="utf-8")
    (tmp_path / "bot.js").write_text("console.log('bot');", encoding="utf-8")

    with patch.object(dashboard, "STATIC_DIR", Path(tmp_path)):
        bot_status, bot_body, bot_type = _request("/bot")
        script_status, script_body, script_type = _request("/assets/bot.js")

    assert (bot_status, bot_body, bot_type) == (
        200,
        b"<main>op\xc3\xa9rations du bot</main>",
        "text/html; charset=utf-8",
    )
    assert (script_status, script_body, script_type) == (
        200,
        b"console.log('bot');",
        "application/javascript",
    )
