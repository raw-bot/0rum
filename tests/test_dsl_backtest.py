"""Non-regression backtest: fill model and the _backtest_guard rejections."""

import unittest
from unittest.mock import patch

from hermes_trading.dsl.backtest import simulate
from hermes_trading.reflect import _backtest_guard

# 2026-06-01T00:00:00Z, aligned on a UTC midnight so same-day losses accumulate.
DAY_START_MS = 1_780_272_000_000

RISK = {"stop_loss_pct": 2.0, "take_profit_pct": 3.0, "max_hold_candles": 30, "position_size_r": 0.5, "fee_rate": 0.0004}
GOAL = {"starting_balance_usd": 10000.0, "daily_loss_limit": 0.015, "asset": "BTC/USDT"}


def _candle(index, close, low=None, high=None):
    return {
        "ts": DAY_START_MS + index * 60_000,
        "open": close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": float(close),
        "volume": 1.0,
    }


def _flat(count, price=100.0, start=0):
    return [_candle(start + i, price) for i in range(count)]


def _groups(entry_value, exit_value, exit_logic="OR"):
    return {
        "entry": {"logic": "AND", "conditions": [{"indicator": "close", "operator": "<", "value": entry_value}]},
        "exit": {"logic": exit_logic, "conditions": [{"indicator": "close", "operator": ">", "value": exit_value}]},
    }


class SimulateFillModelTests(unittest.TestCase):
    def test_entry_fills_at_signal_candle_close_and_tp_fills_intrabar(self):
        candles = _flat(210) + [_candle(210, 90.0)] + _flat(10, 90.0, start=211)
        candles.append(_candle(221, 92.0, high=93.5))  # TP = 90 * 1.03 = 92.7 < high
        result = simulate(_groups(entry_value=95.0, exit_value=1000.0), RISK, GOAL, candles, buffer=200)

        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertEqual(trade["entry_price"], 90.0)
        self.assertEqual(trade["exit_reason"], "take_profit")
        self.assertAlmostEqual(trade["exit_price"], 92.7)
        self.assertEqual(result["errors"], [])

    def test_stop_has_priority_when_stop_and_tp_are_in_the_same_candle(self):
        candles = _flat(210) + [_candle(210, 90.0)]
        # One candle spanning both the stop (88.2) and the TP (92.7).
        candles.append(_candle(211, 90.0, low=88.0, high=93.0))
        result = simulate(_groups(entry_value=95.0, exit_value=1000.0), RISK, GOAL, candles, buffer=200)

        self.assertEqual(result["trades"][0]["exit_reason"], "stop_loss")
        self.assertAlmostEqual(result["trades"][0]["exit_price"], 90.0 * 0.98)

    def test_max_hold_closes_a_position_that_never_moves(self):
        candles = _flat(210) + [_candle(210 + i, 90.0) for i in range(40)]
        result = simulate(_groups(entry_value=95.0, exit_value=1000.0), RISK, GOAL, candles, buffer=200)

        self.assertEqual(result["trades"][0]["exit_reason"], "max_hold")
        self.assertEqual(result["trades"][0]["exit_index"] - result["trades"][0]["entry_index"], 30)

    def test_dsl_exit_fires_at_close_when_no_risk_exit_hits(self):
        risk = dict(RISK, take_profit_pct=50.0, stop_loss_pct=50.0)
        candles = _flat(210) + [_candle(210, 90.0), _candle(211, 90.0), _candle(212, 101.0)]
        result = simulate(_groups(entry_value=95.0, exit_value=100.0), risk, GOAL, candles, buffer=200)

        self.assertEqual(result["trades"][0]["exit_reason"], "dsl_exit")
        self.assertEqual(result["trades"][0]["exit_price"], 101.0)

    def test_zero_entries_is_reported(self):
        result = simulate(_groups(entry_value=-1.0, exit_value=1000.0), RISK, GOAL, _flat(260), buffer=200)
        self.assertEqual(result["entries_triggered"], 0)
        self.assertEqual(result["trades"], [])

    def test_same_day_losses_accumulate_in_daily_returns(self):
        candles = _flat(210)
        # Sawtooth: re-enter at 90 and get stopped intrabar, repeatedly, same UTC day.
        for i in range(6):
            candles.append(_candle(210 + 2 * i, 90.0))
            candles.append(_candle(211 + 2 * i, 90.0, low=87.0))
        result = simulate(_groups(entry_value=95.0, exit_value=1000.0), RISK, GOAL, candles, buffer=200)

        self.assertGreaterEqual(len(result["trades"]), 3)
        self.assertEqual(len(result["daily_returns"]), 1)
        self.assertLess(result["worst_day"], -0.015)

    def test_evaluation_errors_surface_never_raise(self):
        groups = {
            "entry": {"logic": "AND", "conditions": [{"indicator": "rsi", "params": {"period": 14}, "operator": "<", "value": 30}]},
            "exit": {"logic": "OR", "conditions": [{"indicator": "close", "operator": ">", "value": 1000}]},
        }
        result = simulate(groups, RISK, GOAL, _flat(12), buffer=10)
        self.assertTrue(result["errors"])
        self.assertEqual(result["trades"], [])


