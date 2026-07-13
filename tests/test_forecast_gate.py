import math
import unittest
from datetime import datetime, timezone

from orum.portfolio.forecast_gate import decide_forecast_gate, walk_forward_forecast


def _market(count: int = 760) -> list[dict]:
    candles = []
    price = 100.0
    for index in range(count):
        move = 0.00035 + math.sin(index / 17) * 0.0012 + math.cos(index / 43) * 0.0006
        opened = price
        price *= 1 + move
        candles.append({
            "ts": 1_700_000_000_000 + index * 3_600_000,
            "open": opened,
            "high": max(opened, price) * 1.001,
            "low": min(opened, price) * 0.999,
            "close": price,
            "volume": 1000 + index,
        })
    return candles


class ForecastGateTests(unittest.TestCase):
    def test_walk_forward_report_has_ordered_quantiles_and_no_future_training(self):
        report = walk_forward_forecast(_market(), min_evaluations=80, neighbour_count=40)

        self.assertEqual(set(report["horizons"]), {"6", "12", "24"})
        self.assertEqual(report["origin_price"], _market()[-1]["close"])
        for horizon in report["horizons"].values():
            q = horizon["quantiles"]
            self.assertLessEqual(q["p10"], q["p25"])
            self.assertLessEqual(q["p25"], q["p50"])
            self.assertLessEqual(q["p50"], q["p75"])
            self.assertLessEqual(q["p75"], q["p90"])
            self.assertGreaterEqual(horizon["metrics"]["samples"], 80)
            self.assertTrue(horizon["metrics"]["chronology_verified"])
            self.assertLessEqual(horizon["metrics"]["latest_training_target_index"], report["origin_index"])

    def test_walk_forward_report_exposes_last_seven_days_at_fixed_24h_target(self):
        candles = _market()

        report = walk_forward_forecast(candles, min_evaluations=80, neighbour_count=40)

        history = report["history_24h"]
        self.assertEqual(len(history), 168)
        self.assertEqual(
            history[-1]["target_ts"],
            datetime.fromtimestamp(candles[-1]["ts"] / 1000, tz=timezone.utc).isoformat(timespec="seconds"),
        )
        self.assertEqual(
            history[-1]["origin_ts"],
            datetime.fromtimestamp(candles[-25]["ts"] / 1000, tz=timezone.utc).isoformat(timespec="seconds"),
        )
        self.assertAlmostEqual(
            history[-1]["predicted_price"],
            history[-1]["origin_price"] * (1 + history[-1]["median_return"]),
        )
        self.assertEqual(history[-1]["actual_price"], candles[-1]["close"])
        self.assertEqual(history[-1]["source"], "walk_forward")

    def test_walk_forward_history_prediction_does_not_use_its_future_target(self):
        original = _market()
        changed = [dict(candle) for candle in original]
        changed[-1]["close"] *= 1.5

        before = walk_forward_forecast(
            original, min_evaluations=80, neighbour_count=40
        )["history_24h"][-1]
        after = walk_forward_forecast(
            changed, min_evaluations=80, neighbour_count=40
        )["history_24h"][-1]

        self.assertAlmostEqual(after["predicted_price"], before["predicted_price"])
        self.assertNotEqual(after["actual_price"], before["actual_price"])

    def test_locked_report_preserves_baseline_size(self):
        report = {"active": False, "lock_reasons": ["12h: samples 40 < 250"], "horizons": {}}
        decision = decide_forecast_gate(report, atr_risk_fraction=0.02)
        self.assertEqual(decision["action"], "locked")
        self.assertEqual(decision["multiplier"], 1.0)
        self.assertFalse(decision["influenced"])

    def test_qualified_negative_forecast_vetoes_long_entry(self):
        report = {
            "active": True,
            "horizons": {
                "12": {"quantiles": {"p10": -0.04, "p25": -0.03, "p50": -0.01, "p75": 0.0, "p90": 0.01}, "metrics": {"direction_accuracy": 0.56}},
                "24": {"quantiles": {"p10": -0.05, "p25": -0.04, "p50": -0.02, "p75": -0.01, "p90": -0.001}, "metrics": {"direction_accuracy": 0.57}},
            },
        }
        decision = decide_forecast_gate(report, atr_risk_fraction=0.02)
        self.assertEqual(decision["action"], "veto")
        self.assertEqual(decision["multiplier"], 0.0)
        self.assertTrue(decision["influenced"])

    def test_qualified_positive_forecast_can_make_small_bounded_boost(self):
        report = {
            "active": True,
            "horizons": {
                "12": {"quantiles": {"p10": -0.01, "p25": 0.002, "p50": 0.01, "p75": 0.02, "p90": 0.03}, "metrics": {"direction_accuracy": 0.56}},
                "24": {"quantiles": {"p10": -0.015, "p25": 0.003, "p50": 0.015, "p75": 0.03, "p90": 0.05}, "metrics": {"direction_accuracy": 0.58}},
            },
        }
        decision = decide_forecast_gate(report, atr_risk_fraction=0.02)
        self.assertEqual(decision["action"], "boost")
        self.assertEqual(decision["multiplier"], 1.15)


if __name__ == "__main__":
    unittest.main()
