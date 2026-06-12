import unittest

from hermes_trading.loop import entry_signal_fired, market_candles, market_decision, price_is_offline

STRATEGY = {"entry": {"indicator": "rsi", "threshold": 30, "direction": "long"}}


def _market(closes, source):
    return {"source": source, "closes": closes, "last_candle_ts": 60_000 * len(closes)}


class OfflineGuardTests(unittest.TestCase):
    def test_entry_fires_on_live_data_when_rsi_is_below_threshold(self):
        market = _market([100.0 - index * 0.5 for index in range(20)], "binance_public")  # RSI -> 0

        result = entry_signal_fired(STRATEGY, market_candles(market), market)

        self.assertTrue(result["triggered"])

    def test_entry_never_fires_on_offline_fallback_even_with_extreme_rsi(self):
        market = _market([100.0 - index * 0.5 for index in range(20)], "offline_fallback")

        result = entry_signal_fired(STRATEGY, market_candles(market), market)

        self.assertFalse(result["triggered"])

    def test_entry_does_not_fire_above_threshold(self):
        market = _market([100.0 + index * 0.5 for index in range(20)], "binance_public")  # RSI -> 100

        result = entry_signal_fired(STRATEGY, market_candles(market), market)

        self.assertFalse(result["triggered"])

    def test_price_is_offline_detects_fallback_source(self):
        self.assertTrue(price_is_offline({"source": "offline_fallback"}))
        self.assertFalse(price_is_offline({"source": "binance_public"}))

    def test_market_decision_reports_offline_freeze(self):
        decision = market_decision(
            entry_fired=False,
            position={"entry_price": 100.0},
            closed_trade=None,
            entry_summary="rsi(14) <= 30 -> not evaluated",
            current_signal_id="sig-1",
            can_open=False,
            offline=True,
        )

        self.assertEqual(decision["action"], "offline_freeze")
        self.assertIn("frozen", decision["reason"])


if __name__ == "__main__":
    unittest.main()
