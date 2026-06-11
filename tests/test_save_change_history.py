import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from hermes_trading import reflect


class SaveChangeHistoryTests(unittest.TestCase):
    def test_history_archives_pre_change_strategy_not_the_mutated_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            strategy_path = tmp_path / "strategy.yaml"
            history_dir = tmp_path / "history"
            hypotheses_path = tmp_path / "hypotheses.jsonl"

            on_disk = {"version": "01", "entry": {"threshold": 30.0}}
            strategy_path.write_text(yaml.safe_dump(on_disk, sort_keys=False))

            # Simulate what reflect.main does: load, then mutate in memory.
            strategy = yaml.safe_load(strategy_path.read_text())
            strategy["entry"]["threshold"] = 25.0
            hypothesis = {"changed": True, "variable": "entry.threshold", "ts": "2026-06-11T00:00:00+00:00"}

            with (
                patch.object(reflect, "STRATEGY_PATH", strategy_path),
                patch.object(reflect, "HISTORY_DIR", history_dir),
                patch.object(reflect, "HYPOTHESES_PATH", hypotheses_path),
            ):
                reflect._save_change(strategy, hypothesis)

            archived = yaml.safe_load((history_dir / "v0001.yaml").read_text())
            self.assertEqual(archived["entry"]["threshold"], 30.0)
            self.assertEqual(archived["version"], "01")

            current = yaml.safe_load(strategy_path.read_text())
            self.assertEqual(current["entry"]["threshold"], 25.0)
            self.assertEqual(current["version"], "02")

            self.assertTrue(hypotheses_path.exists())


if __name__ == "__main__":
    unittest.main()
