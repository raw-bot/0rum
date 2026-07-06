import unittest
from unittest.mock import patch

from orum.reflect import _cooldown_status, _orum

GOAL = {
    "cooldown_after_change_trades": 10,
    "starting_balance_usd": 10000.0,
    "target_return_30d": 0.07,
    "max_drawdown": 0.05,
    "min_sharpe": 1.3,
}


class OrumCooldownTests(unittest.TestCase):
    def test_cooldown_status_blocks_right_after_a_change(self):
        hypotheses = [{"ts": "2026-06-02T14:07:17+00:00", "changed": True, "variable": "entry.threshold"}]
        trades = [{"ts": "2026-06-02T14:36:42+00:00", "pnl_pct": -0.001}]

        block = _cooldown_status(GOAL, trades, hypotheses)

        self.assertIsNotNone(block)
        self.assertEqual(block["issue"], "cooldown")
        self.assertIn("9 more closed trades required", block["reason"])

    def test_cooldown_status_clears_after_enough_trades(self):
        hypotheses = [{"ts": "2026-06-02T14:07:17+00:00", "changed": True, "variable": "entry.threshold"}]
        trades = [{"ts": f"2026-06-02T15:{index:02d}:00+00:00", "pnl_pct": 0.0} for index in range(10)]

        self.assertIsNone(_cooldown_status(GOAL, trades, hypotheses))

    def test_orum_mode_does_not_call_the_llm_during_cooldown(self):
        hypotheses = [{"ts": "2026-06-02T14:07:17+00:00", "changed": True, "variable": "entry.threshold"}]
        trades = [{"ts": "2026-06-02T14:36:42+00:00", "pnl_pct": -0.001}]

        with patch("orum.reflect.subprocess.run") as run:
            hypothesis = _orum({}, GOAL, trades, hypotheses)

        run.assert_not_called()
        self.assertFalse(hypothesis["changed"])
        self.assertEqual(hypothesis["mode"], "0rum")
        self.assertEqual(hypothesis["issue"], "cooldown")


if __name__ == "__main__":
    unittest.main()
