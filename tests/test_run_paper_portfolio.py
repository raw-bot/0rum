import unittest
from contextlib import nullcontext
from unittest.mock import patch

from scripts import run_paper_portfolio


class PaperPortfolioRuntimeTests(unittest.TestCase):
    def test_binance_provider_drops_forming_candle(self):
        rows = [
            {"time": 0, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1},
            {"time": 900, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1},
            {"time": 1800, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1},
        ]
        with patch.object(run_paper_portfolio, "fetch_klines", return_value=rows), \
             patch.object(run_paper_portfolio.time, "time", return_value=1800.0):
            result = run_paper_portfolio.binance_provider("BTC/USDT", "15m", 2)
        self.assertEqual([row["ts"] for row in result], [0, 900_000])

    def test_direct_utbot_sleeve_and_shared_risk_caps_are_configured(self):
        config = run_paper_portfolio.load_config()
        by_id = {row["id"]: row for row in config["strategies"]}
        sleeve = by_id["btc_utbot_m15_h1"]
        self.assertEqual(sleeve["engine"], "utbot_mtf")
        self.assertEqual(sleeve["symbol"], "BTC/USDT")
        self.assertEqual(sleeve["timeframe"], "15m")
        self.assertEqual(sleeve["risk_pct"], 0.005)
        self.assertTrue(sleeve["entry_enabled"])
        self.assertEqual(config["max_total_stop_risk_pct"], 0.05)
        self.assertEqual(config["max_symbol_stop_risk_pct"], 0.03)

    def test_authoritative_ak_4h_sleeve_accepts_new_entries(self):
        config = run_paper_portfolio.load_config()
        sleeve = {row["id"]: row for row in config["strategies"]}["btc_ak_macd_4h"]
        self.assertEqual(sleeve["engine"], "ak_macd")
        self.assertEqual(sleeve["timeframe"], "4h")
        self.assertTrue(sleeve["entry_enabled"])
        self.assertEqual(sleeve["exit_policy"], "structural_bracket")
        self.assertEqual(sleeve["monitor_timeframe"], "15m")

    def test_overlapping_once_cycle_is_skipped(self):
        class Engine:
            def run_cycle(self):
                raise AssertionError("overlapping cycle must not execute")

        with patch.object(run_paper_portfolio, "cycle_lock", return_value=nullcontext(False)):
            self.assertIsNone(run_paper_portfolio.run_guarded_cycle(Engine()))


if __name__ == "__main__":
    unittest.main()
