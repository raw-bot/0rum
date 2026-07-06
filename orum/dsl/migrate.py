"""Migration of legacy scalar strategies (v03 and earlier) to the DSL format.

The legacy strategy is RSI <= entry.threshold to enter, RSI >= exit_rsi_threshold
to leave; the DSL equivalent pins the same thresholds on rsi(14). Risk fields
move under a `risk:` block that stays OUTSIDE the mutable DSL schema.
"""

from __future__ import annotations

from orum.dsl.schema import DSL_VERSION

# The legacy loop._rsi used period 14 implicitly; the migrated DSL pins it.
LEGACY_RSI_PERIOD = 14

RISK_KEYS = ("stop_loss_pct", "take_profit_pct", "max_hold_candles", "position_size_r", "fee_rate")


def is_dsl_strategy(strategy: dict) -> bool:
    return "dsl_version" in strategy


def legacy_to_dsl_groups(strategy: dict) -> dict:
    """Derive the entry/exit condition groups equivalent to a legacy strategy."""
    entry_threshold = float(strategy.get("entry", {}).get("threshold", 30))
    exit_threshold = float(strategy.get("exit_rsi_threshold", 55))
    return {
        "entry": {
            "logic": "AND",
            "conditions": [
                {
                    "indicator": "rsi",
                    "params": {"period": LEGACY_RSI_PERIOD},
                    "operator": "<=",
                    "value": entry_threshold,
                }
            ],
        },
        "exit": {
            "logic": "OR",
            "conditions": [
                {
                    "indicator": "rsi",
                    "params": {"period": LEGACY_RSI_PERIOD},
                    "operator": ">=",
                    "value": exit_threshold,
                }
            ],
        },
    }


def strategy_dsl_groups(strategy: dict) -> dict:
    """Entry/exit groups of a strategy, deriving them for legacy files."""
    if is_dsl_strategy(strategy):
        return {"entry": strategy["entry"], "exit": strategy["exit"]}
    return legacy_to_dsl_groups(strategy)


def risk_value(strategy: dict, key: str, default: float) -> float:
    """Risk field accessor working for both layouts (risk: block vs top level)."""
    risk = strategy.get("risk")
    if isinstance(risk, dict) and key in risk:
        return float(risk[key])
    return float(strategy.get(key, default))


def migrate_strategy_file(strategy: dict) -> dict:
    """Full file migration (phase 3): legacy scalars -> DSL layout on disk."""
    if is_dsl_strategy(strategy):
        return strategy
    groups = legacy_to_dsl_groups(strategy)
    return {
        "version": strategy.get("version", "01"),
        "dsl_version": DSL_VERSION,
        "entry": groups["entry"],
        "exit": groups["exit"],
        "risk": {key: strategy[key] for key in RISK_KEYS if key in strategy},
        "direction": strategy.get("entry", {}).get("direction", "long"),
    }
