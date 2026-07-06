import tempfile
import unittest
from pathlib import Path

from orum.external import (
    ExternalOrchestrator,
    ExternalSignalStatus,
    ExternalSignalStore,
    signal_source,
)

BAR = 1781424000000          # aligned to 15m grid
BAR_NEXT = BAR + 900_000     # next 15m bar
FUTURE_MS = 2_000_000_000_000  # well past any bar close

STRATEGY = {
    "version": "01",
    "position_size_r": 0.5,
    "stop_loss_pct": 2.0,
    "take_profit_pct": 3.0,
    "max_hold_candles": 30,
    "fee_rate": 0.0004,
}

GOAL = {
    "signal_source": "tradingview_external",
    "starting_balance_usd": 10000.0,
    "max_drawdown": 0.05,
    "emergency_stop_drawdown": 0.06,
    "allowed_external_strategies": [
        {"id": "ema_momentum_v3", "symbol": "BTCUSDT", "timeframe": "15m",
         "events": ["BUY_CANDIDATE", "SELL_CANDIDATE", "EXIT"]},
    ],
}


def _payload(**overrides):
    payload = {
        "source": "tradingview",
        "strategy": "ema_momentum_v3",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "event": "BUY_CANDIDATE",
        "bar_time": BAR,
        "price": 100.0,
        "version": "tv_ema_momentum_v3",
    }
    payload.update(overrides)
    return payload


class FakeState:
    def __init__(self, *, position=None, trades=None, resume_ack=False, mode="paper", offline=False):
        self.strategy = dict(STRATEGY)
        self.position = position
        self.trades = list(trades or [])
        self._resume_ack = resume_ack
        self._mode = mode
        self._offline = offline

    def load_strategy(self):
        return self.strategy

    def load_position(self):
        return self.position

    def save_position(self, position):
        self.position = position

    def clear_position(self):
        self.position = None

    def append_trade(self, trade):
        self.trades.append(trade)

    def trade_history(self):
        return list(self.trades)

    def resume_ack(self):
        return self._resume_ack

    def trading_mode(self):
        return self._mode

    def price_offline(self):
        return self._offline

    def now_ms(self):
        return FUTURE_MS


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, kind, detail, **fields):
        self.calls.append((kind, detail, fields))
        return {"kind": kind}

    def kinds(self):
        return [k for k, _d, _f in self.calls]


class OrchestratorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store_path = Path(self._tmp.name) / "external_signals.jsonl"
        self.log = _Recorder()

    def tearDown(self):
        self._tmp.cleanup()

    def _orch(self, state):
        store = ExternalSignalStore(path=self.store_path, logger=self.log)
        return ExternalOrchestrator(goal=GOAL, state=state, store=store, logger=self.log)


