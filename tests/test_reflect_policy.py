import unittest

from hermes_trading.dsl.migrate import migrate_strategy_file
from hermes_trading.reflect import _fallback


class ReflectPolicyTests(unittest.TestCase):
    def test_fallback_requires_repeated_evidence_before_change(self):
        goal = {
            "reflection_every": 4,
            "target_return_30d": 0.07,
            "max_drawdown": 0.05,
            "min_sharpe": 1.3,
            "evidence_reflections_required": 2,
        }
        strategy = migrate_strategy_file(
            {"version": "01", "entry": {"indicator": "rsi", "threshold": 30, "direction": "long"}, "position_size_r": 0.5}
        )
        trades = [
            {"ts": "2026-06-01T00:00:00Z", "pnl_pct": -0.0005},
            {"ts": "2026-06-01T00:01:00Z", "pnl_pct": -0.0005},
            {"ts": "2026-06-01T00:02:00Z", "pnl_pct": -0.0005},
            {"ts": "2026-06-01T00:03:00Z", "pnl_pct": 0.0001},
        ]

        first = _fallback(strategy, goal, trades, [])
        second = _fallback(strategy, goal, trades, [first])

        self.assertFalse(first["changed"])
        self.assertEqual(first["issue"], "negative_realised_weak_score")
        self.assertTrue(second["changed"])
        self.assertEqual(second["variable"], "entry.threshold")
        # The nudge is now a DSL value mutation: rsi entry value 30 -> 28.
        self.assertEqual(strategy["entry"]["conditions"][0]["value"], 28)

    def test_fallback_respects_cooldown_after_change(self):
        goal = {
            "reflection_every": 2,
            "target_return_30d": 0.07,
            "max_drawdown": 0.05,
            "min_sharpe": 1.3,
            "cooldown_after_change_trades": 10,
            "evidence_reflections_required": 1,
        }
        strategy = {"version": "02", "entry": {"threshold": 28}, "position_size_r": 0.5}
        trades = [{"ts": "2026-06-01T00:00:00Z", "pnl_pct": -0.001}, {"ts": "2026-06-01T00:01:00Z", "pnl_pct": -0.001}]
        hypotheses = [{"ts": "2026-06-01T00:02:00Z", "changed": True, "variable": "entry.threshold"}]

        result = _fallback(strategy, goal, trades, hypotheses)

        self.assertFalse(result["changed"])
        self.assertEqual(result["issue"], "cooldown")
        self.assertIn("10 more closed trades required", result["reason"])
