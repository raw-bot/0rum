"""Bridge tests: live paper execution + stale-before-dedup ordering.

The live test would have caught the FileStateAccess import bug (it exercises the
real open path). The stale test pins the fix that a frozen old payload keeps
surfacing as "stale" instead of being masked as "duplicate".
"""

import json
import tempfile
import unittest
from pathlib import Path

from orum.external.bridge import AkMacdBridge, STUDY_NAME
from orum.external.ingest import ExternalSignalStore
from orum.external.orchestrator import ExternalOrchestrator

TF_MS = 900_000
BAR = 1_781_625_600_000          # aligned to the 15m grid
FRESH_NOW = BAR + TF_MS + 2_000  # just after the bar closed

GOAL = {
    "signal_source": "tradingview_external",
    "starting_balance_usd": 10_000.0,
    "max_drawdown": 0.05,
    "emergency_stop_drawdown": 0.06,
    "allowed_external_sources": ["tradingview"],
    "allowed_external_strategies": [
        {"id": "ak_macd_15m_v1", "symbol": "BTCUSD", "engine_asset": "BTC/USDT",
         "timeframe": "15m", "events": ["BUY_CANDIDATE", "EXIT"]},
    ],
}

STRATEGY = {"version": "01", "risk": {"position_size_r": 0.5, "stop_loss_pct": 2.0,
                                      "take_profit_pct": 3.0, "max_hold_candles": 30, "fee_rate": 0.0}}


def _payload(**over):
    p = {
        "source": "tradingview", "strategy": "ak_macd_15m_v1", "symbol": "BTCUSD",
        "timeframe": "15m", "event": "BUY_CANDIDATE", "bar_time": BAR, "price": 100.0,
        "version": "tv_ak_macd_15m_v1",
        "baseline_at_entry": 98.0, "recent_low": 97.0, "recent_high": 103.0,
    }
    p.update(over)
    return p


def reader_for(payload):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    resp = {"success": True, "studies": [{"name": STUDY_NAME, "tables": [{"rows": [f"payload | {raw}"]}]}]}
    return lambda: resp


class FakeState:
    def __init__(self):
        self.position = None
        self.trades = []
        self.strategy = dict(STRATEGY)

    def load_strategy(self): return self.strategy
    def load_position(self): return self.position
    def save_position(self, p): self.position = p
    def clear_position(self): self.position = None
    def append_trade(self, t): self.trades.append(t)
    def trade_history(self): return list(self.trades)
    def resume_ack(self): return False
    def trading_mode(self): return "paper"
    def price_offline(self): return False
    def now_ms(self): return FRESH_NOW


def _state_loader():
    return {"open_position": None, "recent_trades": (), "resume_ack": False,
            "trading_mode": "paper", "price_offline": False}


class TestBridgeLivePaper(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_accepted_signal_opens_paper_position_with_bracket(self):
        state = FakeState()
        orch = ExternalOrchestrator(
            goal=GOAL, state=state,
            store=ExternalSignalStore(path=self.tmp / "ext.jsonl"),
        )
        bridge = AkMacdBridge(
            reader=reader_for(_payload()), shadow=False, orchestrator=orch,
            goal_loader=lambda: GOAL, state_loader=_state_loader,
            log_path=self.tmp / "bridge.jsonl", now_ms=FRESH_NOW, printer=lambda _m: None,
        )
        v = bridge.poll_once()
        self.assertEqual(v.action, "executed")
        self.assertIsNotNone(state.position)
        self.assertEqual(state.position["exit_mode"], "bracket")
        # SL = min(baseline 98, recent_low 97) = 97 ; TP = 100 + 1.5*(100-97) = 104.5
        self.assertEqual(state.position["stop_loss_price"], 97.0)
        self.assertEqual(state.position["take_profit_price"], 104.5)
        # Risk-based sizing: qty * risk_distance == risk_usd (0.5% of 10k = 50).
        self.assertAlmostEqual(state.position["qty_base"] * state.position["risk_distance"], 50.0)


class TestStaleBeforeDedup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "bridge.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_stale_signal_stays_stale_not_duplicate(self):
        # bar_time many bars in the past -> stale. Polling it twice must report
        # "stale" both times (not "duplicate" on the 2nd) -> stale isn't masked.
        old_bar = BAR
        now = BAR + 20 * TF_MS  # 20 bars later, well past max_stale_bars
        bridge = AkMacdBridge(
            reader=reader_for(_payload(bar_time=old_bar)), shadow=True,
            goal_loader=lambda: GOAL, state_loader=_state_loader,
            log_path=self.log, now_ms=now, printer=lambda _m: None,
        )
        first = bridge.poll_once()
        second = bridge.poll_once()
        self.assertEqual(first.action, "stale")
        self.assertEqual(second.action, "stale")


if __name__ == "__main__":
    unittest.main()
