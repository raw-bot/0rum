import unittest

from hermes_trading.loop import entry_signal_fired, market_decision, price_is_offline


class OfflineGuardTests(unittest.TestCase):
    def test_entry_fires_on_live_data_when_rsi_is_below_threshold(self):
        strategy = {"entry": {"threshold": 30, "direction": "long"}}
        market = {"source": "binance_public", "closes": [100.0] * 20}

        self.assertTrue(entry_signal_fired(strategy, rsi=25.0, market=market))

    def test_entry_never_fires_on_offline_fallback_even_with_extreme_rsi(self):
        strategy = {"entry": {"threshold": 30, "direction": "long"}}
        market = {"source": "offline_fallback", "closes": [100.0] * 20}

        self.assertFalse(entry_signal_fired(strategy, rsi=0.0, market=market))

    def test_entry_does_not_fire_above_threshold(self):
        strategy = {"entry": {"threshold": 30, "direction": "long"}}
        market = {"source": "binance_public", "closes": [100.0] * 20}

        self.assertFalse(entry_signal_fired(strategy, rsi=42.0, market=market))

    def test_price_is_offline_detects_fallback_source(self):
        self.assertTrue(price_is_offline({"source": "offline_fallback"}))
        self.assertFalse(price_is_offline({"source": "binance_public"}))

    def test_market_decision_reports_offline_freeze(self):
        decision = market_decision(
            entry_fired=False,
            position={"entry_price": 100.0},
            closed_trade=None,
            rsi=5.0,
            threshold=30.0,
            current_signal_id="sig-1",
            can_open=False,
            offline=True,
        )

        self.assertEqual(decision["action"], "offline_freeze")
        self.assertIn("frozen", decision["reason"])


if __name__ == "__main__":
    unittest.main()
