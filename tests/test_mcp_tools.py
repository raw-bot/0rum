"""Every orum-mcp tool works on fixtures, degrades cleanly on missing data,
and never writes a byte into the state directory."""

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import yaml

from orum import dashboard
from orum_mcp import tools
from orum_mcp.adapters import backtest_runner, state_reader

GOAL = {
    "asset": "BTC/USDT",
    "starting_balance_usd": 10000.0,
    "soft_drawdown": 0.03,
    "max_drawdown": 0.05,
    "emergency_stop_drawdown": 0.06,
    "daily_loss_limit": 0.015,
}
STRATEGY = {
    "version": "04",
    "dsl_version": 1,
    "entry": {"logic": "AND", "conditions": [{"indicator": "close", "operator": ">", "value": 1.0}]},
    "exit": {"logic": "OR", "conditions": [{"indicator": "close", "operator": ">", "value": 1.0}]},
    "risk": {"stop_loss_pct": 2.0, "take_profit_pct": 3.0, "max_hold_candles": 30, "position_size_r": 0.5},
    "direction": "long",
}
HEARTBEAT = {
    "ts": "2026-07-15T10:00:00+00:00",
    "asset": "BTC/USDT",
    "decision_action": "wait",
    "decision_reason": "No long entry: rsi(14) <= 25 (lhs=38.15) -> not met.",
    "guardrail": "normal",
    "price_source": "binance_public",
    "dsl": {"entry_triggered": False, "exit_triggered": False, "errors": ["rsi: insufficient warm-up"]},
    "signal_id": "BTC/USDT|abc|123",
    "last_price": 62160.0,
}


