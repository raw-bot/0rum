import unittest

from hermes_trading.dsl.migrate import (
    is_dsl_strategy,
    legacy_to_dsl_groups,
    migrate_strategy_file,
    risk_value,
    strategy_dsl_groups,
)
from hermes_trading.dsl.schema import validate_strategy_dsl
from hermes_trading.loop import dsl_shadow_state, market_candles

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


class ShadowEquivalenceTests(unittest.TestCase):
    """Legacy and DSL decisions agree on the migrated RSI config whenever the
    market is unambiguous; threshold-straddling cases are what the live shadow
    phase observes."""

    def test_deep_oversold_fires_both_entries(self):
        closes = [100.0 - index * 0.5 for index in range(20)]
        rsi = 0.0  # legacy _rsi of strictly falling closes
        shadow = dsl_shadow_state(LEGACY_STRATEGY, _market(closes), rsi, position_open=False, exit_rsi=60.0)
        self.assertTrue(shadow["entry_triggered"])
        self.assertEqual(shadow["disagreements"], [])
        self.assertEqual(shadow["errors"], [])

    def test_strong_rally_fires_neither_entry_and_both_exits(self):
        closes = [100.0 + index * 0.5 for index in range(20)]
        rsi = 100.0  # legacy _rsi of strictly rising closes
        shadow = dsl_shadow_state(LEGACY_STRATEGY, _market(closes), rsi, position_open=True, exit_rsi=60.0)
        self.assertFalse(shadow["entry_triggered"])
        self.assertTrue(shadow["exit_triggered"])
        self.assertEqual(shadow["disagreements"], [])

    def test_offline_market_blocks_dsl_entry_like_legacy(self):
        closes = [100.0 - index * 0.5 for index in range(20)]
        shadow = dsl_shadow_state(
            LEGACY_STRATEGY, _market(closes, source="offline_fallback"), 0.0, position_open=False, exit_rsi=60.0
        )
        self.assertFalse(shadow["entry_triggered"])
        self.assertEqual(shadow["disagreements"], [])

    def test_flat_series_disagreement_is_reported_not_hidden(self):
        # Known divergence: legacy RSI returns 100 on a flat series (no
        # losses), TA-Lib/Wilder returns 0 (no gains either). The shadow
        # phase must surface it, not smooth it over.
        closes = [100.0] * 20
        shadow = dsl_shadow_state(LEGACY_STRATEGY, _market(closes), 100.0, position_open=True, exit_rsi=60.0)
        self.assertEqual({item["signal"] for item in shadow["disagreements"]}, {"entry", "exit"})

    def test_insufficient_candles_surface_as_errors(self):
        closes = [100.0, 99.0, 98.0]
        shadow = dsl_shadow_state(LEGACY_STRATEGY, _market(closes), 50.0, position_open=False, exit_rsi=60.0)
        self.assertFalse(shadow["entry_triggered"])
        self.assertTrue(shadow["errors"])

    def test_market_candles_synthesizes_from_closes_when_absent(self):
        candles = market_candles(_market([100.0, 101.0]))
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[-1]["close"], 101.0)
        real = {"candles": [{"ts": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]}
        self.assertIs(market_candles(real), real["candles"])


if __name__ == "__main__":
    unittest.main()
