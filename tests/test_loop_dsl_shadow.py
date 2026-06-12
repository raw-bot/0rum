"""Migration v03 -> DSL and decision behaviour of the migrated RSI config.

Phase 2 ran the DSL in shadow next to the legacy path; since the phase 3
switchover the DSL is the only decision path, so these tests pin the migrated
config's behaviour on unambiguous markets (the live shadow run validated the
threshold-straddling cases before the legacy path was removed)."""

import unittest

from hermes_trading.dsl.migrate import (
    is_dsl_strategy,
    legacy_to_dsl_groups,
    migrate_strategy_file,
    risk_value,
    strategy_dsl_groups,
)
from hermes_trading.dsl.schema import validate_strategy_dsl
from hermes_trading.loop import entry_signal_fired, exit_signal_fired, market_candles, signal_id

LEGACY_STRATEGY = {
    "version": "03",
    "entry": {"indicator": "rsi", "threshold": 25.0, "direction": "long"},
    "stop_loss_pct": 2.0,
    "take_profit_pct": 3.0,
    "max_hold_candles": 30,
    "exit_rsi_threshold": 60.0,
    "position_size_r": 0.5,
}


def _market(closes, source="binance_public"):
    return {"closes": closes, "last_candle_ts": 60_000 * len(closes), "source": source}


class MigrationTests(unittest.TestCase):
    def test_migrated_groups_pass_dsl_validation(self):
        groups = legacy_to_dsl_groups(LEGACY_STRATEGY)
        validate_strategy_dsl(groups["entry"], groups["exit"])

    def test_migrated_groups_pin_the_legacy_thresholds(self):
        groups = legacy_to_dsl_groups(LEGACY_STRATEGY)
        entry = groups["entry"]["conditions"][0]
        exit_condition = groups["exit"]["conditions"][0]
        self.assertEqual((entry["indicator"], entry["operator"], entry["value"]), ("rsi", "<=", 25.0))
        self.assertEqual((exit_condition["operator"], exit_condition["value"]), (">=", 60.0))

    def test_full_file_migration_moves_risk_out_of_the_mutable_groups(self):
        migrated = migrate_strategy_file(LEGACY_STRATEGY)
        self.assertTrue(is_dsl_strategy(migrated))
        self.assertEqual(migrated["risk"]["stop_loss_pct"], 2.0)
        self.assertEqual(migrated["direction"], "long")
        validate_strategy_dsl(migrated["entry"], migrated["exit"])
        for group in (migrated["entry"], migrated["exit"]):
            for condition in group["conditions"]:
                self.assertNotIn("stop_loss_pct", condition)
                self.assertNotIn("position_size_r", condition)

    def test_migration_is_idempotent(self):
        migrated = migrate_strategy_file(LEGACY_STRATEGY)
        self.assertIs(migrate_strategy_file(migrated), migrated)

    def test_risk_value_reads_both_layouts(self):
        self.assertEqual(risk_value(LEGACY_STRATEGY, "stop_loss_pct", 9.9), 2.0)
        self.assertEqual(risk_value(migrate_strategy_file(LEGACY_STRATEGY), "stop_loss_pct", 9.9), 2.0)
        self.assertEqual(risk_value({}, "stop_loss_pct", 9.9), 9.9)

    def test_dsl_strategy_groups_are_used_verbatim(self):
        migrated = migrate_strategy_file(LEGACY_STRATEGY)
        groups = strategy_dsl_groups(migrated)
        self.assertIs(groups["entry"], migrated["entry"])


class MigratedConfigDecisionTests(unittest.TestCase):
    def test_deep_oversold_fires_entry(self):
        closes = [100.0 - index * 0.5 for index in range(20)]
        for strategy in (LEGACY_STRATEGY, migrate_strategy_file(LEGACY_STRATEGY)):
            result = entry_signal_fired(strategy, market_candles(_market(closes)), _market(closes))
            self.assertTrue(result["triggered"])
            self.assertEqual(result["errors"], [])

    def test_strong_rally_fires_exit_not_entry(self):
        closes = [100.0 + index * 0.5 for index in range(20)]
        market = _market(closes)
        candles = market_candles(market)
        strategy = migrate_strategy_file(LEGACY_STRATEGY)
        self.assertFalse(entry_signal_fired(strategy, candles, market)["triggered"])
        self.assertTrue(exit_signal_fired(strategy, candles)["triggered"])

    def test_offline_market_freezes_entry(self):
        closes = [100.0 - index * 0.5 for index in range(20)]
        market = _market(closes, source="offline_fallback")
        result = entry_signal_fired(migrate_strategy_file(LEGACY_STRATEGY), market_candles(market), market)
        self.assertFalse(result["triggered"])

    def test_insufficient_candles_surface_as_errors_and_block_entry(self):
        market = _market([100.0, 99.0, 98.0])
        result = entry_signal_fired(migrate_strategy_file(LEGACY_STRATEGY), market_candles(market), market)
        self.assertFalse(result["triggered"])
        self.assertTrue(result["errors"])

    def test_market_candles_synthesizes_from_closes_when_absent(self):
        candles = market_candles(_market([100.0, 101.0]))
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[-1]["close"], 101.0)
        real = {"candles": [{"ts": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]}
        self.assertIs(market_candles(real), real["candles"])


class CanonicalSignalIdTests(unittest.TestCase):
    def test_signal_id_is_stable_across_key_order_and_layout(self):
        market = _market([100.0, 101.0])
        legacy_id = signal_id("BTC/USDT", LEGACY_STRATEGY, market)
        migrated_id = signal_id("BTC/USDT", migrate_strategy_file(LEGACY_STRATEGY), market)
        # Same version + same canonical entry group => same hash, whatever
        # the on-disk layout or key order.
        self.assertEqual(legacy_id, migrated_id)

    def test_signal_id_changes_with_entry_structure_version_and_candle(self):
        market = _market([100.0, 101.0])
        base = signal_id("BTC/USDT", LEGACY_STRATEGY, market)

        retuned = migrate_strategy_file(LEGACY_STRATEGY)
        retuned["entry"]["conditions"][0]["value"] = 20.0
        self.assertNotEqual(signal_id("BTC/USDT", retuned, market), base)

        rebumped = dict(LEGACY_STRATEGY, version="04")
        self.assertNotEqual(signal_id("BTC/USDT", rebumped, market), base)

        self.assertNotEqual(signal_id("BTC/USDT", LEGACY_STRATEGY, _market([100.0, 101.0, 102.0])), base)


if __name__ == "__main__":
    unittest.main()
