"""The rewired dashboard risk card: _portfolio_config mirrors the engine's
config precedence, and the slider setters edit state/portfolio.yaml so the
live paper engine picks the change up at its next cycle."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from orum import dashboard
from orum.portfolio.paper_engine import PaperEngine

PORTFOLIO_YAML = """\
# committed fallback — comments must survive slider edits
starting_balance_usd: 10000.0
max_total_stop_risk_pct: 0.05

strategies:
  - id: btc_ak_macd_4h
    engine: ak_macd
    symbol: BTC/USDT
    timeframe: "4h"
    risk_pct: 0.02
    entry_enabled: true
    exit_policy: structural_bracket
    reward_risk_ratio: 1.5
    params:
      allow_short: false

  - id: btc_utbot_m15_h1
    engine: utbot_mtf
    symbol: BTC/USDT
    timeframe: "15m"
    risk_pct: 0.005
    entry_enabled: true
    exit_policy: signal_or_stop
    params:
      buy_key: 6.0
"""
GOAL_YAML = "risk_per_trade_min: 0.005\nrisk_per_trade_max: 0.02\n"


class PortfolioRiskCardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.state = root / "state"
        self.state.mkdir()
        (self.state / "goal.yaml").write_text(GOAL_YAML)
        self.default_path = root / "config-portfolio.yaml"
        self.default_path.write_text(PORTFOLIO_YAML)
        patches = [
            patch.object(dashboard, "STATE_DIR", self.state),
            patch.object(dashboard, "PORTFOLIO_DEFAULT_PATH", self.default_path),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    # ---- lecture (carte) ---------------------------------------------------

    def test_portfolio_config_falls_back_to_default_then_prefers_runtime(self):
        config = dashboard._portfolio_config()
        self.assertEqual(config["source"], "config/portfolio.yaml")
        self.assertEqual(config["strategies"][0]["risk_pct"], 0.02)
        self.assertEqual(config["risk_bounds"], {"min": 0.005, "max": 0.02})

        override = PORTFOLIO_YAML.replace("risk_pct: 0.02", "risk_pct: 0.01")
        (self.state / "portfolio.yaml").write_text(override)
        config = dashboard._portfolio_config()
        self.assertEqual(config["source"], "state/portfolio.yaml")
        self.assertEqual(config["strategies"][0]["risk_pct"], 0.01)

    # ---- écriture (sliders) --------------------------------------------------

    def test_set_strategy_risk_materializes_override_and_edits_only_target(self):
        result = dashboard.set_strategy_risk("btc_ak_macd_4h", 0.01)
        self.assertTrue(result["ok"])
        self.assertEqual(result["risk_pct"], 0.01)
        text = (self.state / "portfolio.yaml").read_text()
        self.assertIn("comments must survive", text)          # commentaires préservés
        doc = yaml.safe_load(text)
        by_id = {s["id"]: s for s in doc["strategies"]}
        self.assertEqual(by_id["btc_ak_macd_4h"]["risk_pct"], 0.01)
        self.assertEqual(by_id["btc_utbot_m15_h1"]["risk_pct"], 0.005)  # intact
        self.assertEqual(by_id["btc_ak_macd_4h"]["reward_risk_ratio"], 1.5)  # intact

    def test_set_strategy_risk_clamps_and_snaps(self):
        self.assertEqual(dashboard.set_strategy_risk("btc_ak_macd_4h", 0.5)["risk_pct"], 0.02)
        self.assertEqual(dashboard.set_strategy_risk("btc_ak_macd_4h", 0.0001)["risk_pct"], 0.005)
        self.assertEqual(dashboard.set_strategy_risk("btc_ak_macd_4h", 0.0117)["risk_pct"], 0.0125)

    def test_set_strategy_reward_edits_bracket_strategy_only(self):
        result = dashboard.set_strategy_reward("btc_ak_macd_4h", 2.0)
        self.assertEqual(result["reward_risk_ratio"], 2.0)
        doc = yaml.safe_load((self.state / "portfolio.yaml").read_text())
        self.assertEqual(doc["strategies"][0]["reward_risk_ratio"], 2.0)
        with self.assertRaises(ValueError):  # utbot n'a pas de clé reward_risk_ratio
            dashboard.set_strategy_reward("btc_utbot_m15_h1", 2.0)

    def test_invalid_inputs_are_rejected(self):
        for sid, value in (("nope", 0.01), ("btc_ak_macd_4h", "abc"),
                           ("btc_ak_macd_4h", float("nan")), (None, 0.01)):
            with self.subTest(sid=sid, value=value):
                with self.assertRaises(ValueError):
                    dashboard.set_strategy_risk(sid, value)

    # ---- max leverage (plafond de notional, PaperBroker.open) ---------------

    def test_set_portfolio_leverage_adds_then_edits_top_level_key(self):
        result = dashboard.set_portfolio_leverage(3.0)
        self.assertEqual(result["max_leverage"], 3.0)
        text = (self.state / "portfolio.yaml").read_text()
        self.assertIn("comments must survive", text)
        self.assertEqual(yaml.safe_load(text)["max_leverage"], 3.0)
        dashboard.set_portfolio_leverage(2.0)  # 2e passage : édite la ligne existante
        doc = yaml.safe_load((self.state / "portfolio.yaml").read_text())
        self.assertEqual(doc["max_leverage"], 2.0)
        self.assertEqual(dashboard._portfolio_config()["max_leverage"], 2.0)

    def test_set_portfolio_leverage_clamps_and_snaps(self):
        self.assertEqual(dashboard.set_portfolio_leverage(99)["max_leverage"], 5.0)
        self.assertEqual(dashboard.set_portfolio_leverage(0.1)["max_leverage"], 1.0)
        self.assertEqual(dashboard.set_portfolio_leverage(2.7)["max_leverage"], 2.5)
        with self.assertRaises(ValueError):
            dashboard.set_portfolio_leverage("abc")

    def test_broker_caps_notional_at_max_leverage(self):
        from orum.portfolio.paper_broker import Account, PaperBroker

        broker = PaperBroker()
        # stop très serré : sizing par risque => notional 20x l'equity sans plafond
        kwargs = dict(strategy_id="s", symbol="BTC/USDT", price=100.0,
                      atr_risk=0.1, risk_pct=0.02, equity_for_sizing=1000.0,
                      stop_loss_price=99.9, take_profit_price=100.2)
        uncapped = broker.open(Account(balance_usd=1000.0), **kwargs)
        self.assertEqual(uncapped["notional_usd"], 20000.0)   # comportement historique intact
        self.assertFalse(uncapped["leverage_capped"])

        capped = broker.open(Account(balance_usd=1000.0), **kwargs, max_leverage=2.0)
        self.assertEqual(capped["notional_usd"], 2000.0)       # plafonné à 2x l'equity
        self.assertTrue(capped["leverage_capped"])
        self.assertEqual(capped["stop_loss_price"], 99.9)      # niveaux SL/TP intacts
        self.assertEqual(capped["take_profit_price"], 100.2)

        # une position déjà sous le plafond n'est pas touchée
        small = broker.open(Account(balance_usd=1000.0),
                            **{**kwargs, "atr_risk": 50.0}, max_leverage=2.0)
        self.assertFalse(small["leverage_capped"])

    def test_engine_reads_max_leverage_from_config(self):
        cfg = yaml.safe_load(PORTFOLIO_YAML)
        self.assertIsNone(PaperEngine(cfg, candle_provider=lambda *a, **k: [])._max_leverage)  # noqa: SLF001
        cfg["max_leverage"] = 2.5
        self.assertEqual(PaperEngine(cfg, candle_provider=lambda *a, **k: [])._max_leverage, 2.5)  # noqa: SLF001

    # ---- boucle complète : le moteur charge bien la nouvelle valeur ---------

    def test_paper_engine_loads_the_slider_value(self):
        dashboard.set_strategy_risk("btc_ak_macd_4h", 0.01)
        dashboard.set_strategy_reward("btc_ak_macd_4h", 2.5)
        cfg = yaml.safe_load((self.state / "portfolio.yaml").read_text())
        engine = PaperEngine(cfg, candle_provider=lambda *a, **k: [])
        sc = {s.id: s for s in engine._strategies}["btc_ak_macd_4h"]  # noqa: SLF001
        self.assertEqual(sc.risk_pct, 0.01)
        self.assertEqual(sc.reward_risk_ratio, 2.5)


if __name__ == "__main__":
    unittest.main()
