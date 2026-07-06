import unittest

from orum.executor import Executor, PaperExecutor
from orum.loop import (
    _build_closed_trade,
    close_position_if_needed,
    open_position_from_signal,
)

ENTRY_STRATEGY = {
    "version": "01",
    "entry": {"threshold": 30, "direction": "long"},
    "stop_loss_pct": 2.0,
    "position_size_r": 0.5,
}
EXIT_STRATEGY = {"stop_loss_pct": 2.0, "take_profit_pct": 3.0, "max_hold_candles": 30}


def _drop_timestamps(record: dict) -> dict:
    # opened_at / ts come from the wall clock; everything else must be identical.
    return {k: v for k, v in record.items() if k not in ("opened_at", "ts")}


class PaperExecutorDelegationTests(unittest.TestCase):
    def setUp(self):
        self.executor: Executor = PaperExecutor()

    def test_open_matches_direct_function(self):
        goal = {"starting_balance_usd": 10000}
        market = {"last_candle_ts": 123, "closes": [99.0, 100.0]}
        via_seam = self.executor.open(asset="BTC/USDT", strategy=ENTRY_STRATEGY, goal=goal, market=market, rsi=25.0)
        direct = open_position_from_signal("BTC/USDT", ENTRY_STRATEGY, goal, market, 25.0)
        self.assertEqual(_drop_timestamps(via_seam), _drop_timestamps(direct))
        self.assertEqual(via_seam["notional_usd"], 2500.0)

    def test_close_returns_none_when_no_exit(self):
        position = {"entry_price": 100.0, "opened_candle_ts": 1, "opened_index": 0}
        market = {"last_candle_ts": 2, "closes": [100.0, 100.5]}
        self.assertIsNone(
            self.executor.close(position=position, strategy=EXIT_STRATEGY, market=market, rsi=40.0)
        )

    def test_close_matches_direct_function_on_take_profit(self):
        position = {"entry_price": 100.0, "opened_candle_ts": 1, "opened_index": 0}
        market = {"last_candle_ts": 2, "closes": [100.0, 103.2], "source": "test"}
        via_seam = self.executor.close(position=position, strategy=EXIT_STRATEGY, market=market, rsi=40.0)
        direct = close_position_if_needed(position, EXIT_STRATEGY, market, 40.0)
        self.assertIsNotNone(via_seam)
        self.assertEqual(via_seam["exit_reason"], "take_profit")
        self.assertEqual(_drop_timestamps(via_seam), _drop_timestamps(direct))

    def test_force_close_matches_build_closed_trade(self):
        position = {"entry_price": 100.0, "opened_candle_ts": 1, "opened_index": 0, "notional_usd": 2500.0}
        market = {"last_candle_ts": 2, "closes": [100.0, 90.0], "source": "test"}
        via_seam = self.executor.force_close(position=position, strategy=EXIT_STRATEGY, market=market, rsi=40.0)
        direct = _build_closed_trade(position, EXIT_STRATEGY, market, 40.0, None, "emergency_stop")
        self.assertEqual(via_seam["exit_reason"], "emergency_stop")
        self.assertEqual(_drop_timestamps(via_seam), _drop_timestamps(direct))

    def test_paper_executor_satisfies_protocol(self):
        self.assertIsInstance(self.executor, Executor)


if __name__ == "__main__":
    unittest.main()
