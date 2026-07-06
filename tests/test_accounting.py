import unittest

from orum.accounting import account_returns, compound_balance, trade_net_usd
from orum.dashboard import _portfolio
from orum.score import score

GOAL = {"starting_balance_usd": 10000.0, "target_return_30d": 0.07, "max_drawdown": 0.05, "min_sharpe": 1.3}


def _fee_eaten_trade() -> dict:
    # Real pattern from trades.jsonl: gross price gain is positive but the
    # round-trip fee on the 2500 USD notional exceeds it.
    return {
        "pnl_pct": 0.0003,
        "notional_usd": 2500.0,
        "pnl_usd": 0.75,
        "fees_usd": 2.0,
        "net_pnl_usd": -1.25,
    }


class AccountingTests(unittest.TestCase):
    def test_net_usd_prefers_recorded_net_then_falls_back(self):
        self.assertEqual(trade_net_usd({"net_pnl_usd": -1.25}, 10000.0), -1.25)
        self.assertEqual(trade_net_usd({"pnl_usd": 3.0, "fees_usd": 2.0}, 10000.0), 1.0)
        self.assertEqual(trade_net_usd({"pnl_pct": 0.01, "notional_usd": 2500.0, "fees_usd": 2.0}, 10000.0), 23.0)
        self.assertEqual(trade_net_usd({"pnl_pct": 0.01}, 10000.0), 100.0)

    def test_returns_are_account_level_not_price_level(self):
        trades = [{"pnl_pct": 0.01, "notional_usd": 2500.0, "pnl_usd": 25.0, "fees_usd": 2.0, "net_pnl_usd": 23.0}]

        returns = account_returns(trades, GOAL)

        # +1% price move on a 2500 notional is +0.23% of the account, not +1%.
        self.assertAlmostEqual(returns[0], 0.0023)

    def test_fee_eaten_trades_yield_negative_score_and_portfolio(self):
        trades = [_fee_eaten_trade() for _ in range(10)]

        returns = account_returns(trades, GOAL)
        portfolio = _portfolio(trades, GOAL)

        self.assertTrue(all(item < 0 for item in returns))
        self.assertLess(score(trades, GOAL), 0)
        self.assertLess(portfolio["pnl_usd"], 0)
        self.assertAlmostEqual(portfolio["pnl_usd"], -12.5, places=6)

    def test_compound_balance_applies_net_results_sequentially(self):
        trades = [
            {"net_pnl_usd": 100.0},
            {"net_pnl_usd": -50.0},
        ]

        self.assertAlmostEqual(compound_balance(trades, GOAL), 10050.0)

    def test_legacy_pct_only_trades_keep_historical_meaning(self):
        trades = [{"pnl_pct": 0.01}, {"pnl_pct": -0.02}]

        balance = compound_balance(trades, GOAL)

        self.assertAlmostEqual(balance, 10000.0 * 1.01 * 0.98)


if __name__ == "__main__":
    unittest.main()
