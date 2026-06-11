import asyncio
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

from hermes_trading import events, loop

GOAL = {
    "asset": "BTC/USDT",
    "starting_balance_usd": 10000.0,
    "max_drawdown": 0.05,
    "emergency_stop_drawdown": 0.06,
}

STRATEGY = {
    "version": "01",
    "entry": {"indicator": "rsi", "threshold": 30, "direction": "long"},
    "stop_loss_pct": 2.0,
    "take_profit_pct": 3.0,
    "max_hold_candles": 30,
    "exit_rsi_threshold": 55,
    "position_size_r": 0.5,
    "fee_rate": 0.0004,
}

SIDE_PAYLOAD = {"schema_version": 1, "source": "test"}


def _price_payload(closes: list[float], candle_ts: int) -> dict:
    return {
        "schema_version": 1,
        "source": "binance_public",
        "asset": "BTC/USDT",
        "last": closes[-1],
        "last_candle_ts": candle_ts,
        "closes": closes,
    }


class RunLoopIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._stack = ExitStack()
        tmp = self._stack.enter_context(tempfile.TemporaryDirectory())
        self.state = Path(tmp)
        (self.state / "strategy.yaml").write_text(yaml.safe_dump(STRATEGY, sort_keys=False))

        self.price_fetch = AsyncMock()
        for target in ("onchain", "news", "macro"):
            self._stack.enter_context(
                patch(f"hermes_trading.adapters.{target}.fetch", new=AsyncMock(return_value=dict(SIDE_PAYLOAD)))
            )
        self._stack.enter_context(patch("hermes_trading.adapters.price.fetch", new=self.price_fetch))
        for name, value in {
            "STATE_DIR": self.state,
            "STRATEGY_PATH": self.state / "strategy.yaml",
            "TRADES_PATH": self.state / "trades.jsonl",
            "HEARTBEAT_PATH": self.state / "heartbeat.json",
            "POSITION_PATH": self.state / "open_position.json",
            "RESUME_ACK_PATH": self.state / "manual_resume.ok",
        }.items():
            self._stack.enter_context(patch.object(loop, name, value))
        self._stack.enter_context(patch.object(events, "EVENTS_PATH", self.state / "events.jsonl"))
        self._stack.enter_context(patch.dict("os.environ", {"HERMES_LOOP_INTERVAL_SECONDS": "0"}))

    def tearDown(self):
        self._stack.close()

    def _run_one_iteration(self, closes: list[float], candle_ts: int) -> None:
        self.price_fetch.return_value = _price_payload(closes, candle_ts)
        asyncio.run(loop.run_loop(dict(GOAL), iterations=1))

    def _trades(self) -> list[dict]:
        path = self.state / "trades.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_full_open_close_cycle_records_one_accounted_trade(self):
        oversold = [100.0 - index * 0.5 for index in range(20)]  # falling closes -> RSI 0

        self._run_one_iteration(oversold, candle_ts=60_000 * 60)

        position_path = self.state / "open_position.json"
        self.assertTrue(position_path.exists())
        position = json.loads(position_path.read_text())
        entry_price = position["entry_price"]
        heartbeat = json.loads((self.state / "heartbeat.json").read_text())
        self.assertEqual(heartbeat["decision_action"], "open_position")
        self.assertEqual(heartbeat["guardrail"], "normal")

        take_profit = [entry_price] * 19 + [entry_price * 1.04]  # +4% -> take profit
        self._run_one_iteration(take_profit, candle_ts=60_000 * 61)

        self.assertFalse(position_path.exists())
        trades = self._trades()
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade["exit_reason"], "take_profit")
        self.assertIn("account_return", trade)
        self.assertIn("balance_after_usd", trade)
        self.assertGreater(trade["net_pnl_usd"], 0)
        events_kinds = [json.loads(line)["kind"] for line in (self.state / "events.jsonl").read_text().splitlines()]
        self.assertIn("position_opened", events_kinds)
        self.assertIn("trade_closed", events_kinds)

    def test_crash_leftover_position_is_not_closed_twice(self):
        oversold = [100.0 - index * 0.5 for index in range(20)]
        self._run_one_iteration(oversold, candle_ts=60_000 * 60)
        position_path = self.state / "open_position.json"
        leftover = position_path.read_text()
        entry_price = json.loads(leftover)["entry_price"]

        take_profit = [entry_price] * 19 + [entry_price * 1.04]
        self._run_one_iteration(take_profit, candle_ts=60_000 * 61)
        self.assertEqual(len(self._trades()), 1)

        # Simulate the crash window: trade recorded but position not unlinked.
        position_path.write_text(leftover)
        self._run_one_iteration(take_profit, candle_ts=60_000 * 62)

        self.assertEqual(len(self._trades()), 1)
        self.assertFalse(position_path.exists())
        quarantine = self.state / "position_quarantine.jsonl"
        self.assertTrue(quarantine.exists())
        record = json.loads(quarantine.read_text().splitlines()[-1])
        self.assertIn("duplicate", record["quarantine_reason"])

    def test_offline_price_source_freezes_trading(self):
        offline_closes = [65000.0 - index * 12.5 for index in range(60)]
        self.price_fetch.return_value = {
            "schema_version": 1,
            "source": "offline_fallback",
            "asset": "BTC/USDT",
            "last": offline_closes[-1],
            "last_candle_ts": "offline-59",
            "closes": offline_closes,
        }

        asyncio.run(loop.run_loop(dict(GOAL), iterations=1))

        self.assertFalse((self.state / "open_position.json").exists())
        self.assertEqual(self._trades(), [])
        heartbeat = json.loads((self.state / "heartbeat.json").read_text())
        self.assertEqual(heartbeat["decision_action"], "offline_freeze")
        self.assertFalse(heartbeat["entry_fired"])


if __name__ == "__main__":
    unittest.main()
