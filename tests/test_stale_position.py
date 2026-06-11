import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from hermes_trading import loop
from hermes_trading.loop import position_is_stale

NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


class StalePositionTests(unittest.TestCase):
    def test_fresh_position_is_not_stale(self):
        position = {"opened_at": (NOW - timedelta(minutes=30)).isoformat()}

        self.assertFalse(position_is_stale(position, now=NOW, max_age_hours=6))

    def test_position_older_than_max_age_is_stale(self):
        position = {"opened_at": (NOW - timedelta(days=9)).isoformat()}

        self.assertTrue(position_is_stale(position, now=NOW, max_age_hours=6))

    def test_position_without_opened_at_is_stale(self):
        self.assertTrue(position_is_stale({}, now=NOW, max_age_hours=6))
        self.assertTrue(position_is_stale({"opened_at": "not-a-date"}, now=NOW, max_age_hours=6))

    def test_load_open_position_quarantines_stale_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            position_path = state / "open_position.json"
            position = {
                "asset": "BTC/USDT",
                "entry_price": 67915.96,
                "opened_at": (datetime.now(UTC) - timedelta(days=9)).isoformat(),
            }
            position_path.write_text(json.dumps(position))

            with (
                patch.object(loop, "POSITION_PATH", position_path),
                patch.object(loop, "STATE_DIR", state),
            ):
                loaded = loop._load_open_position()

            self.assertIsNone(loaded)
            self.assertFalse(position_path.exists())
            quarantine = state / "position_quarantine.jsonl"
            self.assertTrue(quarantine.exists())
            record = json.loads(quarantine.read_text().splitlines()[0])
            self.assertEqual(record["asset"], "BTC/USDT")
            self.assertIn("quarantine_reason", record)

    def test_load_open_position_keeps_fresh_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            position_path = state / "open_position.json"
            position = {
                "asset": "BTC/USDT",
                "entry_price": 67915.96,
                "opened_at": datetime.now(UTC).isoformat(),
            }
            position_path.write_text(json.dumps(position))

            with (
                patch.object(loop, "POSITION_PATH", position_path),
                patch.object(loop, "STATE_DIR", state),
            ):
                loaded = loop._load_open_position()

            self.assertIsNotNone(loaded)
            self.assertTrue(position_path.exists())


if __name__ == "__main__":
    unittest.main()
