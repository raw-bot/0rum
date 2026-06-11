import unittest

from hermes_trading.score import score


class ScoreTests(unittest.TestCase):
    def test_negative_expectancy_cannot_score_positive_just_because_drawdown_is_small(self):
        trades = [
            {"pnl_pct": -0.0005},
            {"pnl_pct": -0.0005},
            {"pnl_pct": -0.0005},
            {"pnl_pct": 0.0001},
        ]
        goal = {"target_return_30d": 0.07, "max_drawdown": 0.05, "min_sharpe": 1.3}

        self.assertLess(score(trades, goal), 0)
