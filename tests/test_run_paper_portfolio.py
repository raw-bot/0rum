import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import run_paper_portfolio


class PaperPortfolioRuntimeTests(unittest.TestCase):
    def test_load_config_falls_back_to_committed_config_when_state_override_is_absent(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "portfolio.yaml"
            config = run_paper_portfolio.load_config(path=missing)

        self.assertIn("strategies", config)
        self.assertIn(
            "btc_utbot_m15_h1",
            {row["id"] for row in config["strategies"]},
        )

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

    def test_market_provider_routes_nvda_away_from_binance(self):
        candles = [{"ts": 1, "close": 100.0}]
        with patch.object(
            run_paper_portfolio, "fetch_us_equity_candles", return_value=candles
        ) as us_provider, patch.object(
            run_paper_portfolio, "binance_provider"
        ) as binance:
            result = run_paper_portfolio.paper_market_provider("NVDA", "5m", 300)

        self.assertEqual(result, candles)
        us_provider.assert_called_once_with("NVDA", "5m", 300)
        binance.assert_not_called()

    def test_nvda_opening_range_sleeve_is_paper_enabled_in_both_configs(self):
        for path in (
            run_paper_portfolio.DEFAULT_PORTFOLIO_PATH,
            run_paper_portfolio.PORTFOLIO_PATH,
        ):
            config = run_paper_portfolio.load_config(path=path)
            sleeve = {row["id"]: row for row in config["strategies"]}[
                "nvda_opening_range"
            ]
            self.assertEqual(sleeve["engine"], "opening_range")
            self.assertEqual(sleeve["symbol"], "NVDA")
            self.assertEqual(sleeve["timeframe"], "5m")
            self.assertEqual(sleeve["risk_pct"], 0.0025)
            self.assertTrue(sleeve["entry_enabled"])

    def test_direct_utbot_sleeve_and_shared_risk_caps_are_configured(self):
        # Committed defaults: state/portfolio.yaml is operator-owned since the
        # dashboard risk sliders (risk_pct/reward_risk_ratio/max_leverage) write
        # into it, so structural assertions target the committed file.
        config = run_paper_portfolio.load_config(path=run_paper_portfolio.DEFAULT_PORTFOLIO_PATH)
        by_id = {row["id"]: row for row in config["strategies"]}
        sleeve = by_id["btc_utbot_m15_h1"]
        self.assertEqual(sleeve["engine"], "utbot_mtf")
        self.assertEqual(sleeve["symbol"], "BTC/USDT")
        self.assertEqual(sleeve["timeframe"], "15m")
        self.assertEqual(sleeve["risk_pct"], 0.005)
        self.assertTrue(sleeve["entry_enabled"])
        self.assertEqual(config["max_total_stop_risk_pct"], 0.05)
        self.assertEqual(config["max_symbol_stop_risk_pct"], 0.03)
        self.assertEqual(config["reentry_policy"], "topup")
        self.assertEqual(
            config["merit_order"],
            ["btc_utbot_m15_h1", "btc_ak_macd_4h"],
        )
        self.assertEqual(config["min_topup_fraction"], 0.0)

    def test_operator_state_activates_same_topup_policy_without_risk_reduction(self):
        config = run_paper_portfolio.load_config(
            path=run_paper_portfolio.PORTFOLIO_PATH
        )
        by_id = {row["id"]: row for row in config["strategies"]}

        self.assertEqual(config["reentry_policy"], "topup")
        self.assertEqual(
            config["merit_order"],
            ["btc_utbot_m15_h1", "btc_ak_macd_4h"],
        )
        self.assertEqual(config["min_topup_fraction"], 0.0)
        self.assertEqual(by_id["btc_ak_macd_4h"]["risk_pct"], 0.02)
        self.assertEqual(by_id["btc_utbot_m15_h1"]["risk_pct"], 0.005)

    def test_all_configured_sleeves_accept_new_paper_entries(self):
        for path in (
            run_paper_portfolio.DEFAULT_PORTFOLIO_PATH,
            run_paper_portfolio.PORTFOLIO_PATH,
        ):
            config = run_paper_portfolio.load_config(path=path)
            by_id = {row["id"]: row for row in config["strategies"]}
            for strategy_id in (
                "btc_ak_macd_4h",
                "btc_utbot_m15_h1",
                "btc_ha_trend_4h",
                "eth_donchian",
                "gold_cot",
            ):
                self.assertTrue(by_id[strategy_id]["entry_enabled"], strategy_id)

    def test_authoritative_ak_4h_sleeve_accepts_new_entries(self):
        config = run_paper_portfolio.load_config(path=run_paper_portfolio.DEFAULT_PORTFOLIO_PATH)
        sleeve = {row["id"]: row for row in config["strategies"]}["btc_ak_macd_4h"]
        self.assertEqual(sleeve["engine"], "ak_macd")
        self.assertEqual(sleeve["timeframe"], "4h")
        self.assertTrue(sleeve["entry_enabled"])
        self.assertEqual(sleeve["exit_policy"], "structural_bracket")
        self.assertEqual(sleeve["monitor_timeframe"], "15m")

    def test_ak_paper_sleeve_enables_short_candidates_in_both_configs(self):
        for path in (
            run_paper_portfolio.DEFAULT_PORTFOLIO_PATH,
            run_paper_portfolio.PORTFOLIO_PATH,
        ):
            config = run_paper_portfolio.load_config(path=path)
            sleeve = {row["id"]: row for row in config["strategies"]}["btc_ak_macd_4h"]
            self.assertIs(sleeve["params"]["allow_short"], True)

    def test_ema_cross_btc_and_eth_are_enabled_paper_experiments(self):
        for path in (
            run_paper_portfolio.DEFAULT_PORTFOLIO_PATH,
            run_paper_portfolio.PORTFOLIO_PATH,
        ):
            config = run_paper_portfolio.load_config(path=path)
            by_id = {row["id"]: row for row in config["strategies"]}
            for strategy_id, symbol in (
                ("btc_ema_cross", "BTC/USDT"),
                ("eth_ema_cross", "ETH/USDT"),
            ):
                sleeve = by_id[strategy_id]
                self.assertEqual(sleeve["engine"], "ema_cross")
                self.assertEqual(sleeve["symbol"], symbol)
                self.assertEqual(sleeve["timeframe"], "1h")
                self.assertEqual(sleeve["monitor_timeframe"], "15m")
                self.assertEqual(sleeve["exit_policy"], "signal_or_stop")
                self.assertEqual(sleeve["risk_pct"], 0.005)
                self.assertTrue(sleeve["entry_enabled"])
                self.assertTrue(sleeve["params"]["allow_short"])
                self.assertEqual(sleeve["params"]["min_ema_gap_atr"], 0.20)
                self.assertEqual(sleeve["params"]["min_slow_slope_atr"], 0.05)
                self.assertTrue(sleeve["params"]["require_adx_rising"])
                self.assertEqual(sleeve["dynamic_exit"]["mode"], "execute")
                self.assertEqual(
                    sleeve["dynamic_exit"]["version"], "mfe_ratchet_v1"
                )

    def test_overlapping_once_cycle_is_skipped(self):
        class Engine:
            def run_cycle(self):
                raise AssertionError("overlapping cycle must not execute")

        with patch.object(run_paper_portfolio, "cycle_lock", return_value=nullcontext(False)):
            self.assertIsNone(run_paper_portfolio.run_guarded_cycle(Engine()))


if __name__ == "__main__":
    unittest.main()
