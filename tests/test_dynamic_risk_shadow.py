import json
import tempfile
import unittest
from pathlib import Path

from orum.portfolio.dynamic_risk import DynamicRiskShadow
from orum.portfolio.paper_engine import PaperEngine
from orum.strategies import register_engine
from orum.strategies.base import Side, Signal


def _candles(count=20):
    return [
        {
            "ts": 1_800_000_000_000 + index * 900_000,
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 100.0,
            "volume": 1.0,
        }
        for index in range(count)
    ]


def _artifact():
    return {
        "schema_version": 1,
        "status": "research_only",
        "strategy_id": "btc",
        "promotion_eligible": False,
        "promotion_blocker": "no_untouched_holdout",
        "buckets": [
            {
                "id": "wide_stop",
                "stop_distance_atr_risk_min": 3.0,
                "stop_distance_atr_risk_max": None,
                "sample_n": 134,
                "wins": 46,
                "p_win_research_estimate": 0.352,
                "payoff_r_winsorized": 2.2,
                "kelly_fraction": 0.1,
                "max_risk_pct": 0.02,
            }
        ],
    }


class LongSignalEngine:
    name = "dynamic_risk_test"
    version = "1"
    required_timeframes = []
    required_indicators = []
    warmup_period = 1

    def init(self, config):
        pass

    def on_candle(self, candle, context):
        return Signal(
            side=Side.LONG,
            symbol=context.symbol,
            timeframe=context.timeframe,
            entry_reason="test long",
            suggested_stop=72.0,
        )


class DynamicRiskShadowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register_engine("dynamic_risk_test", LongSignalEngine)

    @classmethod
    def tearDownClass(cls):
        from orum.strategies import _ENGINES

        _ENGINES.pop("dynamic_risk_test", None)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.artifact_path = self.root / "artifact.json"
        self.artifact_path.write_text(json.dumps(_artifact()))

    def tearDown(self):
        self._tmp.cleanup()

    def test_shadow_kelly_never_changes_actual_risk(self):
        shadow = DynamicRiskShadow.from_path(self.artifact_path)

        proposal = shadow.propose(
            strategy_id="btc",
            base_risk_pct=0.01,
            drawdown_multiplier=0.75,
            risk_distance=14.0,
            atr_risk=4.0,
        )

        self.assertEqual(proposal["mode"], "shadow")
        self.assertFalse(proposal["promotion_eligible"])
        self.assertAlmostEqual(proposal["actual_risk_pct"], 0.0075)
        self.assertLess(proposal["shadow_risk_pct"], 0.0075)
        self.assertLessEqual(proposal["shadow_risk_pct"], 0.02 * 0.75)

    def test_malformed_artifact_falls_back_without_an_uplift(self):
        self.artifact_path.write_text("not-json")
        shadow = DynamicRiskShadow.from_path(self.artifact_path)

        proposal = shadow.propose(
            strategy_id="btc",
            base_risk_pct=0.01,
            drawdown_multiplier=0.75,
            risk_distance=14.0,
            atr_risk=4.0,
        )

        self.assertEqual(proposal["mode"], "fallback")
        self.assertAlmostEqual(proposal["shadow_risk_pct"], 0.0075)
        self.assertIn("artifact", proposal["fallback_reason"])

    def _engine(self, config):
        return PaperEngine(
            config,
            candle_provider=lambda _symbol, _timeframe, limit: _candles()[-limit:],
            positions_path=self.root / "positions.json",
            fills_path=self.root / "fills.jsonl",
            equity_path=self.root / "equity.jsonl",
            dynamic_risk_shadow_path=self.root / "dynamic_shadow.jsonl",
        )

    def _config(self):
        return {
            "starting_balance_usd": 10_000.0,
            "reentry_policy": "hold",
            "risk_sizing_basis": "actual_stop",
            "strategies": [
                {
                    "id": "btc",
                    "engine": "dynamic_risk_test",
                    "symbol": "BTC/USDT",
                    "timeframe": "15m",
                    "risk_pct": 0.01,
                    "entry_enabled": True,
                    "params": {},
                }
            ],
        }

    def test_drawdown_policy_scales_risk_without_blocking_the_entry(self):
        (self.root / "positions.json").write_text(json.dumps({
            "balance_usd": 8_700.0,
            "positions": {},
            "processed_candles": {},
        }))
        (self.root / "equity.jsonl").write_text(
            json.dumps({"ts": "peak", "equity_usd": 10_000.0}) + "\n"
        )
        config = self._config()
        config["entry_drawdown_risk_scale"] = {
            "start_pct": 0.10,
            "halt_pct": 0.20,
            "floor_multiplier": 0.10,
        }

        summary = self._engine(config).run_cycle()

        self.assertEqual(summary["intents"]["btc"], "open")
        self.assertAlmostEqual(summary["entry_drawdown"], 0.13)
        self.assertAlmostEqual(summary["entry_drawdown_multiplier"], 0.73)
        self.assertAlmostEqual(summary["fills"][0]["risk_pct"], 0.0073)

    def test_drawdown_policy_halts_new_entries_at_the_hard_boundary(self):
        (self.root / "positions.json").write_text(json.dumps({
            "balance_usd": 8_000.0,
            "positions": {},
            "processed_candles": {},
        }))
        (self.root / "equity.jsonl").write_text(
            json.dumps({"ts": "peak", "equity_usd": 10_000.0}) + "\n"
        )
        config = self._config()
        config["entry_drawdown_risk_scale"] = {
            "start_pct": 0.10,
            "halt_pct": 0.20,
            "floor_multiplier": 0.10,
        }

        summary = self._engine(config).run_cycle()

        self.assertEqual(summary["intents"]["btc"], "drawdown_kill_switch")
        self.assertEqual(summary["fills"], [])
        self.assertEqual(summary["entry_drawdown_multiplier"], 0.0)

    def test_legacy_kill_switch_and_drawdown_scale_are_mutually_exclusive(self):
        config = self._config()
        config["entry_drawdown_kill_pct"] = 0.05
        config["entry_drawdown_risk_scale"] = {
            "start_pct": 0.10,
            "halt_pct": 0.20,
            "floor_multiplier": 0.10,
        }

        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            self._engine(config)

    def test_dynamic_sizing_is_logged_but_cannot_change_the_fill(self):
        config = self._config()
        config["dynamic_risk_shadow"] = {
            "enabled": True,
            "artifact_path": str(self.artifact_path),
        }

        summary = self._engine(config).run_cycle()

        self.assertAlmostEqual(summary["fills"][0]["risk_pct"], 0.01)
        record = json.loads(
            (self.root / "dynamic_shadow.jsonl").read_text().splitlines()[0]
        )
        self.assertEqual(record["strategy_id"], "btc")
        self.assertAlmostEqual(record["actual_risk_pct"], 0.01)
        self.assertNotEqual(record["shadow_risk_pct"], record["actual_risk_pct"])


if __name__ == "__main__":
    unittest.main()