def write_state_fixtures(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "goal.yaml").write_text(yaml.safe_dump(GOAL))
    (state / "strategy.yaml").write_text(yaml.safe_dump(STRATEGY))
    heartbeat = HEARTBEAT | {"ts": (datetime.now(UTC) - timedelta(seconds=60)).isoformat()}
    (state / "heartbeat.json").write_text(json.dumps(heartbeat))
    (state / "paper_positions.json").write_text(json.dumps({
        "balance_usd": 10050.0,
        "updated_at": "2026-07-15T10:00:00+00:00",
        "positions": {
            "btc_ak_macd_4h": {
                "symbol": "BTC/USDT", "side": "long", "qty": 0.01, "entry_px": 60000.0,
                "notional_usd": 600.0, "risk_pct": 0.02, "opened_ts": "2026-07-15T08:00:00+00:00",
                "exit_policy": "structural_bracket", "stop_loss_price": 58800.0,
                "take_profit_price": 61800.0, "monitor_timeframe": "15m",
            }
        },
    }))
    fills = [
        {"ts": "2026-07-14T10:00:00+00:00", "strategy_id": "eth_donchian", "symbol": "ETH/USDT",
         "action": "open", "side": "long", "price": 3000.0, "qty": 0.1, "entry_px": 3000.0},
        {"ts": "2026-07-14T18:00:00+00:00", "strategy_id": "eth_donchian", "symbol": "ETH/USDT",
         "action": "close", "side": "long", "price": 3060.0, "qty": 0.1, "entry_px": 3000.0,
         "realized_pnl_usd": 6.0, "reason": "take_profit", "r": 1.5},
    ]
    (state / "paper_fills.jsonl").write_text("".join(json.dumps(f) + "\n" for f in fills))
    fresh_ts = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    (state / "paper_equity.jsonl").write_text(
        json.dumps({"ts": fresh_ts, "equity_usd": 10056.0, "open_positions": 1}) + "\n"
    )
    events = [
        {"ts": "2026-07-14T12:00:00+00:00", "kind": "guardrail_halt", "detail": "drawdown breached"},
        {"ts": "2026-07-14T13:00:00+00:00", "kind": "position_quarantined", "detail": "stale after outage"},
        {"ts": "2026-07-15T09:00:00+00:00", "kind": "worker_boot", "detail": "booting"},
    ]
    (state / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (state / "hypotheses.jsonl").write_text(
        json.dumps({"ts": "2026-07-14T14:00:00+00:00", "outcome": "rejected",
                    "reason": "backtest regression: zero signals"}) + "\n"
    )
    (state / "llm_decisions.jsonl").write_text(
        json.dumps({"recorded_at": "2026-07-15T09:30:00+00:00", "decision_id": "decision-777",
                    "status": "rejected", "reason": "limit orders not supported"}) + "\n"
    )
    candles = [
        {"ts": 1_783_900_000_000 + i * 60_000, "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0 + (i % 7) * 0.1, "volume": 1.0}
        for i in range(1200)
    ]
    (state / "candle_history.json").write_text(json.dumps({"asset": "BTC/USDT", "candles": candles}))
    history = state / "history"
    history.mkdir(exist_ok=True)
    (history / "v0001.yaml").write_text(yaml.safe_dump(STRATEGY | {"version": "01"}))


def state_digest(state: Path) -> dict:
    return {
        str(p.relative_to(state)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(state.rglob("*")) if p.is_file()
    }


class McpToolsOnFixturesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / "state"
        write_state_fixtures(self.state)
        self._patch = patch.object(dashboard, "STATE_DIR", self.state)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def _call(self, name, arguments=None):
        return tools.call_tool(name, arguments or {})

    def test_every_tool_runs_and_state_dir_is_never_modified(self):
        before = state_digest(self.state)
        args_by_tool = {
            "run_existing_backtest": {"version": "current", "days": 1},
            "compare_backtest_runs": {"version_a": "current", "version_b": "v0001", "days": 1},
        }
        for tool in tools.TOOLS:
            with self.subTest(tool=tool["name"]):
                result = self._call(tool["name"], args_by_tool.get(tool["name"]))
                self.assertIsInstance(result, dict)
        self.assertEqual(before, state_digest(self.state), "a tool wrote into the state dir")

    def test_worker_status_reports_paper_engine_freshness(self):
        status = self._call("get_worker_status")
        self.assertTrue(status["paper_engine"]["running"])
        self.assertFalse(status["engine_process"]["running"])
        self.assertEqual(status["worker_heartbeat"]["guardrail"], "normal")

    def test_account_and_positions_come_from_paper_ledger(self):
        account = self._call("get_account_state")
        self.assertEqual(account["paper_account"]["balance_usd"], 10050.0)
        positions = self._call("get_open_positions")
        self.assertEqual(positions["open_count"], 1)
        self.assertEqual(positions["paper_positions"][0]["strategy_id"], "btc_ak_macd_4h")

    def test_pending_orders_exposes_brackets_and_says_unsupported(self):
        pending = self._call("get_pending_orders")
        self.assertFalse(pending["supported"])
        self.assertEqual(pending["pending_orders"], [])
        self.assertEqual(pending["position_brackets"][0]["stop_loss_price"], 58800.0)

    def test_current_signal_state_is_verbatim_heartbeat(self):
        signal = self._call("get_current_signal_state")
        self.assertTrue(signal["available"])
        self.assertEqual(signal["decision_action"], "wait")
        self.assertIn("rsi(14)", signal["decision_reason"])
        self.assertFalse(signal["stale"])
        self.assertIsNone(signal["stale_warning"])

    def test_stale_heartbeat_is_flagged(self):
        old = HEARTBEAT | {"ts": "2026-07-01T00:00:00+00:00"}
        (self.state / "heartbeat.json").write_text(json.dumps(old))
        signal = self._call("get_current_signal_state")
        self.assertTrue(signal["stale"])
        self.assertIn("LAST loop", signal["stale_warning"])
        status = self._call("get_worker_status")
        self.assertTrue(status["worker_heartbeat"]["stale"])

    def test_explain_rejected_signal_finds_recorded_rejections_only(self):
        found = self._call("explain_rejected_signal", {"query": "decision-777"})
        self.assertTrue(found["found"])
        self.assertEqual(found["matches"][0]["record"]["decision_id"], "decision-777")
        missing = self._call("explain_rejected_signal", {"query": "no-such-id"})
        self.assertFalse(missing["found"])
        self.assertEqual(missing["matches"], [])

    def test_explain_trade_returns_fills_and_trade(self):
        result = self._call("explain_trade", {"strategy_id": "eth_donchian"})
        self.assertTrue(result["found"])
        self.assertEqual(result["trade"]["exit_reason"], "take_profit")
        self.assertEqual(len(result["raw_fills"]), 2)
        self.assertFalse(self._call("explain_trade", {"strategy_id": "nope"})["found"])

    def test_recent_trades_and_metrics(self):
        trades = self._call("get_recent_trades", {"limit": 5})
        self.assertEqual(trades["trade_count_total"], 1)
        metrics = self._call("get_strategy_metrics")
        self.assertEqual(metrics["trade_count"], 1)
        self.assertEqual(metrics["per_strategy"]["eth_donchian"]["wins"], 1)
        self.assertEqual(metrics["strategy_version"], "04")

    def test_recent_errors_filters_incident_kinds(self):
        errors = self._call("get_recent_errors")
        kinds = {e["kind"] for e in errors["incident_events"]}
        self.assertEqual(kinds, {"guardrail_halt", "position_quarantined"})
        self.assertIn("rsi: insufficient warm-up", errors["heartbeat_dsl_errors"])

    def test_backtest_replays_cache_and_compare_diffs(self):
        run = self._call("run_existing_backtest", {"version": "current", "days": 1})
        self.assertTrue(run["ok"])
        self.assertGreater(run["entries_triggered"], 0)
        compare = self._call("compare_backtest_runs",
                             {"version_a": "current", "version_b": "v0001", "days": 1})
        self.assertTrue(compare["run_a"]["ok"] and compare["run_b"]["ok"])
        self.assertEqual(compare["comparison"]["trade_count"]["delta"], 0)

    def test_runtime_config_lists_history_versions(self):
        config = self._call("get_runtime_config")
        self.assertEqual(config["strategy_history_versions"], ["v0001.yaml"])
        self.assertEqual(config["goal"]["asset"], "BTC/USDT")


class McpToolsOnEmptyStateTests(unittest.TestCase):
    """Missing data must yield clean structured answers, never exceptions."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / "empty-state"
        self.state.mkdir()
        self._patch = patch.object(dashboard, "STATE_DIR", self.state)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_every_tool_handles_missing_data(self):
        args_by_tool = {
            "run_existing_backtest": {},
            "compare_backtest_runs": {"version_a": "current", "version_b": "v0001"},
        }
        for tool in tools.TOOLS:
            with self.subTest(tool=tool["name"]):
                result = tools.call_tool(tool["name"], args_by_tool.get(tool["name"]))
                self.assertIsInstance(result, dict)

    def test_backtest_reports_missing_strategy_then_missing_cache(self):
        run = backtest_runner.run_backtest()
        self.assertFalse(run["ok"])
        self.assertIn("not found", run["error"])
        (self.state / "strategy.yaml").write_text(yaml.safe_dump(STRATEGY))
        run = backtest_runner.run_backtest()
        self.assertFalse(run["ok"])
        self.assertIn("candle_history.json missing", run["error"])

    def test_signal_state_reports_unavailable(self):
        self.assertFalse(state_reader.current_signal_state()["available"])


if __name__ == "__main__":
    unittest.main()
