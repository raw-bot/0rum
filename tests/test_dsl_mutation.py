"""Full mutation chain: jsonschema -> semantic validation -> one_block_only
diff -> backtest -> apply. Every rejection step is exercised."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from hermes_trading.dsl.diff import group_changes, structural_diff
from hermes_trading.reflect import _apply_dsl_hypothesis, _hermes, _hermes_prompt

GOAL = {
    "starting_balance_usd": 10000.0,
    "target_return_30d": 0.07,
    "max_drawdown": 0.05,
    "min_sharpe": 1.3,
    "one_block_only": True,
}


def _dsl_strategy():
    return {
        "version": "04",
        "dsl_version": 1,
        "entry": {
            "logic": "AND",
            "conditions": [{"indicator": "rsi", "params": {"period": 14}, "operator": "<=", "value": 25.0}],
        },
        "exit": {
            "logic": "OR",
            "conditions": [{"indicator": "rsi", "params": {"period": 14}, "operator": ">=", "value": 60.0}],
        },
        "risk": {"stop_loss_pct": 2.0, "take_profit_pct": 3.0, "max_hold_candles": 30, "position_size_r": 0.5},
        "direction": "long",
    }


def _hypothesis(entry=None, exit_group=None, action="change"):
    strategy = _dsl_strategy()
    return {
        "action": action,
        "proposed_entry": entry if entry is not None else strategy["entry"],
        "proposed_exit": exit_group if exit_group is not None else strategy["exit"],
        "rationale": "losses cluster in unfavorable regime",
        "expected_effect": "hit rate +5pts over 30 trades",
        "issue": "regime_filter",
    }


class StructuralDiffTests(unittest.TestCase):
    def test_modified_condition_counts_one(self):
        old = _dsl_strategy()["entry"]
        new = copy.deepcopy(old)
        new["conditions"][0]["value"] = 20.0
        self.assertEqual(group_changes(old, new), 1)

    def test_added_condition_counts_one(self):
        old = _dsl_strategy()["entry"]
        new = copy.deepcopy(old)
        new["conditions"].append({"indicator": "regime", "operator": "!=", "value_str": "unfavorable"})
        self.assertEqual(group_changes(old, new), 1)

    def test_logic_flip_counts_one(self):
        old = _dsl_strategy()["entry"]
        new = copy.deepcopy(old)
        new["logic"] = "OR"
        self.assertEqual(group_changes(old, new), 1)

    def test_identical_groups_count_zero_whatever_the_key_order(self):
        old = _dsl_strategy()["entry"]
        reordered = json.loads(json.dumps(old))
        reordered["conditions"][0] = dict(reversed(list(reordered["conditions"][0].items())))
        self.assertEqual(group_changes(old, reordered), 0)

    def test_diff_sums_entry_and_exit(self):
        strategy = _dsl_strategy()
        new_entry = copy.deepcopy(strategy["entry"])
        new_entry["conditions"][0]["value"] = 20.0
        new_exit = copy.deepcopy(strategy["exit"])
        new_exit["conditions"][0]["value"] = 65.0
        self.assertEqual(structural_diff(strategy["entry"], strategy["exit"], new_entry, new_exit), 2)


class MutationChainTests(unittest.TestCase):
    def test_single_condition_addition_is_applied(self):
        strategy = _dsl_strategy()
        entry = copy.deepcopy(strategy["entry"])
        entry["conditions"].append({"indicator": "regime", "operator": "!=", "value_str": "unfavorable"})

        with patch("hermes_trading.reflect._backtest_guard", return_value=None):
            result = _apply_dsl_hypothesis(strategy, _hypothesis(entry=entry), GOAL)

        self.assertTrue(result["changed"])
        self.assertEqual(result["structural_changes"], 1)
        self.assertEqual(len(strategy["entry"]["conditions"]), 2)
        self.assertIn("losses cluster", result["reason"])

    def test_no_change_action_passes_through_without_rejection(self):
        strategy = _dsl_strategy()
        result = _apply_dsl_hypothesis(strategy, _hypothesis(action="no_change"), GOAL)
        self.assertFalse(result["changed"])
        self.assertNotIn("rejected", result)

    def test_unknown_action_is_rejected(self):
        result = _apply_dsl_hypothesis(_dsl_strategy(), _hypothesis(action="rewrite_everything"), GOAL)
        self.assertTrue(result["rejected"])

    def test_missing_proposed_groups_are_rejected(self):
        hypothesis = _hypothesis()
        hypothesis["proposed_exit"] = None
        result = _apply_dsl_hypothesis(_dsl_strategy(), hypothesis, GOAL)
        self.assertTrue(result["rejected"])
        self.assertIn("proposed_exit", result["reason"])

    def test_schema_violation_is_rejected(self):
        strategy = _dsl_strategy()
        entry = copy.deepcopy(strategy["entry"])
        entry["conditions"][0]["surprise_field"] = 1
        result = _apply_dsl_hypothesis(strategy, _hypothesis(entry=entry), GOAL)
        self.assertTrue(result["rejected"])
        self.assertIn("DSL validation", result["reason"])
        self.assertEqual(strategy["entry"], _dsl_strategy()["entry"])

    def test_semantic_violation_out_of_bounds_period_is_rejected(self):
        strategy = _dsl_strategy()
        entry = copy.deepcopy(strategy["entry"])
        entry["conditions"][0]["params"]["period"] = 99
        result = _apply_dsl_hypothesis(strategy, _hypothesis(entry=entry), GOAL)
        self.assertTrue(result["rejected"])
        self.assertIn("outside [2, 50]", result["reason"])

    def test_risk_field_smuggled_into_a_condition_is_rejected(self):
        strategy = _dsl_strategy()
        entry = copy.deepcopy(strategy["entry"])
        entry["conditions"][0]["stop_loss_pct"] = 50.0
        result = _apply_dsl_hypothesis(strategy, _hypothesis(entry=entry), GOAL)
        self.assertTrue(result["rejected"])
        self.assertEqual(strategy["risk"]["stop_loss_pct"], 2.0)

    def test_two_structural_changes_are_rejected_by_one_block_only(self):
        strategy = _dsl_strategy()
        entry = copy.deepcopy(strategy["entry"])
        entry["conditions"][0]["value"] = 20.0
        exit_group = copy.deepcopy(strategy["exit"])
        exit_group["conditions"][0]["value"] = 65.0
        result = _apply_dsl_hypothesis(strategy, _hypothesis(entry=entry, exit_group=exit_group), GOAL)
        self.assertTrue(result["rejected"])
        self.assertIn("one_block_only", result["reason"])
        self.assertEqual(strategy["entry"]["conditions"][0]["value"], 25.0)

    def test_identical_proposal_is_a_no_change_not_a_version_bump(self):
        strategy = _dsl_strategy()
        result = _apply_dsl_hypothesis(strategy, _hypothesis(), GOAL)
        self.assertFalse(result["changed"])
        self.assertNotIn("rejected", result)
        self.assertIn("identical", result["reason"])

    def test_backtest_rejection_blocks_the_mutation(self):
        strategy = _dsl_strategy()
        entry = copy.deepcopy(strategy["entry"])
        entry["conditions"][0]["value"] = 20.0
        with patch("hermes_trading.reflect._backtest_guard", return_value="rejected: zero signals in backtest"):
            result = _apply_dsl_hypothesis(strategy, _hypothesis(entry=entry), GOAL)
        self.assertTrue(result["rejected"])
        self.assertIn("backtest", result["reason"])
        self.assertEqual(strategy["entry"]["conditions"][0]["value"], 25.0)


class PromptContractTests(unittest.TestCase):
    def test_prompt_carries_current_dsl_catalogue_and_contract(self):
        prompt = _hermes_prompt(_dsl_strategy(), GOAL, [], [])
        self.assertIn('"entry"', prompt)
        self.assertIn("crosses_above", prompt)
        self.assertIn("favorable|neutral|unfavorable", prompt)
        self.assertIn('"no_change"', prompt)
        self.assertIn("proposed_entry", prompt)
        self.assertIn("expected_effect", prompt)
        # Risk stays out of the model's reach.
        self.assertNotIn("stop_loss_pct", prompt)
        self.assertNotIn("position_size_r", prompt)

    def test_prompt_includes_regime_breakdown_of_trades(self):
        trades = [
            {"ts": "t1", "market_regime_at_entry": "unfavorable", "net_pnl_usd": -4.0},
            {"ts": "t2", "market_regime_at_entry": "favorable", "net_pnl_usd": 6.0},
        ]
        prompt = _hermes_prompt(_dsl_strategy(), GOAL, trades, [])
        self.assertIn('"unfavorable"', prompt)
        self.assertIn('"net_pnl_usd": -4.0', prompt)


class HermesPipelineTests(unittest.TestCase):
    def test_hermes_applies_a_valid_structural_proposal_end_to_end(self):
        strategy = _dsl_strategy()
        entry = copy.deepcopy(strategy["entry"])
        entry["conditions"].append({"indicator": "regime", "operator": "!=", "value_str": "unfavorable"})
        llm_output = json.dumps(
            {
                "action": "change",
                "proposed_entry": entry,
                "proposed_exit": strategy["exit"],
                "rationale": "all losing trades entered in unfavorable regime",
                "expected_effect": "expectancy > 0 over the next 30 trades",
                "issue": "regime_losses",
            }
        )

        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config.yaml").write_text(yaml.safe_dump({"model": {"default": "test-model", "provider": "test"}}))
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=llm_output, stderr="")
            with (
                patch.dict("os.environ", {"HERMES_REFLECT_HOME": tmp}),
                patch("hermes_trading.reflect.subprocess.run", return_value=completed),
                patch("hermes_trading.reflect._backtest_guard", return_value=None),
            ):
                result = _hermes(strategy, GOAL, [], [])

        self.assertTrue(result["changed"])
        self.assertEqual(result["mode"], "hermes")
        self.assertEqual(result["model"], "test-model")
        self.assertEqual(result["issue"], "regime_losses")
        self.assertEqual(len(strategy["entry"]["conditions"]), 2)


if __name__ == "__main__":
    unittest.main()