class ExecutionTests(OrchestratorTestBase):
    def test_buy_opens_position(self):
        state = FakeState()
        outcome = self._orch(state).handle(_payload())
        self.assertEqual(outcome.status, ExternalSignalStatus.EXECUTED)
        self.assertTrue(outcome.executed)
        self.assertEqual(outcome.detail, "opened")
        self.assertIsNotNone(state.position)
        self.assertEqual(state.position["entry_price"], 100.0)
        self.assertIn("external_signal_id", state.position)
        self.assertIn("external_signal_executed", self.log.kinds())

    def test_opened_position_carries_strategy_candle_interval(self):
        # 15m strategy -> 900_000 ms, so _held_candles counts 15m bars, not 1m.
        state = FakeState()
        self._orch(state).handle(_payload())
        self.assertEqual(state.position["candle_interval_ms"], 900_000)

    def test_without_engine_asset_position_keeps_tv_symbol(self):
        # No engine_asset mapping -> position labelled with the TV ticker.
        state = FakeState()
        self._orch(state).handle(_payload())
        self.assertEqual(state.position["asset"], "BTCUSDT")

    def test_engine_asset_mapping_relabels_position_to_engine_venue(self):
        # engine_asset remaps so the worker's risk tick prices on its own venue.
        goal = {**GOAL, "allowed_external_strategies": [
            {"id": "ema_momentum_v3", "symbol": "BTCUSDT", "engine_asset": "BTC/USDT",
             "timeframe": "15m", "events": ["BUY_CANDIDATE", "EXIT"]},
        ]}
        state = FakeState()
        store = ExternalSignalStore(path=self.store_path, logger=self.log)
        orch = ExternalOrchestrator(goal=goal, state=state, store=store, logger=self.log)
        orch.handle(_payload())
        self.assertEqual(state.position["asset"], "BTC/USDT")

    def test_full_lifecycle_open_then_exit(self):
        state = FakeState()
        orch = self._orch(state)
        orch.handle(_payload())  # open at 100
        self.assertIsNotNone(state.position)

        outcome = orch.handle(_payload(event="EXIT", bar_time=BAR_NEXT, price=110.0))
        self.assertEqual(outcome.status, ExternalSignalStatus.EXECUTED)
        self.assertEqual(outcome.detail, "closed")
        self.assertIsNone(state.position)
        self.assertEqual(len(state.trades), 1)
        trade = state.trades[0]
        self.assertEqual(trade["exit_reason"], "external_exit")
        self.assertEqual(trade["exit_price"], 110.0)
        self.assertIn("external_signal_id", trade)
        self.assertIn("balance_before_usd", trade)
        self.assertIn("account_return", trade)

    def test_short_executes_when_allowed(self):
        goal = {**GOAL, "allow_short": True}
        state = FakeState()
        store = ExternalSignalStore(path=self.store_path, logger=self.log)
        orch = ExternalOrchestrator(goal=goal, state=state, store=store, logger=self.log)
        outcome = orch.handle(_payload(event="SELL_CANDIDATE"))
        self.assertTrue(outcome.executed)


class StopTheFlowTests(OrchestratorTestBase):
    def test_duplicate_is_not_executed(self):
        state = FakeState()
        orch = self._orch(state)
        orch.handle(_payload())
        state.position = None  # pretend it closed; identity still seen
        outcome = orch.handle(_payload())  # same identity
        self.assertEqual(outcome.status, ExternalSignalStatus.DUPLICATE)
        self.assertEqual(outcome.stage, "ingest")
        self.assertIsNone(state.position)

    def test_malformed_is_not_executed(self):
        state = FakeState()
        outcome = self._orch(state).handle(_payload(bar_time=1781424000))  # seconds
        self.assertEqual(outcome.status, ExternalSignalStatus.REJECTED)
        self.assertEqual(outcome.stage, "ingest")

    def test_unknown_strategy_rejected_at_validation(self):
        state = FakeState()
        outcome = self._orch(state).handle(_payload(strategy="nope"))
        self.assertEqual(outcome.status, ExternalSignalStatus.REJECTED)
        self.assertEqual(outcome.stage, "validate")
        self.assertIsNone(state.position)  # nothing executed
        self.assertIn("external_signal_rejected", self.log.kinds())

    def test_guardrail_blocks_open(self):
        state = FakeState(trades=[{"net_pnl_usd": -550.0}])  # drawdown 5.5% -> halt_entries
        outcome = self._orch(state).handle(_payload())
        self.assertEqual(outcome.status, ExternalSignalStatus.REJECTED)
        self.assertEqual(outcome.stage, "validate")
        self.assertIsNone(state.position)

    def test_live_mode_blocks_execution(self):
        state = FakeState(mode="live")
        outcome = self._orch(state).handle(_payload())
        self.assertEqual(outcome.status, ExternalSignalStatus.REJECTED)
        self.assertEqual(outcome.stage, "validate")

    def test_exit_without_position_rejected(self):
        state = FakeState()
        outcome = self._orch(state).handle(_payload(event="EXIT"))
        self.assertEqual(outcome.status, ExternalSignalStatus.REJECTED)
        self.assertEqual(len(state.trades), 0)


class SignalSourceTests(unittest.TestCase):
    def test_default_is_native(self):
        self.assertEqual(signal_source({}), "native")

    def test_external_recognized(self):
        self.assertEqual(signal_source({"signal_source": "tradingview_external"}), "tradingview_external")

    def test_unknown_falls_back_to_native(self):
        self.assertEqual(signal_source({"signal_source": "garbage"}), "native")


if __name__ == "__main__":
    unittest.main()
