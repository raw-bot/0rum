import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orum import dashboard


def _write(state: Path, name: str, lines: list[dict]):
    (state / name).write_text("\n".join(json.dumps(item) for item in lines) + "\n")


class DashboardExternalTests(unittest.TestCase):
    def _snapshot(self, *, events, goal, ext=None, position=None):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "goal.yaml").write_text(json.dumps(goal))  # yaml.safe_load reads JSON fine
            _write(state, "events.jsonl", events)
            if ext is not None:
                _write(state, "external_signals.jsonl", ext)
            if position is not None:
                (state / "open_position.json").write_text(json.dumps(position))
            with patch.object(dashboard, "STATE_DIR", state):
                return dashboard.build_snapshot()

    def test_external_feed_counts_and_recent(self):
        events = [
            {"ts": "2026-06-14T10:00:00+00:00", "kind": "external_signal_received", "detail": "BUY", "dedup_hash": "abc"},
            {"ts": "2026-06-14T10:00:01+00:00", "kind": "external_signal_executed", "detail": "opened"},
            {"ts": "2026-06-14T10:01:00+00:00", "kind": "external_signal_rejected", "detail": "halt", "check": "guardrail"},
            {"ts": "2026-06-14T10:02:00+00:00", "kind": "external_signal_duplicate", "detail": "dup"},
            {"ts": "2026-06-14T10:03:00+00:00", "kind": "worker_boot", "detail": "boot"},  # non-external, ignored
        ]
        snap = self._snapshot(events=events, goal={"signal_source": "tradingview_external"})
        ext = snap["external"]
        self.assertEqual(ext["mode"], "tradingview_external")
        self.assertEqual(ext["counts"]["received"], 1)
        self.assertEqual(ext["counts"]["executed"], 1)
        self.assertEqual(ext["counts"]["rejected"], 1)
        self.assertEqual(ext["counts"]["duplicate"], 1)
        self.assertEqual(ext["total"], 4)
        # Newest first, rejection carries its failing check.
        self.assertEqual(ext["recent"][0]["status"], "duplicate")
        rej = next(r for r in ext["recent"] if r["status"] == "rejected")
        self.assertEqual(rej["check"], "guardrail")

    def test_signal_source_defaults_native(self):
        snap = self._snapshot(events=[], goal={})
        self.assertEqual(snap["signal_source"], "native")
        self.assertEqual(snap["external"]["total"], 0)

    def test_price_series_downsamples_and_markers_map_entry_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "goal.yaml").write_text(json.dumps({}))
            candles = [{"ts": 1_780_000_000_000 + i * 60_000, "open": 100 + i * 0.1, "high": 101 + i * 0.1,
                        "low": 99 + i * 0.1, "close": 100 + i * 0.1, "volume": 1.0 + i} for i in range(3000)]
            (state / "candle_history.json").write_text(json.dumps({"asset": "BTC/USDT", "candles": candles}))
            # trade_markers now come from the unified paper ledger (open+close
            # fills), not the retired trades.jsonl. The close fill's ts dates the
            # exit; the matched open fill dates the entry.
            _write(state, "paper_fills.jsonl", [
                {"action": "open", "ts": "2026-06-14T10:00:00+00:00", "strategy_id": "btc_ak_macd_4h",
                 "symbol": "BTC/USDT", "side": "long", "price": 100.0, "qty": 1.0},
                {"action": "close", "ts": "2026-06-14T11:00:00+00:00", "strategy_id": "btc_ak_macd_4h",
                 "symbol": "BTC/USDT", "side": "long", "entry_px": 100.0, "price": 103.0, "qty": 1.0,
                 "realized_pnl_usd": 70.0, "r": 1.0, "reason": "take_profit"},
            ])
            # Stub the live Binance fetch so this test exercises the _price_series
            # fallback it is about — otherwise the real feed wins and leaks live prices.
            with patch.object(dashboard, "_binance_15m_candles", return_value=[]), \
                 patch.object(dashboard, "STATE_DIR", state):
                snap = dashboard.build_snapshot()
        ps = snap["price_series"]
        self.assertLessEqual(len(ps), 1440)  # downsampled from 3000
        self.assertGreater(len(ps), 1)
        self.assertEqual(ps[-1]["close"], candles[-1]["close"])  # last bar kept
        self.assertTrue(all(k in ps[0] for k in ("open", "high", "low", "close", "volume")))  # OHLCV for candles
        m = snap["trade_markers"][0]
        self.assertEqual(m["entry_price"], 100.0)
        self.assertEqual(m["exit_price"], 103.0)
        self.assertTrue(m["win"])
        self.assertEqual(m["exit_ts"], 1_781_434_800_000)  # ms of the close fill ts 2026-06-14T11:00Z

    def test_signal_markers_join_store_with_event_verdict(self):
        # external_signals.jsonl = the structured proposal (bar_time/price/event);
        # events.jsonl = 0rum' verdict. _signal_markers joins them by dedup_hash.
        ext = [
            {"event": "BUY_CANDIDATE", "bar_time": 1781424000000, "price": 100.0, "dedup_hash": "h1", "status": "received"},
            {"event": "EXIT", "bar_time": 1781424900000, "price": 110.0, "dedup_hash": "h2", "status": "received"},
            {"event": "BUY_CANDIDATE", "bar_time": 1781425800000, "price": 105.0, "dedup_hash": "h3", "status": "received"},
            {"event": "BUY_CANDIDATE", "bar_time": 1781426700000, "price": 106.0, "dedup_hash": "h4", "status": "duplicate"},
            {"event": "BUY_CANDIDATE", "bar_time": 0, "price": 0.0, "dedup_hash": "bad", "status": "received"},  # unplottable
        ]
        events = [
            {"ts": "2026-06-14T10:00:01+00:00", "kind": "external_signal_executed", "detail": "opened", "dedup_hash": "h1"},
            {"ts": "2026-06-14T10:01:00+00:00", "kind": "external_signal_rejected", "detail": "already open", "check": "position", "dedup_hash": "h2"},
        ]
        snap = self._snapshot(events=events, goal={"signal_source": "tradingview_external"}, ext=ext)
        sigs = {s["price"]: s for s in snap["signals"]}
        self.assertEqual(len(snap["signals"]), 4)  # the price<=0 / bar_time=0 record is dropped
        self.assertEqual(sigs[100.0]["event"], "BUY")
        self.assertEqual(sigs[100.0]["status"], "executed")  # verdict from events.jsonl
        self.assertEqual(sigs[110.0]["event"], "EXIT")
        self.assertEqual(sigs[110.0]["status"], "rejected")
        self.assertEqual(sigs[110.0]["reason"], "position")
        self.assertEqual(sigs[105.0]["status"], "received")  # no verdict -> proposed only
        self.assertEqual(sigs[106.0]["status"], "duplicate")

    def test_executed_buy_of_open_position_is_marked_opened(self):
        # An executed BUY whose position is still open has no closed trade yet;
        # it must surface as 'opened' (live IN), not be hidden as 'executed'.
        ext = [{"event": "BUY_CANDIDATE", "bar_time": 1781424000000, "price": 100.0, "dedup_hash": "h1", "status": "received"}]
        events = [{"ts": "2026-06-14T10:00:01+00:00", "kind": "external_signal_executed", "detail": "opened", "dedup_hash": "h1"}]
        snap = self._snapshot(
            events=events, goal={"signal_source": "tradingview_external"}, ext=ext,
            position={"external_signal_id": "h1", "entry_price": 100.0, "opened_at": "2026-06-14T10:00:00+00:00"},
        )
        self.assertEqual(snap["signals"][0]["status"], "opened")

    def test_executed_buy_without_open_position_stays_executed(self):
        # Same signal, but no open position (it closed) -> 'executed', hidden on
        # the Pine layer because the closed trade already shows IN/OUT.
        ext = [{"event": "BUY_CANDIDATE", "bar_time": 1781424000000, "price": 100.0, "dedup_hash": "h1", "status": "received"}]
        events = [{"ts": "2026-06-14T10:00:01+00:00", "kind": "external_signal_executed", "detail": "opened", "dedup_hash": "h1"}]
        snap = self._snapshot(events=events, goal={"signal_source": "tradingview_external"}, ext=ext)
        self.assertEqual(snap["signals"][0]["status"], "executed")

    def test_logs_are_newest_first(self):
        events = [
            {"ts": "2026-06-14T10:00:00+00:00", "kind": "worker_boot", "detail": "boot"},
            {"ts": "2026-06-14T10:05:00+00:00", "kind": "trade_closed", "detail": "closed"},
        ]
        snap = self._snapshot(events=events, goal={})
        self.assertEqual(snap["logs"][0]["kind"], "trade_closed")
        self.assertEqual(snap["logs"][1]["kind"], "worker_boot")


if __name__ == "__main__":
    unittest.main()
