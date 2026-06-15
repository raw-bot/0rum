import unittest

from hermes_trading.external import (
    ExternalSignalStatus,
    ValidationContext,
    parse_external_signal,
    validate_external_signal,
)

BAR_MS = 1781424000000  # divisible by 900000 (15m grid)

GOAL = {
    "starting_balance_usd": 10000.0,
    "max_drawdown": 0.05,
    "emergency_stop_drawdown": 0.06,
    "allowed_external_strategies": [
        {
            "id": "ema_momentum_v3",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "events": ["BUY_CANDIDATE", "SELL_CANDIDATE", "EXIT"],
        }
    ],
}


def _signal(**overrides):
    payload = {
        "source": "tradingview",
        "strategy": "ema_momentum_v3",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "event": "BUY_CANDIDATE",
        "bar_time": BAR_MS,
        "price": 68200.5,
        "version": "tv_ema_momentum_v3",
    }
    payload.update(overrides)
    return parse_external_signal(payload)


def _ctx(**overrides):
    base = dict(goal=GOAL)
    base.update(overrides)
    return ValidationContext(**base)


class AcceptanceTests(unittest.TestCase):
    def test_clean_buy_is_accepted(self):
        result = validate_external_signal(_signal(), _ctx())
        self.assertTrue(result.accepted)
        self.assertEqual(result.status, ExternalSignalStatus.ACCEPTED)
        self.assertEqual(result.guardrail, "normal")
        self.assertIsNone(result.check)

    def test_exit_with_open_position_accepted(self):
        result = validate_external_signal(
            _signal(event="EXIT"),
            _ctx(open_position={"asset": "BTC/USDT", "direction": "long"}),
        )
        self.assertTrue(result.accepted)

    def test_bar_closed_when_now_past_close(self):
        result = validate_external_signal(_signal(), _ctx(now_ms=BAR_MS + 900_000))
        self.assertTrue(result.accepted)

    def test_short_allowed_when_enabled(self):
        goal = {**GOAL, "allow_short": True}
        result = validate_external_signal(_signal(event="SELL_CANDIDATE"), _ctx(goal=goal))
        self.assertTrue(result.accepted)

    def test_resume_ack_lifts_guardrail(self):
        # Drawdown would be emergency, but a manual ack resumes trading.
        result = validate_external_signal(
            _signal(),
            _ctx(recent_trades=({"net_pnl_usd": -700.0},), resume_ack=True),
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.guardrail, "normal")


class RejectionTests(unittest.TestCase):
    def _assert_reject(self, result, check, needle=None):
        self.assertEqual(result.status, ExternalSignalStatus.REJECTED)
        self.assertEqual(result.check, check)
        if needle:
            self.assertIn(needle, result.reason)

    def test_source_not_authorized(self):
        goal = {**GOAL, "allowed_external_sources": ["someone_else"]}
        self._assert_reject(validate_external_signal(_signal(), _ctx(goal=goal)), "source")

    def test_strategy_not_in_allowlist(self):
        self._assert_reject(validate_external_signal(_signal(strategy="unknown_strat"), _ctx()), "strategy")

    def test_symbol_mismatch(self):
        self._assert_reject(validate_external_signal(_signal(symbol="ETHUSDT"), _ctx()), "symbol")

    def test_timeframe_mismatch(self):
        self._assert_reject(validate_external_signal(_signal(timeframe="1h"), _ctx()), "timeframe")

    def test_event_not_allowed(self):
        goal = {**GOAL, "allowed_external_strategies": [{**GOAL["allowed_external_strategies"][0], "events": ["EXIT"]}]}
        self._assert_reject(validate_external_signal(_signal(event="BUY_CANDIDATE"), _ctx(goal=goal)), "event")

    def test_already_processed_duplicate(self):
        self._assert_reject(validate_external_signal(_signal(), _ctx(already_processed=True)), "duplicate")

    def test_bar_not_aligned(self):
        self._assert_reject(validate_external_signal(_signal(bar_time=BAR_MS + 1), _ctx()), "bar_closed", "not aligned")

    def test_bar_not_closed_yet(self):
        result = validate_external_signal(_signal(), _ctx(now_ms=BAR_MS + 1))
        self._assert_reject(result, "bar_closed", "not closed")

    def test_live_mode_rejected(self):
        self._assert_reject(validate_external_signal(_signal(), _ctx(trading_mode="live")), "mode", "live")

    def test_price_offline_rejected(self):
        self._assert_reject(validate_external_signal(_signal(), _ctx(price_offline=True)), "price_source")

    def test_position_already_open(self):
        result = validate_external_signal(_signal(), _ctx(open_position={"asset": "BTC/USDT"}))
        self._assert_reject(result, "position", "already open")

    def test_exit_without_position(self):
        self._assert_reject(validate_external_signal(_signal(event="EXIT"), _ctx()), "position", "no open position")

    def test_short_without_allow_short(self):
        self._assert_reject(validate_external_signal(_signal(event="SELL_CANDIDATE"), _ctx()), "position", "short")

    def test_guardrail_halts_entry(self):
        # net -550 on 10k = -5.5% return -> drawdown 0.055 -> halt_entries.
        result = validate_external_signal(_signal(), _ctx(recent_trades=({"net_pnl_usd": -550.0},)))
        self._assert_reject(result, "guardrail")
        self.assertEqual(result.guardrail, "halt_entries")

    def test_exit_not_blocked_by_guardrail(self):
        # Same drawdown, but EXIT must still pass (you can always close).
        result = validate_external_signal(
            _signal(event="EXIT"),
            _ctx(recent_trades=({"net_pnl_usd": -550.0},), open_position={"asset": "BTC/USDT"}),
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.guardrail, "halt_entries")


class OrderingTests(unittest.TestCase):
    def test_first_failing_gate_wins(self):
        # Unknown strategy AND offline price: strategy (gate 2) is checked first.
        result = validate_external_signal(
            _signal(strategy="nope"),
            _ctx(price_offline=True),
        )
        self.assertEqual(result.check, "strategy")


if __name__ == "__main__":
    unittest.main()
