"""Decision parity between the vectorized gate (harness) and the production
walk_forward_forecast, on REAL snapshot data across many origins.

Bar: identical active/lock_reasons/qualified, identical gate decisions
(action, multiplier, influenced), quantiles and metrics within 1e-9 relative.
Skipped when the BTC 1h snapshot is absent (CI without market data)."""

import json
import math
import time
import unittest
from pathlib import Path

from orum.portfolio.forecast_gate import decide_forecast_gate, walk_forward_forecast

from scripts.replay_harness.fast_gate import walk_forward_forecast_fast

SNAPSHOT = Path(__file__).resolve().parents[1] / "backtests" / "snapshots" / "BTCUSDT_1h.json"


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


@unittest.skipUnless(SNAPSHOT.exists(), "BTC 1h snapshot not fetched")
class FastGateParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = json.loads(SNAPSHOT.read_text())

    def _compare_reports(self, slow: dict, fast: dict, label: str):
        self.assertEqual(slow["active"], fast["active"], label)
        self.assertEqual(slow["lock_reasons"], fast["lock_reasons"], label)
        self.assertEqual(slow.get("origin_index"), fast.get("origin_index"), label)
        for horizon, sh in slow.get("horizons", {}).items():
            fh = fast["horizons"][horizon]
            self.assertEqual(sh["qualified"], fh["qualified"], f"{label} h{horizon}")
            for name, value in sh["quantiles"].items():
                self.assertTrue(_close(value, fh["quantiles"][name]),
                                f"{label} h{horizon} {name}: {value} vs {fh['quantiles'][name]}")
            for metric, value in sh["metrics"].items():
                other = fh["metrics"][metric]
                if isinstance(value, float):
                    self.assertTrue(_close(value, other), f"{label} h{horizon} {metric}: {value} vs {other}")
                else:
                    self.assertEqual(value, other, f"{label} h{horizon} {metric}")
        self.assertEqual(len(slow["history_24h"]), len(fast["history_24h"]), label)
        for s_row, f_row in zip(slow["history_24h"], fast["history_24h"]):
            self.assertEqual(s_row["origin_ts"], f_row["origin_ts"], label)
            self.assertTrue(_close(s_row["median_return"], f_row["median_return"]), label)

    def test_decision_parity_across_origins(self):
        checked = 0
        for end in range(1200, len(self.bars), max(1, (len(self.bars) - 1200) // 12)):
            window = self.bars[end - 900:end]
            slow = walk_forward_forecast(window)
            fast = walk_forward_forecast_fast(window)
            self._compare_reports(slow, fast, f"origin@{end}")
            for atr_frac in (0.005, 0.02):
                ds = decide_forecast_gate(slow, atr_risk_fraction=atr_frac)
                df = decide_forecast_gate(fast, atr_risk_fraction=atr_frac)
                self.assertEqual(ds, df, f"gate decision origin@{end} atr={atr_frac}")
            checked += 1
        self.assertGreaterEqual(checked, 10)

    def test_short_history_and_speedup(self):
        self.assertEqual(walk_forward_forecast_fast(self.bars[:50])["active"], False)
        window = self.bars[-900:]
        t0 = time.perf_counter(); walk_forward_forecast(window); slow_s = time.perf_counter() - t0
        t0 = time.perf_counter(); walk_forward_forecast_fast(window); fast_s = time.perf_counter() - t0
        self.assertLess(fast_s * 10, slow_s, f"expected >=10x speedup, got {slow_s / fast_s:.1f}x")


if __name__ == "__main__":
    unittest.main()
