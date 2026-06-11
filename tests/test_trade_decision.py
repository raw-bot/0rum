import unittest

from hermes_trading.loop import (
    close_position_if_needed,
    market_decision,
    open_position_from_signal,
    signal_id,
    should_record_signal,
)
from hermes_trading.market_regime import rolling_return_regime


class TradeDecisionTests(unittest.TestCase):
    def test_same_signal_on_same_candle_is_not_recorded_twice(self):
        strategy = {"version": "01", "entry": {"threshold": 30, "direction": "long"}}
        current_signal = signal_id("BTC/USDT", strategy, {"last_candle_ts": 123456})
        previous_trades = [{"signal_id": current_signal}]

        self.assertFalse(should_record_signal(current_signal, previous_trades))

    def test_same_strategy_can_record_a_new_candle_signal(self):
        strategy = {"version": "01", "entry": {"threshold": 30, "direction": "long"}}
        previous_signal = signal_id("BTC/USDT", strategy, {"last_candle_ts": 123456})
        current_signal = signal_id("BTC/USDT", strategy, {"last_candle_ts": 123516})
        previous_trades = [{"signal_id": previous_signal}]

        self.assertTrue(should_record_signal(current_signal, previous_trades))

    def test_position_stays_open_until_exit_condition(self):
        strategy = {"stop_loss_pct": 2.0, "take_profit_pct": 3.0, "max_hold_candles": 30, "exit_rsi_threshold": 55}
        position = {"entry_price": 100.0, "opened_candle_ts": 1, "opened_index": 0}
        market = {"last_candle_ts": 2, "closes": [100.0, 100.5]}

        self.assertIsNone(close_position_if_needed(position, strategy, market, rsi=40.0))

    def test_position_closes_on_take_profit(self):
        strategy = {"stop_loss_pct": 2.0, "take_profit_pct": 3.0, "max_hold_candles": 30, "exit_rsi_threshold": 55}
        position = {"entry_price": 100.0, "opened_candle_ts": 1, "opened_index": 0}
        market = {"last_candle_ts": 2, "closes": [100.0, 103.2]}

        result = close_position_if_needed(position, strategy, market, rsi=40.0)

        self.assertIsNotNone(result)
        self.assertEqual(result["exit_reason"], "take_profit")

    def test_open_position_contains_sizing_values(self):
        strategy = {"version": "01", "entry": {"threshold": 30, "direction": "long"}, "stop_loss_pct": 2.0, "position_size_r": 0.5}
        goal = {"starting_balance_usd": 10000}
        market = {"last_candle_ts": 123, "closes": [99.0, 100.0]}

        position = open_position_from_signal("BTC/USDT", strategy, goal, market, rsi=25.0)

        self.assertEqual(position["notional_usd"], 2500.0)
        self.assertEqual(position["risk_usd"], 50.0)
        self.assertAlmostEqual(position["qty_base"], 25.0)
        self.assertIn("market_regime_at_entry", position)

    def test_closed_trade_contains_regime_context(self):
        strategy = {"stop_loss_pct": 2.0, "take_profit_pct": 3.0, "max_hold_candles": 30, "exit_rsi_threshold": 55}
        position = {
            "entry_price": 100.0,
            "opened_candle_ts": 1,
            "opened_index": 0,
            "market_regime_at_entry": "neutral",
            "rsi_at_entry": 25.0,
            "price_source_at_entry": "test",
        }
        market = {"last_candle_ts": 2, "closes": [100.0, 103.2], "source": "test"}

        result = close_position_if_needed(position, strategy, market, rsi=40.0)

        self.assertIsNotNone(result)
        self.assertEqual(result["market_regime_at_entry"], "neutral")
        self.assertIn("market_regime_at_exit", result)
        self.assertEqual(result["rsi_at_entry"], 25.0)
        self.assertEqual(result["rsi_at_exit"], 40.0)

    def test_offline_candle_timestamp_does_not_crash_exit_logic(self):
        strategy = {"stop_loss_pct": 2.0, "take_profit_pct": 3.0, "max_hold_candles": 30, "exit_rsi_threshold": 55}
        position = {"entry_price": 100.0, "opened_candle_ts": "offline-10", "opened_index": 10}
        market = {"last_candle_ts": "offline-59", "closes": [100.0] * 59 + [103.2], "source": "offline_fallback"}

        result = close_position_if_needed(position, strategy, market, rsi=40.0)

        self.assertIsNotNone(result)
        self.assertEqual(result["exit_reason"], "take_profit")

    def test_market_decision_explains_wait_state(self):
        decision = market_decision(
            entry_fired=False,
            position=None,
            closed_trade=None,
            rsi=42.25,
            threshold=30.0,
            current_signal_id="sig-1",
            can_open=False,
        )

        self.assertEqual(decision["action"], "wait")
        self.assertIn("RSI 42.25", decision["reason"])

    def test_rolling_return_regime_classifies_context(self):
        self.assertEqual(rolling_return_regime([100, 101])["label"], "favorable")
        self.assertEqual(rolling_return_regime([100, 99])["label"], "unfavorable")
        self.assertEqual(rolling_return_regime([100, 100.1])["label"], "neutral")
