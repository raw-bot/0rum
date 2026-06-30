"""Tests for loop._shadow_check_native_dsl: logs (never raises, never feeds
back into a decision) when NativeDslEngine disagrees with the
entry_signal_fired/exit_signal_fired results loop.py's run_loop computes
every iteration. This is read-only instrumentation ahead of any cutover."""

import unittest
from unittest.mock import patch

from orum.dsl.migrate import migrate_strategy_file
from orum.loop import _shadow_check_native_dsl, entry_signal_fired, exit_signal_fired, market_candles

LEGACY_STRATEGY = {
    "version": "03",
    "entry": {"indicator": "rsi", "threshold": 25.0, "direction": "long"},
    "stop_loss_pct": 2.0,
    "take_profit_pct": 3.0,
    "max_hold_candles": 30,
    "exit_rsi_threshold": 60.0,
    "position_size_r": 0.5,
}
STRATEGY = migrate_strategy_file(LEGACY_STRATEGY)


def _market(closes, source="binance_public"):
    return {"closes": closes, "last_candle_ts": 60_000 * len(closes), "source": source}


class ShadowAgreementTests(unittest.TestCase):
    def test_no_log_when_real_evals_and_engine_agree(self):
        closes = [100.0 - index * 0.5 for index in range(20)]  # deep oversold: both fire entry
        market = _market(closes)
        candles = market_candles(market)
        entry_eval = entry_signal_fired(STRATEGY, candles, market)
        exit_eval = exit_signal_fired(STRATEGY, candles)

        with patch("orum.loop.log_event") as mock_log:
            _shadow_check_native_dsl(STRATEGY, candles, "BTC/USDT", entry_eval, exit_eval)

        mock_log.assert_not_called()

    def test_no_log_on_a_flat_decision_market(self):
        closes = [100.0 + (0.1 if i % 2 == 0 else -0.1) for i in range(20)]
        market = _market(closes)
        candles = market_candles(market)
        entry_eval = entry_signal_fired(STRATEGY, candles, market)
        exit_eval = exit_signal_fired(STRATEGY, candles)
        self.assertFalse(entry_eval["triggered"])
        self.assertFalse(exit_eval["triggered"])

        with patch("orum.loop.log_event") as mock_log:
            _shadow_check_native_dsl(STRATEGY, candles, "BTC/USDT", entry_eval, exit_eval)

        mock_log.assert_not_called()


class ShadowDisagreementTests(unittest.TestCase):
    def test_logs_when_the_passed_in_eval_disagrees_with_the_engine(self):
        # Forge a real (entry_eval, exit_eval) pair that doesn't match what
        # NativeDslEngine will independently compute on the same candles --
        # exercises the comparison/logging path itself, regardless of
        # whether a real divergence currently exists in production.
        closes = [100.0 - index * 0.5 for index in range(20)]
        market = _market(closes)
        candles = market_candles(market)
        real_entry_eval = entry_signal_fired(STRATEGY, candles, market)
        self.assertTrue(real_entry_eval["triggered"])
        forged_entry_eval = {**real_entry_eval, "triggered": False}
        exit_eval = exit_signal_fired(STRATEGY, candles)

        with patch("orum.loop.log_event") as mock_log:
            _shadow_check_native_dsl(STRATEGY, candles, "BTC/USDT", forged_entry_eval, exit_eval)

        mock_log.assert_called_once()
        kind, detail = mock_log.call_args.args[:2]
        self.assertEqual(kind, "strategy_engine_shadow_disagreement")
        self.assertIn("long", detail)
        self.assertEqual(mock_log.call_args.kwargs["shadow"], "long")
        self.assertIsNone(mock_log.call_args.kwargs["legacy"])

    def test_engine_exception_is_logged_and_never_raised(self):
        closes = [100.0 - index * 0.5 for index in range(20)]
        candles = market_candles(_market(closes))
        broken_strategy = {**STRATEGY, "entry": "not-a-dict"}  # forces NativeDslEngine.init to misbehave

        with patch("orum.loop.log_event") as mock_log:
            _shadow_check_native_dsl(broken_strategy, candles, "BTC/USDT", {"triggered": False}, {"triggered": False})

        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args.args[0], "strategy_engine_shadow_error")


if __name__ == "__main__":
    unittest.main()
