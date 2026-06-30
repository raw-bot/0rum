"""Proves the registry/interface is genuinely pluggable, using the cheapest
possible engine that has nothing to do with the DSL or AK MACD:
  - a strategy can be picked by config alone (load_engine swaps DummyEngine
    in for native_dsl/ak_macd with no core-code change);
  - a Signal a strategy produces is sufficient, as-is, to drive the existing
    execution seam (PaperExecutor.open) -- i.e. risk/execution genuinely
    doesn't care which engine produced the signal.
"""

import unittest

from orum.executor import PaperExecutor
from orum.strategies import load_engine
from orum.strategies.base import Side, StrategyContext
from orum.strategies.dummy import DummyEngine


def _context() -> StrategyContext:
    return StrategyContext(candles=[{"close": 100.0}], symbol="BTC/USDT", timeframe="1m")


class DummyEngineTests(unittest.TestCase):
    def test_defaults_to_flat_and_never_signals(self):
        engine = DummyEngine()
        engine.init({})
        self.assertIsNone(engine.on_candle({}, _context()))

    def test_configured_side_is_returned_every_call(self):
        engine = DummyEngine()
        engine.init({"side": "long"})
        signal = engine.on_candle({}, _context())
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.LONG)
        self.assertEqual(signal.symbol, "BTC/USDT")


class DummyEngineLoadableByConfigTests(unittest.TestCase):
    def test_loadable_by_name_through_the_registry(self):
        engine = load_engine({"strategy_engine": {"name": "dummy", "params": {"side": "short"}}})
        self.assertIsInstance(engine, DummyEngine)
        signal = engine.on_candle({}, _context())
        self.assertEqual(signal.side, Side.SHORT)

    def test_switching_strategy_engine_name_swaps_the_implementation(self):
        # The point of the registry: changing one config value changes which
        # engine runs, with no code touched on either side of the seam.
        from orum.strategies.ak_macd import AkMacdEngine
        from orum.strategies.native_dsl import NativeDslEngine

        for name, expected_type, params in (
            ("dummy", DummyEngine, {}),
            ("ak_macd", AkMacdEngine, {}),
            ("native_dsl", NativeDslEngine, {"version": "01", "entry": {}, "exit": {}}),
        ):
            engine = load_engine({"strategy_engine": {"name": name, "params": params}})
            self.assertIsInstance(engine, expected_type)


class SignalReachesExecutorUnchangedTests(unittest.TestCase):
    def test_a_dummy_signal_drives_paper_executor_open_without_modification(self):
        # The executor/risk seam (executor.py) takes asset/strategy/goal/
        # market/entry_summary -- exactly what a Signal already carries
        # (symbol, entry_reason) -- with no engine-specific branching.
        engine = DummyEngine()
        engine.init({"side": "long"})
        signal = engine.on_candle({}, _context())

        strategy = {
            "version": "01",
            "entry": {"threshold": 30, "direction": "long"},
            "stop_loss_pct": 2.0,
            "position_size_r": 0.5,
        }
        goal = {"starting_balance_usd": 10000}
        market = {"last_candle_ts": 123, "closes": [99.0, 100.0]}

        position = PaperExecutor().open(
            asset=signal.symbol,
            strategy=strategy,
            goal=goal,
            market=market,
            rsi=25.0,
            entry_summary=signal.entry_reason,
        )

        self.assertEqual(position["asset"], "BTC/USDT")
        self.assertIn("entry_price", position)


if __name__ == "__main__":
    unittest.main()
