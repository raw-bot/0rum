import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_trading import events


class EventLogTests(unittest.TestCase):
    def test_event_is_appended_as_jsonl_with_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            with patch.object(events, "EVENTS_PATH", path):
                events.log_event("price_source_changed", "switched", previous="binance_public", current="offline_fallback")
                events.log_event("trade_closed", "closed", net_pnl_usd=-1.25)

            lines = [json.loads(line) for line in path.read_text().splitlines()]

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["kind"], "price_source_changed")
        self.assertEqual(lines[0]["previous"], "binance_public")
        self.assertIn("ts", lines[0])
        self.assertEqual(lines[1]["net_pnl_usd"], -1.25)

    def test_logging_failure_never_raises(self):
        unwritable = Path("/nonexistent-root-dir/events.jsonl")
        with patch.object(events, "EVENTS_PATH", unwritable):
            record = events.log_event("worker_failure", "boom")

        self.assertEqual(record["kind"], "worker_failure")


if __name__ == "__main__":
    unittest.main()
