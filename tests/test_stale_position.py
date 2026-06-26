import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from hermes_trading import events, loop
from hermes_trading.loop import worker_outage_detected

NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


def _write_heartbeat(state: Path, age: timedelta | None):
    """Write a heartbeat whose ts is `age` in the past (or omit it entirely)."""
    if age is None:
        return
    (state / "heartbeat.json").write_text(json.dumps({"ts": (NOW - age).isoformat()}))


class WorkerOutageTests(unittest.TestCase):
    def test_fresh_heartbeat_is_not_an_outage(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            _write_heartbeat(state, timedelta(seconds=60))  # normal loop cadence
            with patch.object(loop, "HEARTBEAT_PATH", state / "heartbeat.json"):
                self.assertFalse(worker_outage_detected(now=NOW))

    def test_stale_heartbeat_is_an_outage(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            _write_heartbeat(state, timedelta(hours=3))
            with patch.object(loop, "HEARTBEAT_PATH", state / "heartbeat.json"):
                self.assertTrue(worker_outage_detected(now=NOW))

    def test_missing_heartbeat_is_not_an_outage(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            with patch.object(loop, "HEARTBEAT_PATH", state / "heartbeat.json"):
                self.assertFalse(worker_outage_detected(now=NOW))


class LoadOpenPositionTests(unittest.TestCase):
    def _run_load(self, state: Path, position: dict):
        (state / "open_position.json").write_text(json.dumps(position))
        with (
            patch.object(loop, "POSITION_PATH", state / "open_position.json"),
            patch.object(loop, "HEARTBEAT_PATH", state / "heartbeat.json"),
            patch.object(loop, "STATE_DIR", state),
            patch.object(events, "EVENTS_PATH", state / "events.jsonl"),
        ):
            return loop._load_open_position()

    def test_old_position_is_kept_during_continuous_operation(self):
        # THE BUG FIX: a position open for 9 days is still VALID as long as the
        # worker has been running (fresh heartbeat) -- never discarded on age.
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            _write_heartbeat(state, timedelta(seconds=60))
            position = {"asset": "BTC/USDT", "direction": "short", "entry_price": 60077.99,
                        "opened_at": (datetime.now(UTC) - timedelta(days=9)).isoformat()}
            loaded = self._run_load(state, position)
            self.assertIsNotNone(loaded)                          # kept, not discarded
            self.assertTrue((state / "open_position.json").exists())
            self.assertFalse((state / "position_quarantine.jsonl").exists())

    def test_position_is_resumed_not_discarded_after_outage(self):
        # After a real outage (stale heartbeat) the position is RESUMED (returned,
        # file kept) and an event is logged -- it is never thrown away.
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            _write_heartbeat(state, timedelta(hours=3))
            position = {"asset": "BTC/USDT", "direction": "short", "entry_price": 60077.99,
                        "opened_at": (datetime.now(UTC) - timedelta(hours=4)).isoformat()}
            loaded = self._run_load(state, position)
            self.assertIsNotNone(loaded)                          # resumed, not discarded
            self.assertTrue((state / "open_position.json").exists())
            self.assertFalse((state / "position_quarantine.jsonl").exists())
            events_logged = (state / "events.jsonl").read_text()
            self.assertIn("position_resumed_after_outage", events_logged)

    def test_no_position_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            _write_heartbeat(state, timedelta(seconds=60))
            with (
                patch.object(loop, "POSITION_PATH", state / "open_position.json"),
                patch.object(loop, "HEARTBEAT_PATH", state / "heartbeat.json"),
            ):
                self.assertIsNone(loop._load_open_position())


if __name__ == "__main__":
    unittest.main()
