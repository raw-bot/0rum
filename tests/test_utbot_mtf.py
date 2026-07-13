import unittest

from orum.strategies.base import Side, StrategyContext
from orum.strategies.utbot_mtf import UtBotMtfEngine, utbot_signal_series


def candles(closes: list[float], step_ms: int) -> list[dict]:
    return [
        {
            "ts": 1_780_000_000_000 + i * step_ms,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1.0,
        }
        for i, close in enumerate(closes)
    ]


class UtBotMtfEngineTests(unittest.TestCase):
    def context(self, m15_closes: list[float], h1_closes: list[float]) -> StrategyContext:
        m15 = candles(m15_closes, 900_000)
        h1 = candles(h1_closes, 3_600_000)
        decision_ts = int(m15[-1]["ts"]) + 900_000
        for index, candle in enumerate(h1):
            candle["ts"] = decision_ts - (len(h1) - index) * 3_600_000
        return StrategyContext(
            candles=m15,
            symbol="BTC/USDT",
            timeframe="15m",
            candles_by_timeframe={"15m": m15, "1h": h1},
        )

    def test_utbot_series_detects_upward_cross_after_flat_market(self):
        m15 = candles([100.0] * 30 + [200.0], 900_000)
        buy, sell, _ = utbot_signal_series(m15, key_value=6.0, atr_period=10)
        self.assertTrue(buy[-1])
        self.assertFalse(sell[-1])

    def test_buy_opens_only_when_h1_close_is_above_ema200(self):
        engine = UtBotMtfEngine()
        engine.init({})
        allowed = self.context([100.0] * 30 + [200.0], [100.0] * 200 + [101.0])
        signal = engine.on_candle(allowed.candles[-1], allowed)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.LONG)

        blocked_engine = UtBotMtfEngine()
        blocked_engine.init({})
        blocked = self.context([100.0] * 30 + [200.0], [200.0] * 200 + [100.0])
        self.assertIsNone(blocked_engine.on_candle(blocked.candles[-1], blocked))

    def test_h1_filter_is_aligned_as_of_the_evaluated_m15_close(self):
        engine = UtBotMtfEngine()
        engine.init({})
        m15 = candles([100.0] * 30 + [200.0], 900_000)
        decision_ts = int(m15[-1]["ts"]) + 900_000
        h1 = candles([200.0] * 201, 3_600_000)
        for index, candle in enumerate(h1):
            candle["ts"] = decision_ts - (202 - index) * 3_600_000
        future_h1 = dict(h1[-1], ts=decision_ts, close=300.0, open=300.0,
                         high=300.0, low=300.0)
        context = StrategyContext(
            candles=m15, symbol="BTC/USDT", timeframe="15m",
            candles_by_timeframe={"15m": m15, "1h": [*h1, future_h1]},
        )

        self.assertIsNone(engine.on_candle(m15[-1], context))

    def test_sell_trigger_is_exit_and_does_not_require_h1_downtrend(self):
        engine = UtBotMtfEngine()
        engine.init({})
        context = self.context([200.0] * 30 + [100.0], [100.0] * 201)
        signal = engine.on_candle(context.candles[-1], context)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.EXIT)

    def test_same_closed_m15_candle_is_not_emitted_twice(self):
        engine = UtBotMtfEngine()
        engine.init({})
        context = self.context([100.0] * 30 + [200.0], [100.0] * 200 + [101.0])
        self.assertIsNotNone(engine.on_candle(context.candles[-1], context))
        self.assertIsNone(engine.on_candle(context.candles[-1], context))


if __name__ == "__main__":
    unittest.main()
