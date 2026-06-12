import json
import math
import unittest
from pathlib import Path

from hermes_trading.dsl import indicators

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "talib_reference.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text())
CANDLES = FIXTURE["candles"]
EXPECTED = FIXTURE["expected"]


def _assert_series_matches(testcase, actual, expected_key, places=6):
    expected = EXPECTED[expected_key]
    testcase.assertEqual(len(actual), len(expected), f"{expected_key}: length mismatch")
    for index, (got, want) in enumerate(zip(actual, expected)):
        if want is None:
            testcase.assertTrue(math.isnan(got), f"{expected_key}[{index}]: expected NaN warm-up, got {got}")
        else:
            testcase.assertAlmostEqual(got, want, places=places, msg=f"{expected_key}[{index}]")


class TalibReferenceTests(unittest.TestCase):
    """Each indicator against frozen TA-Lib reference values (see
    scripts/gen_talib_fixtures.py; TA-Lib is not a runtime dependency)."""

    def test_rsi_14_matches_talib(self):
        _assert_series_matches(self, indicators.rsi(CANDLES, 14), "rsi_14")

    def test_rsi_2_matches_talib(self):
        _assert_series_matches(self, indicators.rsi(CANDLES, 2), "rsi_2")

    def test_sma_20_matches_talib(self):
        _assert_series_matches(self, indicators.sma(CANDLES, 20), "sma_20")

    def test_sma_2_matches_talib(self):
        _assert_series_matches(self, indicators.sma(CANDLES, 2), "sma_2")

    def test_ema_20_matches_talib(self):
        _assert_series_matches(self, indicators.ema(CANDLES, 20), "ema_20")

    def test_ema_9_matches_talib(self):
        _assert_series_matches(self, indicators.ema(CANDLES, 9), "ema_9")

    def test_atr_14_matches_talib(self):
        _assert_series_matches(self, indicators.atr(CANDLES, 14), "atr_14")

    def test_atr_2_matches_talib(self):
        _assert_series_matches(self, indicators.atr(CANDLES, 2), "atr_2")

    def test_bollinger_20_2_matches_talib(self):
        bands = indicators.bollinger(CANDLES, 20, 2.0)
        _assert_series_matches(self, bands["upper"], "bollinger_20_2_upper")
        _assert_series_matches(self, bands["middle"], "bollinger_20_2_middle")
        _assert_series_matches(self, bands["lower"], "bollinger_20_2_lower")

    def test_bollinger_5_1_matches_talib(self):
        bands = indicators.bollinger(CANDLES, 5, 1.0)
        _assert_series_matches(self, bands["upper"], "bollinger_5_1_upper")
        _assert_series_matches(self, bands["middle"], "bollinger_5_1_middle")
        _assert_series_matches(self, bands["lower"], "bollinger_5_1_lower")


class SeriesShapeTests(unittest.TestCase):
    """Full-series contract: same length as input, NaN warm-up prefix, never
    a fabricated scalar."""

    def test_every_numeric_indicator_returns_full_length_series(self):
        for name, params, field in (
            ("rsi", {"period": 14}, None),
            ("sma", {"period": 20}, None),
            ("ema", {"period": 20}, None),
            ("atr", {"period": 14}, None),
            ("bollinger", {"period": 20, "std_dev": 2.0}, "pct_b"),
            ("close", None, None),
        ):
            series = indicators.series(name, params, field, CANDLES)
            self.assertEqual(len(series), len(CANDLES), name)

    def test_warmup_prefix_is_nan_and_first_valid_index_is_exact(self):
        for name, params, expected_first in (
            ("rsi", {"period": 14}, 14),
            ("sma", {"period": 20}, 19),
            ("ema", {"period": 20}, 19),
            ("atr", {"period": 14}, 14),
            ("bollinger", {"period": 20, "std_dev": 2.0}, 19),
        ):
            field = "middle" if name == "bollinger" else None
            series = indicators.series(name, params, field, CANDLES)
            self.assertEqual(indicators.first_valid_index(name, params), expected_first, name)
            for index in range(expected_first):
                self.assertTrue(math.isnan(series[index]), f"{name}[{index}] should be warm-up NaN")
            self.assertFalse(math.isnan(series[expected_first]), f"{name}[{expected_first}] should be valid")

    def test_insufficient_candles_yields_all_nan_never_a_value(self):
        short = CANDLES[:5]
        for name, params in (("rsi", {"period": 14}), ("sma", {"period": 20}), ("atr", {"period": 14})):
            series = indicators.series(name, params, None, short)
            self.assertEqual(len(series), 5, name)
            self.assertTrue(all(math.isnan(value) for value in series), f"{name} must not fabricate values")

    def test_pct_b_is_nan_when_band_width_is_zero(self):
        flat = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0}] * 30
        bands = indicators.bollinger(flat, 20, 2.0)
        self.assertTrue(math.isnan(bands["pct_b"][-1]))
        self.assertAlmostEqual(bands["upper"][-1], 100.0)

    def test_flat_series_rsi_is_zero_not_a_silent_fallback(self):
        flat = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0}] * 30
        self.assertEqual(indicators.rsi(flat, 14)[-1], 0.0)


class RegimeWrapperTests(unittest.TestCase):
    def test_regime_returns_label_series_matching_classifier(self):
        rising = [{"open": p, "high": p + 1, "low": p - 1, "close": p, "volume": 1.0} for p in range(100, 130)]
        labels = indicators.regime(rising)
        self.assertEqual(len(labels), 30)
        self.assertEqual(labels[0], "unknown")
        self.assertEqual(labels[-1], "favorable")

    def test_bollinger_without_field_is_an_error_not_a_default(self):
        with self.assertRaises(indicators.IndicatorError):
            indicators.series("bollinger", {"period": 20, "std_dev": 2.0}, None, CANDLES)

    def test_unknown_indicator_raises(self):
        with self.assertRaises(indicators.IndicatorError):
            indicators.series("macd", {"period": 12}, None, CANDLES)


if __name__ == "__main__":
    unittest.main()
