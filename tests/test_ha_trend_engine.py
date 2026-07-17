import unittest

from orum.strategies import load_engine
from orum.strategies.base import Side, StrategyContext
from orum.strategies.ha_trend import HaTrendEngine, _ema, _ha_green_flags, _rsi_last


def _candle(o: float, h: float, l: float, c: float, ts: int) -> dict:
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": 0.0}


def _uptrend_with_pullback(*, bars_up: int = 90, dip: int = 4) -> list[dict]:
    """Synthetic 4h-style series: steady uptrend, a sharp pullback into the
    EMA zone, then a strong green recovery candle that flips Heikin Ashi."""
    candles: list[dict] = []
    px = 100.0
    for i in range(bars_up):
        o = px
        px += 0.6
        candles.append(_candle(o, px + 0.3, o - 0.3, px, i))
    # Pullback: red candles dropping into the EMA(high) zone.
    for j in range(dip):
        o = px
        px -= 1.6
        candles.append(_candle(o, o + 0.1, px - 0.4, px, bars_up + j))
    # Recovery: strong green candles (HA needs body dominance to flip).
    for k in range(3):
        o = px
        px += 3.0
        candles.append(_candle(o, px + 0.4, o - 0.1, px, bars_up + dip + k))
    return candles


def _ctx(candles: list[dict]) -> tuple[dict, StrategyContext]:
    return candles[-1], StrategyContext(candles=candles, symbol="BTC/USDT", timeframe="4h")


def _signals_over_replay(engine: HaTrendEngine, candles: list[dict]):
    """Feed every growing prefix (one closed candle per cycle, like the paper
    engine does) and collect the emitted signals."""
    out = []
    for end in range(engine.warmup_period, len(candles) + 1):
        window = candles[:end]
        signal = engine.on_candle(*_ctx(window))
        if signal is not None:
            out.append((end - 1, signal))
    return out


class HaTrendEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HaTrendEngine()
        self.engine.init({})

    def test_pullback_recovery_emits_long_with_bracket(self):
        candles = _uptrend_with_pullback()
        signals = _signals_over_replay(self.engine, candles)
        self.assertTrue(signals, "expected at least one LONG on the recovery")
        index, signal = signals[-1]
        self.assertEqual(signal.side, Side.LONG)
        close = candles[index]["close"]
        self.assertIsNotNone(signal.suggested_stop)
        self.assertIsNotNone(signal.suggested_take_profit)
        self.assertLess(signal.suggested_stop, close)
        # TP is exactly rr times the stop distance above the close.
        risk = close - signal.suggested_stop
        self.assertAlmostEqual(signal.suggested_take_profit, close + 3.0 * risk)
        # The signal fires on the recovery, not during the pullback itself.
        self.assertGreaterEqual(index, 90)

    def test_flat_market_never_signals(self):
        candles = [_candle(100.0, 100.5, 99.5, 100.0, i) for i in range(120)]
        self.assertEqual(_signals_over_replay(self.engine, candles), [])

    def test_downtrend_never_signals(self):
        candles = []
        px = 200.0
        for i in range(120):
            o = px
            px -= 0.6
            candles.append(_candle(o, o + 0.3, px - 0.3, px, i))
        self.assertEqual(_signals_over_replay(self.engine, candles), [])

    def test_no_signal_without_ha_flip(self):
        # Pure steady uptrend: HA stays green throughout, so there is no
        # red->green flip even though trend/RSI/touch may all hold.
        candles = []
        px = 100.0
        for i in range(120):
            o = px
            px += 0.6
            candles.append(_candle(o, px + 0.3, o - 0.5, px, i))
        flags = _ha_green_flags(candles)
        self.assertTrue(all(flags[10:]), "fixture must keep HA green")
        self.assertEqual(_signals_over_replay(self.engine, candles), [])

    def test_insufficient_history_is_no_signal(self):
        candles = _uptrend_with_pullback()[: self.engine.warmup_period - 1]
        self.assertIsNone(self.engine.on_candle(*_ctx(candles)))

    def test_bad_params_fall_back_to_defaults(self):
        self.engine.init({"ema_len": 0, "rr": "nope", "slope_len": False, "rsi_len": -3})
        self.assertEqual(self.engine._ema_len, 20)
        self.assertEqual(self.engine._slope_len, 5)
        self.assertEqual(self.engine._rr, 3.0)
        self.assertEqual(self.engine._rsi_len, 14)

    def test_registry_loads_ha_trend_with_params(self):
        goal = {"strategy_engine": {"name": "ha_trend", "params": {"swing_len": 8, "rr": 2.0}}}
        engine = load_engine(goal)
        self.assertIsInstance(engine, HaTrendEngine)
        self.assertEqual(engine._swing_len, 8)
        self.assertEqual(engine._rr, 2.0)

    def test_ema_matches_tradingview_seeding(self):
        values = [float(v) for v in range(1, 11)]
        out = _ema(values, 5)
        self.assertAlmostEqual(out[4], 3.0)  # SMA seed of the first 5 values
        self.assertAlmostEqual(out[5], (2 / 6) * 6.0 + (4 / 6) * 3.0)

    def test_rsi_extremes(self):
        rising = [float(v) for v in range(1, 100)]
        self.assertAlmostEqual(_rsi_last(rising, 14), 100.0)
        self.assertTrue(_rsi_last(rising[::-1], 14) < 50.0)


if __name__ == "__main__":
    unittest.main()
