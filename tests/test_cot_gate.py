import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orum.strategies.cot_gate import read_gate

NOW = datetime(2026, 7, 8, tzinfo=timezone.utc)


def _valid(**overrides) -> dict:
    gate = {
        "report_date": "2026-06-30",
        "usable_from": "2026-07-03",
        "cot_index": 53.9,
        "gate_on": False,
        "threshold": 20.0,
        "updated_at": "2026-07-08T00:00:00+00:00",
    }
    gate.update(overrides)
    return gate


class CotGateReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "cot_gate.json"

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _write(self, obj) -> None:
        self.path.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)

    def test_valid_gate_parses(self):
        self._write(_valid(gate_on=True, cot_index=12.0))
        gate = read_gate(self.path, now=NOW)
        self.assertIsNotNone(gate)
        self.assertTrue(gate.gate_on)
        self.assertEqual(gate.cot_index, 12.0)
        self.assertEqual(gate.report_date, "2026-06-30")

    def test_missing_file_is_none(self):
        self.assertIsNone(read_gate(self.path, now=NOW))

    def test_invalid_json_is_none(self):
        self._write("{not json")
        self.assertIsNone(read_gate(self.path, now=NOW))

    def test_missing_report_date_is_none(self):
        obj = _valid()
        del obj["report_date"]
        self._write(obj)
        self.assertIsNone(read_gate(self.path, now=NOW))

    def test_non_bool_gate_on_is_none(self):
        self._write(_valid(gate_on=1))  # int, not bool
        self.assertIsNone(read_gate(self.path, now=NOW))

    def test_non_numeric_cot_index_is_none(self):
        self._write(_valid(cot_index="53.9"))
        self.assertIsNone(read_gate(self.path, now=NOW))

    def test_null_cot_index_is_allowed(self):
        self._write(_valid(cot_index=None, gate_on=False))
        gate = read_gate(self.path, now=NOW)
        self.assertIsNotNone(gate)
        self.assertIsNone(gate.cot_index)

    def test_missing_updated_at_is_none(self):
        obj = _valid()
        del obj["updated_at"]
        self._write(obj)
        self.assertIsNone(read_gate(self.path, now=NOW))

    def test_stale_updated_at_is_none(self):
        old = (NOW - timedelta(days=30)).isoformat()
        self._write(_valid(updated_at=old))
        self.assertIsNone(read_gate(self.path, now=NOW, max_age_days=10))

    def test_fresh_within_window_parses(self):
        recent = (NOW - timedelta(days=3)).isoformat()
        self._write(_valid(updated_at=recent))
        self.assertIsNotNone(read_gate(self.path, now=NOW, max_age_days=10))


if __name__ == "__main__":
    unittest.main()
