import unittest

from orum.strategies import load_engine
from orum.strategies.base import Side, StrategyContext
from orum.strategies.donchian import DonchianEngine


def _candles(closes: list[float]) -> list[dict]:
    """Minimal closed-candle dicts; Donchian only reads `close`."""
    return [
        {"ts": i, "open": c, "high": c, "low": c, "close": c, "volume": 0.0}
        for i, c in enumerate(closes)
    ]


def _ctx(closes: list[float]) -> tuple[dict, StrategyContext]:
    candles = _candles(closes)
    return candles[-1], StrategyContext(candles=candles, symbol="ETH/USDT", timeframe="1d")


class DonchianEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = DonchianEngine()
        self.engine.init({})  # 20/10 defaults

    def test_breakout_above_prior_high_is_long(self):
        closes = [100.0] * 20 + [130.0]  # last close tops the prior 20-bar high (100)
        signal = self.engine.on_candle(*_ctx(closes))
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.LONG)

    def test_break_below_prior_low_is_exit(self):
        closes = [100.0] * 20 + [80.0]  # last close breaks the prior 10-bar low (100)
        signal = self.engine.on_candle(*_ctx(closes))
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.EXIT)

    def test_inside_channel_is_no_signal(self):
        # A rising ramp: the last close (119) sits below the prior 20-bar high
        # (120 two bars back) and above the prior 10-bar low -> hold.
        closes = list(range(100, 121)) + [119.0]
        signal = self.engine.on_candle(*_ctx(closes))
        self.assertIsNone(signal)

    def test_insufficient_history_is_no_signal(self):
        closes = [100.0] * 5  # fewer than entry_n + 1 candles
        self.assertIsNone(self.engine.on_candle(*_ctx(closes)))

    def test_registry_loads_donchian_with_params(self):
        goal = {"strategy_engine": {"name": "donchian", "params": {"entry_n": 5, "exit_n": 3}}}
        engine = load_engine(goal)
        self.assertIsInstance(engine, DonchianEngine)
        self.assertEqual(engine._entry_n, 5)
        self.assertEqual(engine._exit_n, 3)
        # With entry_n=5, a breakout needs only 6 candles.
        closes = [100.0] * 5 + [130.0]
        signal = engine.on_candle(*_ctx(closes))
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.LONG)

    def test_bad_params_fall_back_to_defaults(self):
        self.engine.init({"entry_n": 0, "exit_n": "nope"})
        self.assertEqual(self.engine._entry_n, 20)
        self.assertEqual(self.engine._exit_n, 10)


if __name__ == "__main__":
    unittest.main()
