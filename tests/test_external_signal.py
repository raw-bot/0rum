import json
import unittest
from datetime import UTC, datetime

from hermes_trading.external import (
    ExternalSignal,
    ExternalSignalError,
    ExternalSignalEvent,
    ExternalSignalSource,
    ExternalSignalStatus,
    parse_external_signal,
)

FIXED_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


def _payload(**overrides):
    payload = {
        "source": "tradingview",
        "strategy": "ema_momentum_v3",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "event": "BUY_CANDIDATE",
        "bar_time": 1781424000000,  # milliseconds
        "price": 68200.5,
        "version": "tv_ema_momentum_v3",
    }
    payload.update(overrides)
    for key, value in list(overrides.items()):
        if value is _DELETE:
            del payload[key]
    return payload


_DELETE = object()


class ParseAcceptanceTests(unittest.TestCase):
    def test_valid_payload_parses(self):
        signal = parse_external_signal(_payload(), now=FIXED_NOW)
        self.assertEqual(signal.source, "tradingview")
        self.assertEqual(signal.strategy, "ema_momentum_v3")
        self.assertEqual(signal.symbol, "BTCUSDT")
        self.assertEqual(signal.timeframe, "15m")
        self.assertEqual(signal.event, "BUY_CANDIDATE")
        self.assertEqual(signal.bar_time, 1781424000000)
        self.assertEqual(signal.price, 68200.5)
        self.assertEqual(signal.version, "tv_ema_momentum_v3")
        self.assertEqual(signal.received_at, FIXED_NOW.isoformat())

    def test_json_string_input(self):
        signal = parse_external_signal(json.dumps(_payload()), now=FIXED_NOW)
        self.assertEqual(signal.symbol, "BTCUSDT")

    def test_version_optional(self):
        signal = parse_external_signal(_payload(version=_DELETE), now=FIXED_NOW)
        self.assertIsNone(signal.version)

    def test_integral_float_bar_time_accepted(self):
        signal = parse_external_signal(_payload(bar_time=1781424000000.0), now=FIXED_NOW)
        self.assertEqual(signal.bar_time, 1781424000000)
        self.assertIsInstance(signal.bar_time, int)

    def test_raw_payload_preserved(self):
        payload = _payload(extra_field="kept")
        signal = parse_external_signal(payload, now=FIXED_NOW)
        self.assertEqual(signal.raw["extra_field"], "kept")

    def test_frozen_immutable(self):
        signal = parse_external_signal(_payload(), now=FIXED_NOW)
        with self.assertRaises(Exception):
            signal.price = 1.0  # type: ignore[misc]


class DedupTests(unittest.TestCase):
    def test_dedup_key_composite(self):
        signal = parse_external_signal(_payload(), now=FIXED_NOW)
        self.assertEqual(
            signal.dedup_key(),
            "tradingview|ema_momentum_v3|BTCUSDT|15m|BUY_CANDIDATE|1781424000000",
        )

    def test_dedup_hash_stable_and_short(self):
        a = parse_external_signal(_payload(), now=FIXED_NOW)
        b = parse_external_signal(_payload(price=70000.0), now=datetime(2027, 1, 1, tzinfo=UTC))
        # Same identity fields -> same hash even if price / received_at differ.
        self.assertEqual(a.dedup_hash(), b.dedup_hash())
        self.assertEqual(len(a.dedup_hash()), 16)

    def test_different_event_changes_hash(self):
        a = parse_external_signal(_payload(), now=FIXED_NOW)
        b = parse_external_signal(_payload(event="SELL_CANDIDATE"), now=FIXED_NOW)
        self.assertNotEqual(a.dedup_hash(), b.dedup_hash())

    def test_different_bar_time_changes_hash(self):
        a = parse_external_signal(_payload(), now=FIXED_NOW)
        b = parse_external_signal(_payload(bar_time=1781424900000), now=FIXED_NOW)
        self.assertNotEqual(a.dedup_hash(), b.dedup_hash())


class ToRecordTests(unittest.TestCase):
    def test_record_is_json_serializable_with_hash(self):
        signal = parse_external_signal(_payload(), now=FIXED_NOW)
        record = signal.to_record()
        self.assertEqual(record["dedup_hash"], signal.dedup_hash())
        # Round-trips through JSON cleanly.
        json.loads(json.dumps(record, sort_keys=True))


class ParseRejectionTests(unittest.TestCase):
    def _assert_rejects(self, payload, needle):
        with self.assertRaises(ExternalSignalError) as ctx:
            parse_external_signal(payload, now=FIXED_NOW)
        joined = "; ".join(ctx.exception.errors)
        self.assertIn(needle, joined)

    def test_not_json(self):
        self._assert_rejects("{not json", "not valid JSON")

    def test_non_object_payload(self):
        self._assert_rejects(json.dumps([1, 2, 3]), "must be a JSON object")

    def test_missing_each_required_field(self):
        for name in ("source", "strategy", "symbol", "timeframe", "event", "bar_time", "price"):
            with self.subTest(field=name):
                self._assert_rejects(_payload(**{name: _DELETE}), f"missing required field {name!r}")

    def test_unknown_source(self):
        self._assert_rejects(_payload(source="metatrader"), "not in")

    def test_unknown_event(self):
        self._assert_rejects(_payload(event="MOON"), "not in")

    def test_bar_time_in_seconds_rejected(self):
        self._assert_rejects(_payload(bar_time=1781424000), "looks like seconds")

    def test_bar_time_non_integer(self):
        self._assert_rejects(_payload(bar_time=1781424000000.5), "must be an integer")

    def test_bar_time_bool_rejected(self):
        self._assert_rejects(_payload(bar_time=True), "must be an integer")

    def test_price_non_positive(self):
        self._assert_rejects(_payload(price=0), "must be positive")

    def test_price_non_numeric(self):
        self._assert_rejects(_payload(price="68200"), "must be a number")

    def test_price_bool_rejected(self):
        self._assert_rejects(_payload(price=True), "must be a number")

    def test_empty_strategy(self):
        self._assert_rejects(_payload(strategy="  "), "non-empty string")

    def test_version_wrong_type(self):
        self._assert_rejects(_payload(version=123), "version must be a string")

    def test_all_errors_collected_at_once(self):
        with self.assertRaises(ExternalSignalError) as ctx:
            parse_external_signal(_payload(source="x", event="y", price=-1), now=FIXED_NOW)
        self.assertGreaterEqual(len(ctx.exception.errors), 3)


class EnumTests(unittest.TestCase):
    def test_source_values(self):
        self.assertEqual(ExternalSignalSource.TRADINGVIEW.value, "tradingview")

    def test_event_values(self):
        self.assertEqual(
            {member.value for member in ExternalSignalEvent},
            {"BUY_CANDIDATE", "SELL_CANDIDATE", "EXIT"},
        )

    def test_status_values(self):
        self.assertEqual(
            {member.value for member in ExternalSignalStatus},
            {"received", "validated", "accepted", "executed", "rejected", "duplicate"},
        )


if __name__ == "__main__":
    unittest.main()
