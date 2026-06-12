import unittest

from hermes_trading.accounting import max_drawdown
from hermes_trading.loop import _build_closed_trade, guardrail_action, market_decision

GOAL = {"max_drawdown": 0.05, "emergency_stop_drawdown": 0.06, "starting_balance_usd": 10000.0}


class GuardrailActionTests(unittest.TestCase):
    def test_normal_below_thresholds(self):
        self.assertEqual(guardrail_action(0.01, GOAL, resume_ack=False), "normal")

    def test_halt_entries_at_max_drawdown(self):
        self.assertEqual(guardrail_action(0.05, GOAL, resume_ack=False), "halt_entries")

    def test_emergency_at_kill_threshold(self):
        self.assertEqual(guardrail_action(0.06, GOAL, resume_ack=False), "emergency")
        self.assertEqual(guardrail_action(0.10, GOAL, resume_ack=False), "emergency")

    def test_manual_ack_resumes_trading(self):
        self.assertEqual(guardrail_action(0.10, GOAL, resume_ack=True), "normal")

    def test_drawdown_uses_account_level_returns(self):
        trades = [{"net_pnl_usd": -300.0}, {"net_pnl_usd": -350.0}]

        drawdown = max_drawdown(trades, GOAL)

        self.assertGreater(drawdown, 0.06)
        self.assertEqual(guardrail_action(drawdown, GOAL, resume_ack=False), "emergency")


class GuardrailDecisionTests(unittest.TestCase):
    def test_decision_reports_guardrail_halt(self):
        decision = market_decision(
            entry_fired=True,
            position=None,
            closed_trade=None,
            entry_summary="rsi(14) <= 30 (lhs=20) -> met",
            current_signal_id="sig-1",
            can_open=False,
            guardrail="halt_entries",
        )

        self.assertEqual(decision["action"], "guardrail_halt")
        self.assertIn("Max drawdown", decision["reason"])

    def test_emergency_close_record_carries_exit_reason(self):
        strategy = {"stop_loss_pct": 2.0, "take_profit_pct": 3.0, "fee_rate": 0.0004}
        position = {
            "entry_price": 100.0,
            "opened_candle_ts": 1,
            "opened_index": 0,
            "notional_usd": 2500.0,
            "rsi_at_entry": 25.0,
        }
        market = {"last_candle_ts": 2, "closes": [100.0, 100.4], "source": "test"}

        trade = _build_closed_trade(position, strategy, market, rsi=40.0, regime=None, exit_reason="emergency_stop")

        self.assertEqual(trade["exit_reason"], "emergency_stop")
        self.assertAlmostEqual(trade["pnl_pct"], 0.004)
        self.assertIn("net_pnl_usd", trade)


if __name__ == "__main__":
    unittest.main()
