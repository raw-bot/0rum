import unittest

from orum.reflect import _fallback, _worst_daily_return

GOAL = {
    "reflection_every": 3,
    "daily_loss_limit": 0.015,
    "evidence_reflections_required": 1,
    "starting_balance_usd": 10000.0,
    "target_return_30d": 0.07,
    "max_drawdown": 0.05,
    "min_sharpe": 1.3,
}


class WorstDailyReturnTests(unittest.TestCase):
    def test_losses_on_the_same_day_accumulate(self):
        trades = [
            {"ts": "2026-06-10T09:00:00+00:00"},
            {"ts": "2026-06-10T15:00:00+00:00"},
            {"ts": "2026-06-11T09:00:00+00:00"},
        ]
        returns = [-0.01, -0.008, 0.002]

        self.assertAlmostEqual(_worst_daily_return(trades, returns), -0.018)

    def test_losses_spread_over_days_do_not_accumulate(self):
        trades = [{"ts": f"2026-06-{10 + index}T09:00:00+00:00"} for index in range(3)]
        returns = [-0.01, -0.008, 0.002]

        self.assertAlmostEqual(_worst_daily_return(trades, returns), -0.01)


class DailyLossGuardrailTests(unittest.TestCase):
    def test_accumulated_same_day_losses_trigger_risk_reduction(self):
        strategy = {"version": "01", "entry": {"threshold": 30}, "position_size_r": 0.7}
        # Three trades the same day, each -0.6% of the account: the single
        # worst trade is above the limit but the daily sum (-1.8%) breaches it.
        trades = [
            {"ts": f"2026-06-10T0{index}:00:00+00:00", "net_pnl_usd": -60.0} for index in range(3)
        ]

        result = _fallback(strategy, GOAL, trades, [])

        self.assertEqual(result["issue"], "daily_loss_guardrail")
        self.assertTrue(result["changed"])
        self.assertEqual(result["variable"], "position_size_r")
        # The nudge lands in the risk: block (outside the mutable DSL) and the
        # legacy top-level key is removed.
        self.assertAlmostEqual(strategy["risk"]["position_size_r"], 0.6)
        self.assertNotIn("position_size_r", strategy)


if __name__ == "__main__":
    unittest.main()
