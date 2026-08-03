import unittest
from unittest.mock import patch

from orum.strategies.base import Side, StrategyContext
from orum.strategies.ema_cross import (
    EmaCrossEngine,
    _directional_series,
    _ema_series,
)


def _candles(count: int = 70) -> list[dict]:
    return [
        {
            "ts": index,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 0.0,
        }
        for index in range(count)
    ]


def _context(candles: list[dict]) -> StrategyContext:
    return StrategyContext(
        candles=candles,
        symbol="BTC/USDT",
        timeframe="1h",
    )


def _long_cross(count: int) -> tuple[list[float], list[float]]:
    slow = [100.0] * count
    fast = [99.0] * count
    fast[-4] = 99.0
    fast[-3:] = [100.6, 100.8, 101.0]
    return fast, slow


def _short_cross(count: int) -> tuple[list[float], list[float]]:
    slow = [100.0] * count
    fast = [101.0] * count
    fast[-4] = 101.0
    fast[-3:] = [99.4, 99.2, 99.0]
    return fast, slow


class EmaCrossEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = EmaCrossEngine()
        self.engine.init({
            "allow_short": True,
            "min_ema_gap_atr": 0.25,
            "slope_lookback": 3,
            "min_slow_slope_atr": 0.05,
            "require_adx_rising": True,
            "short_reward_risk": 2.0,
        })
        self.candles = _candles()

    def _signal(
        self,
        fast: list[float],
        slow: list[float],
        *,
        adx: float = 25.0,
        previous_adx: float = 24.0,
        plus_di: float = 30.0,
        minus_di: float = 15.0,
        atr: float = 2.0,
    ):
        count = len(self.candles)
        adx_series = [previous_adx] * count
        adx_series[-1] = adx
        plus_series = [plus_di] * count
        minus_series = [minus_di] * count
        atr_series = [atr] * count
        with patch(
            "orum.strategies.ema_cross._ema_series",
            side_effect=[fast, slow],
        ), patch(
            "orum.strategies.ema_cross._directional_series",
            return_value=(adx_series, plus_series, minus_series, atr_series),
        ):
            return self.engine.on_candle(
                self.candles[-1], _context(self.candles)
            )

    def test_long_requires_direction_strength_gap_and_slope(self):
        fast, slow = _long_cross(len(self.candles))
        slow[-4:] = [99.6, 99.7, 99.8, 100.0]

        signal = self._signal(fast, slow)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.LONG)
        self.assertLess(signal.suggested_stop, self.candles[-1]["close"])
        self.assertIsNone(signal.suggested_take_profit)
        self.assertGreater(signal.strategy_metadata["plus_di"], signal.strategy_metadata["minus_di"])

    def test_long_rejects_flat_ema_separation(self):
        fast, slow = _long_cross(len(self.candles))
        fast[-1] = 100.1
        slow[-4:] = [99.8, 99.9, 100.0, 100.0]

        self.assertIsNone(self._signal(fast, slow))

    def test_long_rejects_bearish_directional_movement(self):
        fast, slow = _long_cross(len(self.candles))
        slow[-4:] = [99.6, 99.7, 99.8, 100.0]

        self.assertIsNone(
            self._signal(fast, slow, plus_di=15.0, minus_di=30.0)
        )

    def test_long_rejects_falling_adx(self):
        fast, slow = _long_cross(len(self.candles))
        slow[-4:] = [99.6, 99.7, 99.8, 100.0]

        self.assertIsNone(
            self._signal(fast, slow, adx=23.0, previous_adx=25.0)
        )

    def test_short_is_symmetric_and_has_complete_bracket(self):
        fast, slow = _short_cross(len(self.candles))
        slow[-4:] = [100.4, 100.3, 100.2, 100.0]

        signal = self._signal(
            fast,
            slow,
            plus_di=15.0,
            minus_di=30.0,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, Side.SHORT)
        self.assertGreater(signal.suggested_stop, self.candles[-1]["close"])
        self.assertLess(signal.suggested_take_profit, self.candles[-1]["close"])
        risk = signal.suggested_stop - self.candles[-1]["close"]
        self.assertAlmostEqual(
            signal.suggested_take_profit,
            self.candles[-1]["close"] - 2.0 * risk,
        )

    def test_short_can_be_disabled(self):
        self.engine.init({"allow_short": False})
        fast, slow = _short_cross(len(self.candles))
        slow[-4:] = [100.4, 100.3, 100.2, 100.0]

        self.assertIsNone(
            self._signal(
                fast,
                slow,
                plus_di=15.0,
                minus_di=30.0,
            )
        )

    def test_raw_crosses_wait_for_directional_confirmation(self):
        count = len(self.candles)
        slow = [100.0] * count
        down = [101.0] * count
        down[-1] = 99.0
        up = [99.0] * count
        up[-1] = 101.0

        with patch(
            "orum.strategies.ema_cross._ema_series",
            side_effect=[down, slow],
        ):
            down_signal = self.engine.on_candle(
                self.candles[-1], _context(self.candles)
            )
        with patch(
            "orum.strategies.ema_cross._ema_series",
            side_effect=[up, slow],
        ):
            up_signal = self.engine.on_candle(
                self.candles[-1], _context(self.candles)
            )

        self.assertIsNone(down_signal)
        self.assertIsNone(up_signal)

    def test_real_indicator_calculations_identify_a_strong_uptrend(self):
        candles = []
        closes = []
        for index in range(70):
            price = 100.0 + index
            closes.append(price)
            candles.append({
                "ts": index,
                "open": price - 0.2,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": 1.0,
            })

        fast = _ema_series(closes, 9)
        slow = _ema_series(closes, 21)
        adx, plus_di, minus_di, atr = _directional_series(candles, 14)

        self.assertGreater(fast[-1], slow[-1])
        self.assertGreater(adx[-1], 20.0)
        self.assertGreater(plus_di[-1], minus_di[-1])
        self.assertGreater(atr[-1], 0.0)


if __name__ == "__main__":
    unittest.main()
