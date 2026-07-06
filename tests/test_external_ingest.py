import json
import tempfile
import unittest
from pathlib import Path

from orum.external import (
    ExternalSignalStatus,
    ExternalSignalStore,
    parse_external_signal,
)


def _payload(**overrides):
    payload = {
        "source": "tradingview",
        "strategy": "ema_momentum_v3",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "event": "BUY_CANDIDATE",
        "bar_time": 1781424000000,
        "price": 68200.5,
        "version": "tv_ema_momentum_v3",
    }
    payload.update(overrides)
    return payload


class _Recorder:
    """Capturing stand-in for events.log_event."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, kind: str, detail: str, **fields) -> dict:
        self.calls.append((kind, detail, fields))
        return {"kind": kind, "detail": detail, **fields}

    def kinds(self) -> list[str]:
        return [kind for kind, _detail, _fields in self.calls]


class IngestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "external_signals.jsonl"
        self.log = _Recorder()

    def tearDown(self):
        self._tmp.cleanup()

    def _store(self):
        return ExternalSignalStore(path=self.path, logger=self.log)

    def test_first_signal_is_received_and_persisted(self):
        store = self._store()
        result = store.ingest(parse_external_signal(_payload()))
        self.assertEqual(result.status, ExternalSignalStatus.RECEIVED)
        self.assertTrue(result.admitted)
        records = store.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "received")
        self.assertEqual(records[0]["dedup_hash"], result.dedup_hash)
        self.assertEqual(self.log.kinds(), ["external_signal_received"])

    def test_same_identity_is_duplicate(self):
        store = self._store()
        store.ingest(parse_external_signal(_payload()))
        result = store.ingest(parse_external_signal(_payload(price=70000.0)))  # price differs, identity same
        self.assertEqual(result.status, ExternalSignalStatus.DUPLICATE)
        self.assertFalse(result.admitted)
        # Both writes are audited, but only one identity is in the index.
        self.assertEqual(len(store.records()), 2)
        self.assertEqual(len(store._seen), 1)
        self.assertEqual(self.log.kinds(), ["external_signal_received", "external_signal_duplicate"])

    def test_different_bar_time_is_received(self):
        store = self._store()
        store.ingest(parse_external_signal(_payload()))
        result = store.ingest(parse_external_signal(_payload(bar_time=1781424900000)))
        self.assertEqual(result.status, ExternalSignalStatus.RECEIVED)
        self.assertEqual(len(store._seen), 2)

    def test_different_event_is_received(self):
        store = self._store()
        store.ingest(parse_external_signal(_payload()))
        result = store.ingest(parse_external_signal(_payload(event="SELL_CANDIDATE")))
        self.assertEqual(result.status, ExternalSignalStatus.RECEIVED)

    def test_dedup_survives_restart(self):
        first = self._store()
        first.ingest(parse_external_signal(_payload()))
        # New store instance, same file: index is rebuilt from disk.
        second = ExternalSignalStore(path=self.path, logger=self.log)
        result = second.ingest(parse_external_signal(_payload()))
        self.assertEqual(result.status, ExternalSignalStatus.DUPLICATE)

    def test_submit_parses_then_ingests(self):
        store = self._store()
        result = store.submit(_payload())
        self.assertEqual(result.status, ExternalSignalStatus.RECEIVED)
        self.assertIsNotNone(result.signal)

    def test_submit_json_string(self):
        store = self._store()
        result = store.submit(json.dumps(_payload()))
        self.assertEqual(result.status, ExternalSignalStatus.RECEIVED)

    def test_submit_malformed_is_rejected_and_not_persisted(self):
        store = self._store()
        result = store.submit(_payload(bar_time=1781424000))  # seconds, not ms
        self.assertEqual(result.status, ExternalSignalStatus.REJECTED)
        self.assertTrue(result.errors)
        self.assertIsNone(result.signal)
        # Malformed payloads never enter the signals file.
        self.assertEqual(store.records(), [])
        self.assertEqual(self.log.kinds(), ["external_signal_malformed"])

    def test_records_are_sorted_json_lines(self):
        store = self._store()
        store.ingest(parse_external_signal(_payload()))
        raw = self.path.read_text().splitlines()
        self.assertEqual(len(raw), 1)
        # Each line round-trips and carries the lifecycle fields.
        record = json.loads(raw[0])
        self.assertIn("status", record)
        self.assertIn("ingested_at", record)
        self.assertIn("raw", record)

    def test_torn_line_does_not_crash_index_rebuild(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text('{"dedup_hash": "abc"}\n{not valid json\n')
        store = self._store()  # must not raise
        self.assertIn("abc", store._seen)


if __name__ == "__main__":
    unittest.main()
