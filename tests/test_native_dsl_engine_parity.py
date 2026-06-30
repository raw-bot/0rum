"""Pins NativeDslEngine.on_candle() as equivalent to the entry_signal_fired /
exit_signal_fired call sites loop.py uses today, on every market shape those
have a test for (see test_loop_dsl_shadow.py). The engine is not wired into
loop.py yet; this is what makes a future cutover safe."""

import unittest

from orum.dsl.migrate import migrate_strategy_file
from orum.loop import entry_signal_fired, exit_signal_fired, market_candles
from orum.strategies.base import Side, StrategyContext
from orum.strategies.native_dsl import NativeDslEngine

LEGACY_STRATEGY = {
    "version": "03",
    "entry": {"indicator": "rsi", "threshold": 25.0, "direction": "long"},
    "stop_loss_pct": 2.0,
    "take_profit_pct": 3.0,
    "max_hold_candles": 30,
    "exit_rsi_threshold": 60.0,
    "position_size_r": 0.5,
}
STRATEGY = migrate_strategy_file(LEGACY_STRATEGY)


def _market(closes, source="binance_public"):
    return {"closes": closes, "last_candle_ts": 60_000 * len(closes), "source": source}


def _engine() -> NativeDslEngine:
    engine = NativeDslEngine()
    engine.init(STRATEGY)
    return engine


class NativeDslEngineParityTests(unittest.TestCase):
    def test_deep_oversold_matches_entry_signal_fired(self):
        closes = [100.0 - index * 0.5 for index in range(20)]
        market = _market(closes)
        candles = market_candles(market)
        legacy = entry_signal_fired(STRATEGY, candles, market)

        signal = _engine().on_candle(candles[-1], _context(candles))

        self.assertTrue(legacy["triggered"])
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.LONG)

    def test_strong_rally_matches_exit_signal_fired_not_entry(self):
        closes = [100.0 + index * 0.5 for index in range(20)]
        market = _market(closes)
        candles = market_candles(market)
        self.assertFalse(entry_signal_fired(STRATEGY, candles, market)["triggered"])
        self.assertTrue(exit_signal_fired(STRATEGY, candles)["triggered"])

        signal = _engine().on_candle(candles[-1], _context(candles))

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.EXIT)

    def test_choppy_market_produces_no_signal_like_both_evals_untriggered(self):
        # A flat series (constant close) drives this RSI implementation to 0.0
        # (no gains, no losses), which trips the entry threshold -- not the
        # "no signal" case this test wants. A small back-and-forth chop keeps
        # RSI mid-range so neither entry (<=25) nor exit (>=60) condition is
        # met, matching entry_signal_fired/exit_signal_fired below.
        closes = [100.0 + (0.1 if index % 2 == 0 else -0.1) for index in range(20)]
        market = _market(closes)
        candles = market_candles(market)
        self.assertFalse(entry_signal_fired(STRATEGY, candles, market)["triggered"])
        self.assertFalse(exit_signal_fired(STRATEGY, candles)["triggered"])

        self.assertIsNone(_engine().on_candle(candles[-1], _context(candles)))

    def test_insufficient_candles_produce_no_signal_not_a_crash(self):
        market = _market([100.0, 99.0, 98.0])
        candles = market_candles(market)
        self.assertTrue(entry_signal_fired(STRATEGY, candles, market)["errors"])

        self.assertIsNone(_engine().on_candle(candles[-1], _context(candles)))

    def test_short_direction_strategy_never_enters(self):
        short_strategy = dict(STRATEGY, direction="short")
        closes = [100.0 - index * 0.5 for index in range(20)]
        candles = market_candles(_market(closes))
        engine = NativeDslEngine()
        engine.init(short_strategy)

        self.assertIsNone(engine.on_candle(candles[-1], _context(candles)))

    def test_short_direction_strategy_still_exits_like_exit_signal_fired(self):
        # exit_signal_fired never checks direction; the engine must not
        # either, or a non-long position would be silently stranded.
        short_strategy = dict(STRATEGY, direction="short")
        closes = [100.0 + index * 0.5 for index in range(20)]
        candles = market_candles(_market(closes))
        self.assertTrue(exit_signal_fired(short_strategy, candles)["triggered"])
        engine = NativeDslEngine()
        engine.init(short_strategy)

        signal = engine.on_candle(candles[-1], _context(candles))

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.EXIT)


def _context(candles):
    return StrategyContext(candles=candles, symbol="BTC/USDT", timeframe="1m")


if __name__ == "__main__":
    unittest.main()
