"""Security guarantees of orum-mcp: no secrets out, no trading tools, no
network, no Binance call, validated parameters."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orum import dashboard
from orum.paths import PROJECT_ROOT
from orum_mcp import server, tools
from orum_mcp.adapters import backtest_runner
from orum_mcp.redact import bounded_json, redact
try:  # package import (python -m unittest tests.…) or top-level (discover -s tests)
    from tests.test_mcp_tools import write_state_fixtures
except ImportError:
    from test_mcp_tools import write_state_fixtures

FORBIDDEN_PREFIXES = ("place_", "open_", "close_", "modify_", "change_", "enable_", "set_", "create_", "delete_")
NETWORK_TOKENS = ("import httpx", "import requests", "import socket", "import urllib",
                  "import ccxt", "from httpx", "from urllib", "api.binance.com")


def _call_via_server(name, arguments=None):
    response = server.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })
    return response["result"]


class NoSecretsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / "state"
        write_state_fixtures(self.state)
        (self.state / "llm_reference_account.json").write_text(json.dumps({
            "balance_usd": 1000.0,
            "openrouter_api_key": "sk-SUPER-SECRET-123",
            "nested": {"exchange_api_secret": "hunter2", "bearer_token": "tok-999"},
        }))
        self._patch = patch.object(dashboard, "STATE_DIR", self.state)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_secret_fields_are_masked_in_server_responses(self):
        result = _call_via_server("get_account_state")
        text = result["content"][0]["text"]
        self.assertFalse(result["isError"])
        for secret in ("sk-SUPER-SECRET-123", "hunter2", "tok-999"):
            self.assertNotIn(secret, text)
        self.assertIn("***redacted***", text)
        self.assertIn('"balance_usd": 1000.0', text)  # non-secret data survives

    def test_no_tool_response_contains_secrets(self):
        args_by_tool = {
            "run_existing_backtest": {"days": 1},
            "compare_backtest_runs": {"version_a": "current", "version_b": "v0001", "days": 1},
        }
        for tool in tools.TOOLS:
            with self.subTest(tool=tool["name"]):
                result = _call_via_server(tool["name"], args_by_tool.get(tool["name"]))
                self.assertNotIn("sk-SUPER-SECRET-123", result["content"][0]["text"])

    def test_redact_masks_env_style_log_lines(self):
        self.assertEqual(redact("OPENROUTER_API_KEY=sk-live-42"),
                         "OPENROUTER_API_KEY=***redacted***")


class NoTradingToolsTests(unittest.TestCase):
    def test_forbidden_tool_names_do_not_exist(self):
        names = {tool["name"] for tool in tools.TOOLS}
        self.assertFalse(tools.FORBIDDEN_TOOL_NAMES & names)
        for name in names:
            self.assertFalse(name.startswith(FORBIDDEN_PREFIXES),
                             f"{name} looks like a mutating tool")

    def test_every_tool_is_a_get_explain_run_or_compare(self):
        for tool in tools.TOOLS:
            self.assertTrue(
                tool["name"].startswith(("get_", "explain_", "run_existing_", "compare_")),
                tool["name"],
            )


class NoNetworkTests(unittest.TestCase):
    def test_orum_mcp_sources_import_no_network_module(self):
        for path in sorted((PROJECT_ROOT / "orum_mcp").rglob("*.py")):
            source = path.read_text()
            for token in NETWORK_TOKENS:
                self.assertNotIn(token, source, f"{path.name} references {token!r}")

    def test_backtest_never_fetches_binance(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            write_state_fixtures(state)
            with patch.object(dashboard, "STATE_DIR", state), \
                 patch("orum.dsl.backtest._fetch_history",
                       side_effect=AssertionError("network call attempted")):
                run = backtest_runner.run_backtest(days=1)
                self.assertTrue(run["ok"])
                # and with a missing cache it fails structurally instead of fetching
                (state / "candle_history.json").unlink()
                run = backtest_runner.run_backtest(days=1)
                self.assertFalse(run["ok"])


class ParameterValidationTests(unittest.TestCase):
    def test_unknown_tool_rejected(self):
        with self.assertRaises(tools.ToolError):
            tools.call_tool("place_order", {})

    def test_invalid_arguments_rejected(self):
        cases = [
            ("get_last_decisions", {"limit": 0}),
            ("get_last_decisions", {"limit": 101}),
            ("get_last_decisions", {"limit": "ten"}),
            ("get_recent_trades", {"source": "live"}),
            ("get_worker_status", {"unexpected": True}),
            ("run_existing_backtest", {"version": "../../etc/passwd"}),
            ("run_existing_backtest", {"version": "v1"}),
            ("run_existing_backtest", {"days": 400}),
            ("compare_backtest_runs", {"version_a": "current"}),
        ]
        for name, arguments in cases:
            with self.subTest(tool=name, arguments=arguments):
                with self.assertRaises(tools.ToolError):
                    tools.call_tool(name, arguments)

    def test_server_reports_validation_failure_as_structured_error(self):
        result = _call_via_server("run_existing_backtest", {"version": "v1; rm -rf /"})
        self.assertTrue(result["isError"])
        self.assertIn("invalid", result["content"][0]["text"])


class ResponseBoundTests(unittest.TestCase):
    def test_oversized_payload_is_replaced_by_structured_error(self):
        text = bounded_json({"blob": "x" * 500_000})
        payload = json.loads(text)
        self.assertEqual(payload["error"], "response_too_large")


if __name__ == "__main__":
    unittest.main()