class BacktestGuardTests(unittest.TestCase):
    """Each spec rejection: zero signals, simulated daily-loss breach,
    degenerate trade count; plus fail-closed on missing history."""

    def _strategy(self, entry_value=95.0, fee_rate=0.0004):
        return {
            "version": "04",
            "dsl_version": 1,
            "entry": {"logic": "AND", "conditions": [{"indicator": "close", "operator": "<", "value": entry_value}]},
            "exit": {"logic": "OR", "conditions": [{"indicator": "close", "operator": ">", "value": 1000.0}]},
            "risk": dict(RISK, fee_rate=fee_rate),
            "direction": "long",
        }

    def _guard(self, strategy, proposed, candles):
        with patch("hermes_trading.reflect.dsl_backtest.load_history", return_value=candles):
            return _backtest_guard(strategy, proposed["entry"], proposed["exit"], GOAL)

    def test_zero_signal_proposal_is_rejected(self):
        candles = _flat(300)
        reason = self._guard(self._strategy(), _groups(entry_value=-1.0, exit_value=1000.0), candles)
        self.assertIn("zero entry signals", reason)

    def test_daily_loss_breach_is_rejected(self):
        candles = _flat(210)
        for i in range(6):
            candles.append(_candle(210 + 2 * i, 90.0))
            candles.append(_candle(211 + 2 * i, 90.0, low=87.0))
        reason = self._guard(self._strategy(entry_value=-1.0), _groups(entry_value=95.0, exit_value=1000.0), candles)
        self.assertIn("daily_loss_limit", reason)

    def test_degenerate_trade_count_is_rejected(self):
        # Proposed churns a trade every other candle (instant dsl_exit), the
        # current strategy never trades; fee_rate 0 keeps the daily loss
        # guard out of the way so degeneracy is what rejects.
        candles = _flat(300, price=100.0)
        strategy = self._strategy(entry_value=-1.0, fee_rate=0.0)
        proposed = _groups(entry_value=200.0, exit_value=50.0)
        reason = self._guard(strategy, proposed, candles)
        self.assertIn("degenerate trade count", reason)

    def test_healthy_proposal_passes(self):
        candles = _flat(210) + [_candle(210, 90.0)] + _flat(10, 90.0, start=211)
        candles.append(_candle(221, 92.0, high=93.5))
        candles += _flat(50, 100.0, start=222)
        strategy = self._strategy()
        reason = self._guard(strategy, _groups(entry_value=94.0, exit_value=1000.0), candles)
        self.assertIsNone(reason)

    def test_missing_history_fails_closed(self):
        strategy = self._strategy()
        with patch(
            "hermes_trading.reflect.dsl_backtest.load_history",
            side_effect=RuntimeError("history fetch returned 0 candles"),
        ):
            reason = _backtest_guard(strategy, strategy["entry"], strategy["exit"], GOAL)
        self.assertIn("history unavailable", reason)


if __name__ == "__main__":
    unittest.main()
