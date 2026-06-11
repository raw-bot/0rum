import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import yaml

from hermes_trading import dashboard


class DashboardStateTests(unittest.TestCase):
    def test_snapshot_summarizes_bot_state_and_guardrails(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "goal.yaml").write_text(
                yaml.safe_dump(
                    {
                        "asset": "BTC/USDT",
                        "target_return_30d": 0.07,
                        "soft_drawdown": 0.03,
                        "max_drawdown": 0.05,
                        "emergency_stop_drawdown": 0.06,
                        "min_sharpe": 1.3,
                        "reflection_every": 10,
                        "starting_balance_usd": 10000,
                    }
                )
            )
            (state / "strategy.yaml").write_text(
                yaml.safe_dump(
                    {
                        "version": "01",
                        "entry": {"threshold": 30},
                        "position_size_r": 0.5,
                        "stop_loss_pct": 2.0,
                        "take_profit_pct": 3.0,
                    }
                )
            )
            (state / "heartbeat.json").write_text(
                json.dumps(
                    {
                        "ts": "2026-06-01T07:25:09Z",
                        "rsi": 31.5,
                        "last_price": 102.0,
                        "price_source": "binance_public",
                        "decision_action": "manage_position",
                        "decision_reason": "Position remains open.",
                    }
                )
            )
            (state / "open_position.json").write_text(
                json.dumps(
                    {
                        "asset": "BTC/USDT",
                        "direction": "long",
                        "entry_price": 100.0,
                        "notional_usd": 2500.0,
                        "risk_usd": 50.0,
                        "qty_base": 25.0,
                        "opened_at": "2026-06-01T07:20:09+00:00",
                    }
                )
            )
            trades = [
                {"pnl_pct": 0.01, "ts": "t1", "asset": "BTC/USDT", "entry_price": 100.0, "exit_price": 101.0},
                {"pnl_pct": -0.02, "ts": "t2", "asset": "BTC/USDT", "entry_price": 101.0, "exit_price": 98.98},
                {"pnl_pct": 0.005, "ts": "t3", "asset": "BTC/USDT", "entry_price": 98.98, "exit_price": 99.47},
            ]
            (state / "trades.jsonl").write_text("\n".join(json.dumps(trade) for trade in trades) + "\n")
            (state / "hypotheses.jsonl").write_text(
                json.dumps({"changed": False, "score": 0.12, "reason": "hold", "ts": "h1"}) + "\n"
            )
            (state / "hermes_watcher.json").write_text(
                json.dumps(
                    {
                        "ts": datetime.now(UTC).isoformat(),
                        "status": "standby",
                        "mode": "local_hermes_watcher",
                        "reflection_every": 10,
                        "trades_seen": 3,
                        "trades_since_reflection": 3,
                        "detail": "Waiting for 7 more closed trades before Hermes reflection.",
                    }
                )
            )
            (state / "history").mkdir()

            with patch.object(dashboard, "STATE_DIR", state):
                snapshot = dashboard.build_snapshot()

        self.assertEqual(snapshot["asset"], "BTC/USDT")
        self.assertEqual(snapshot["trade_count"], 3)
        self.assertEqual(snapshot["reflection"]["remaining"], 7)
        self.assertEqual(snapshot["strategy"]["version"], "01")
        self.assertEqual(snapshot["last_price"], 102.0)
        self.assertEqual(snapshot["heartbeat"]["price_source"], "binance_public")
        self.assertTrue(any("action=manage_position" in item["detail"] for item in snapshot["activity"]))
        self.assertTrue(snapshot["open_position"]["active"])
        self.assertAlmostEqual(snapshot["open_position"]["unrealized_pnl_pct"], 0.02)
        self.assertAlmostEqual(snapshot["open_position"]["unrealized_pnl_usd"], 50.0)
        self.assertAlmostEqual(snapshot["open_position"]["stop_price"], 98.0)
        self.assertAlmostEqual(snapshot["open_position"]["take_profit_price"], 103.0)
        self.assertEqual(snapshot["latest_hypothesis"]["reason"], "hold")
        self.assertEqual(snapshot["decisions"][0]["decision"], "hold")
        self.assertEqual(snapshot["decisions"][0]["score"], 0.12)
        self.assertEqual(snapshot["engine"]["label"], "Hermes watcher connected")
        self.assertGreaterEqual(len(snapshot["activity"]), 1)
        self.assertEqual(len(snapshot["candles"]), 3)
        self.assertIn("close", snapshot["candles"][0])
        self.assertEqual(len(snapshot["equity_curve"]), 3)
        self.assertIn("equity", snapshot["equity_curve"][0])
        self.assertAlmostEqual(snapshot["portfolio"]["starting_balance_usd"], 10000)
        self.assertAlmostEqual(snapshot["portfolio"]["balance_usd"], 9947.49, places=2)
        self.assertAlmostEqual(snapshot["portfolio"]["pnl_usd"], -52.51, places=2)
        self.assertIn(snapshot["guardrail"]["status"], {"normal", "caution", "review", "kill"})
