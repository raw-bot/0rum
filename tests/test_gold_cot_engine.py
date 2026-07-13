import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from orum.strategies import load_engine
from orum.strategies.base import Side, StrategyContext
from orum.strategies.donchian import DonchianEngine
from orum.strategies.gold_cot import GoldCotEngine


def _candle() -> tuple[dict, StrategyContext]:
    candles = [{"ts": i, "open": 4000.0, "high": 4000.0, "low": 4000.0, "close": 4000.0, "volume": 0.0} for i in range(3)]
    return candles[-1], StrategyContext(candles=candles, symbol="PAXG/USDT", timeframe="1d")


def _fresh_gate(gate_on: bool, cot_index: float | None) -> dict:
    return {
        "report_date": "2026-06-30",
        "usable_from": "2026-07-03",
        "cot_index": cot_index,
        "gate_on": gate_on,
        "threshold": 20.0,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class GoldCotEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.gate_path = Path(self._dir.name) / "cot_gate.json"
        self.engine = GoldCotEngine()
        self.engine.init({"gate_path": str(self.gate_path)})

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _write(self, obj) -> None:
        self.gate_path.write_text(json.dumps(obj))

    def test_gate_on_is_long(self):
        self._write(_fresh_gate(gate_on=True, cot_index=12.0))
        signal = self.engine.on_candle(*_candle())
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.LONG)

    def test_gate_off_is_exit(self):
        self._write(_fresh_gate(gate_on=False, cot_index=53.9))
        signal = self.engine.on_candle(*_candle())
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.EXIT)

    def test_missing_cache_is_no_trade(self):
        self.assertIsNone(self.engine.on_candle(*_candle()))

    def test_invalid_cache_is_no_trade(self):
        self.gate_path.write_text("{broken")
        self.assertIsNone(self.engine.on_candle(*_candle()))

    def test_registry_loads_gold_cot(self):
        engine = load_engine({"strategy_engine": {"name": "gold_cot", "params": {"gate_path": str(self.gate_path)}}})
        self.assertIsInstance(engine, GoldCotEngine)
        self._write(_fresh_gate(gate_on=True, cot_index=5.0))
        self.assertEqual(engine.on_candle(*_candle()).side, Side.LONG)


class StrategyIsolationTests(unittest.TestCase):
    """The whole point of the cache design: a COT problem can only ever stop
    gold; it must never touch or break BTC/ETH."""

    def test_donchian_ignores_cot_entirely(self):
        # No cot_gate.json exists anywhere; Donchian must still fire on a breakout.
        engine = DonchianEngine()
        engine.init({})
        closes = [100.0] * 20 + [130.0]
        candles = [{"ts": i, "open": c, "high": c, "low": c, "close": c, "volume": 0.0} for i, c in enumerate(closes)]
        signal = engine.on_candle(candles[-1], StrategyContext(candles=candles, symbol="ETH/USDT", timeframe="1d"))
        self.assertEqual(signal.side, Side.LONG)

    def test_gold_no_trade_does_not_raise(self):
        # A missing gate is a clean no-trade, never an exception that could bubble
        # up and stall the shared loop for the other strategies.
        engine = GoldCotEngine()
        engine.init({"gate_path": "/nonexistent/cot_gate.json"})
        self.assertIsNone(engine.on_candle(*_candle()))


if __name__ == "__main__":
    unittest.main()
