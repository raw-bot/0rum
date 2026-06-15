import tempfile
import unittest
from pathlib import Path

from hermes_trading.external import (
    ExternalOrchestrator,
    ExternalSignalStatus,
    ExternalSignalStore,
    MirrorPoller,
    extract_mirror_payload,
)

# A payload exactly like the live Mirror emits — note the pipes inside sig_key.
LIVE_PAYLOAD = (
    '{"source":"tradingview","strategy":"hermes_mirror_v1","symbol":"BTCUSD",'
    '"timeframe":"1w","event":"BUY_CANDIDATE","bar_time":1779667200000,'
    '"price":73567.99,"version":"tv_hermes_mirror_v1",'
    '"sig_key":"tradingview|hermes_mirror_v1|BTCUSD|1w|BUY_CANDIDATE|1779667200000"}'
)


def _table_response(payload: str, *, study="Hermes Mirror", success=True):
    return {
        "success": success,
        "studies": [
            {
                "name": study,
                "tables": [
                    {
                        "rows": [
                            "symbol/tf | BTCUSD 1w",
                            "close | 73567.99",
                            "regime | favorable",
                            "last evt | BUY_CANDIDATE",
                            f"payload | {payload}",
                        ]
                    }
                ],
            }
        ],
    }


class ExtractTests(unittest.TestCase):
    def test_extracts_payload_with_internal_pipes(self):
        raw = extract_mirror_payload(_table_response(LIVE_PAYLOAD))
        self.assertEqual(raw, LIVE_PAYLOAD)  # sig_key pipes preserved

    def test_no_signal_yet(self):
        self.assertEqual(extract_mirror_payload(_table_response("none")), "none")

    def test_missing_study_returns_none(self):
        self.assertIsNone(extract_mirror_payload(_table_response(LIVE_PAYLOAD, study="Some Other Study")))

    def test_unsuccessful_response_returns_none(self):
        self.assertIsNone(extract_mirror_payload(_table_response(LIVE_PAYLOAD, success=False)))

    def test_garbage_response_returns_none(self):
        self.assertIsNone(extract_mirror_payload({"oops": True}))
        self.assertIsNone(extract_mirror_payload("not a dict"))


GOAL = {
    "signal_source": "tradingview_external",
    "starting_balance_usd": 10000.0,
    "max_drawdown": 0.05,
    "emergency_stop_drawdown": 0.06,
    "allowed_external_strategies": [
        {"id": "hermes_mirror_v1", "symbol": "BTCUSD", "timeframe": "1w",
         "events": ["BUY_CANDIDATE", "SELL_CANDIDATE", "EXIT"]},
    ],
}


class FakeState:
    def __init__(self):
        self.strategy = {"version": "01", "position_size_r": 0.5, "stop_loss_pct": 2.0,
                         "take_profit_pct": 3.0, "max_hold_candles": 30, "fee_rate": 0.0004}
        self.position = None
        self.trades = []

    def load_strategy(self): return self.strategy
    def load_position(self): return self.position
    def save_position(self, p): self.position = p
    def clear_position(self): self.position = None
    def append_trade(self, t): self.trades.append(t)
    def trade_history(self): return list(self.trades)
    def resume_ack(self): return False
    def trading_mode(self): return "paper"
    def price_offline(self): return False
    def now_ms(self): return 1779667200000 + 604_800_000  # one weekly bar after close


class PollOnceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "external_signals.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _poller(self, reader, state=None):
        store = ExternalSignalStore(path=self.path, logger=lambda *a, **k: {})
        orch = ExternalOrchestrator(goal=GOAL, state=state or FakeState(), store=store,
                                    logger=lambda *a, **k: {})
        return MirrorPoller(orchestrator=orch, reader=reader, logger=lambda *a, **k: {})

    def test_handled_buy_opens_position(self):
        state = FakeState()
        poller = self._poller(lambda: _table_response(LIVE_PAYLOAD), state=state)
        result = poller.poll_once()
        self.assertEqual(result.action, "handled")
        self.assertEqual(result.outcome.status, ExternalSignalStatus.EXECUTED)
        self.assertIsNotNone(state.position)

    def test_no_signal_is_noop(self):
        poller = self._poller(lambda: _table_response("none"))
        result = poller.poll_once()
        self.assertEqual(result.action, "no_signal")
        self.assertIsNone(result.outcome)

    def test_no_table_is_noop(self):
        poller = self._poller(lambda: {"success": False})
        result = poller.poll_once()
        self.assertEqual(result.action, "no_table")

    def test_polling_same_bar_twice_dedups(self):
        state = FakeState()
        poller = self._poller(lambda: _table_response(LIVE_PAYLOAD), state=state)
        first = poller.poll_once()
        second = poller.poll_once()  # same bar_time identity
        self.assertEqual(first.outcome.status, ExternalSignalStatus.EXECUTED)
        self.assertEqual(second.outcome.status, ExternalSignalStatus.DUPLICATE)


if __name__ == "__main__":
    unittest.main()
