import unittest

from hermes_trading.loop import is_duplicate_close


class DuplicateCloseTests(unittest.TestCase):
    def test_close_already_recorded_is_a_duplicate(self):
        closed_trade = {"signal_id": "BTC/USDT|02|rsi|long|25.0|1780412520000", "pnl_pct": 0.001}
        trades = [{"signal_id": "BTC/USDT|02|rsi|long|25.0|1780412520000", "ts": "2026-06-02T15:10:00+00:00"}]

        self.assertTrue(is_duplicate_close(closed_trade, trades))

    def test_fresh_close_is_not_a_duplicate(self):
        closed_trade = {"signal_id": "BTC/USDT|02|rsi|long|25.0|1780412520000"}
        trades = [{"signal_id": "BTC/USDT|02|rsi|long|25.0|1780411000000"}]

        self.assertFalse(is_duplicate_close(closed_trade, trades))

    def test_no_closed_trade_is_not_a_duplicate(self):
        self.assertFalse(is_duplicate_close(None, [{"signal_id": "x"}]))

    def test_close_without_signal_id_is_never_flagged(self):
        self.assertFalse(is_duplicate_close({"pnl_pct": 0.0}, [{"signal_id": None}]))


if __name__ == "__main__":
    unittest.main()
