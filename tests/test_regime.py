"""Shadow regime study (scripts/replay_harness/regime.py).

Contract under test:
  * calendar walk-forward windows tile correctly (eval starts exactly where
    train ends, last window clipped, no window past the study end);
  * thresholds are learned deterministically on train data under the
    keep-fraction floor, frozen, and only ever applied to LATER trades;
  * verdicts fail OPEN when features are unavailable (warmup);
  * ledger trades round-trip with net-of-fees R and tranche keys folded onto
    their base strategy;
  * the end-to-end study separates a profitable regime from a losing one out
    of sample and its aggregates are internally consistent.
"""

import json
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.replay_harness.regime import (
    apply_thresholds,
    build_windows,
    learn_thresholds,
    load_trades,
    regime_features,
    run_regime_study,
)
from scripts.replay_harness.timeline import SnapshotProvider

H4_MS = 4 * 3600 * 1000


def _ms(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def _candles(closes: list[float], start_ms: int = 0) -> list[dict]:
    return [{"ts": start_ms + i * H4_MS, "open": c, "high": c * 1.005,
             "low": c * 0.995, "close": c, "volume": 1.0}
            for i, c in enumerate(closes)]


def _trade(features: dict, r_net: float, ts_ms: int = 0) -> dict:
    return {"features": features, "r_net": r_net, "net_usd": 100.0 * r_net,
            "entry_ts_ms": ts_ms}


class WindowTests(unittest.TestCase):
    def test_windows_tile_and_clip(self):
        windows = build_windows("2024-01-01", "2026-07-15", train_months=12, eval_months=6)
        self.assertEqual([w["train_span"] for w in windows],
                         ["2024-01-01->2025-01-01", "2024-07-01->2025-07-01",
                          "2025-01-01->2026-01-01", "2025-07-01->2026-07-01"])
        for w in windows:
            self.assertEqual(w["eval_start_ms"], w["train_end_ms"])
        self.assertEqual(windows[-1]["eval_end_ms"], _ms("2026-07-15"))  # clipped

    def test_eval_windows_do_not_overlap(self):
        windows = build_windows("2024-01-01", "2026-07-15", train_months=12, eval_months=6)
        for prev, cur in zip(windows, windows[1:]):
            self.assertEqual(prev["eval_end_ms"],
                             min(cur["eval_start_ms"], _ms("2026-07-15")))


class FeatureTests(unittest.TestCase):
    def test_uptrend_has_positive_slope_and_high_er(self):
        f = regime_features(_candles([1000.0 * (1.01 ** i) for i in range(60)]))
        self.assertGreater(f["channel_mid_slope_atr"], 0.0)
        self.assertGreater(f["efficiency_ratio_10"], 0.9)

    def test_choppy_market_has_low_er(self):
        closes = [1000.0 + (25.0 if i % 2 else -25.0) for i in range(60)]
        f = regime_features(_candles(closes))
        self.assertLess(f["efficiency_ratio_10"], 0.2)

    def test_warmup_returns_nan(self):
        f = regime_features(_candles([1000.0] * 10))
        self.assertTrue(math.isnan(f["channel_mid_slope_atr"]))


class LearnTests(unittest.TestCase):
    def _split_trades(self) -> list[dict]:
        # High-slope trades win, low-slope trades lose; ER uninformative.
        trades = []
        for i in range(20):
            trades.append(_trade({"channel_mid_slope_atr": 0.5 + i * 0.01,
                                  "efficiency_ratio_10": 0.5}, r_net=1.0, ts_ms=i))
        for i in range(20):
            trades.append(_trade({"channel_mid_slope_atr": -0.5 - i * 0.01,
                                  "efficiency_ratio_10": 0.5}, r_net=-1.0, ts_ms=100 + i))
        return trades

    def test_learns_separating_slope_threshold(self):
        thresholds = learn_thresholds(self._split_trades())
        self.assertIsNotNone(thresholds["slope_min"])
        self.assertGreater(thresholds["slope_min"], -0.5)
        self.assertEqual(thresholds["train_pass_n"], 20)
        self.assertAlmostEqual(thresholds["train_mean_r_pass"], 1.0)

    def test_no_filter_when_filtering_cannot_help(self):
        trades = [_trade({"channel_mid_slope_atr": 0.1 * i,
                          "efficiency_ratio_10": 0.5}, r_net=1.0, ts_ms=i)
                  for i in range(20)]
        thresholds = learn_thresholds(trades)
        # Every subset has mean R 1.0 -> ties resolve to keeping everything.
        self.assertIsNone(thresholds["slope_min"])
        self.assertIsNone(thresholds["er_min"])

    def test_keep_floor_is_respected(self):
        thresholds = learn_thresholds(self._split_trades(), min_keep_fraction=0.3)
        self.assertGreaterEqual(thresholds["train_pass_n"],
                                int(0.3 * thresholds["train_n"]))

    def test_deterministic(self):
        self.assertEqual(learn_thresholds(self._split_trades()),
                         learn_thresholds(self._split_trades()))


class VerdictTests(unittest.TestCase):
    def test_blocked_reason_names_the_binding_feature(self):
        trade = _trade({"channel_mid_slope_atr": -0.2, "efficiency_ratio_10": 0.9}, 0.0)
        verdict, reason = apply_thresholds(trade, {"slope_min": 0.1, "er_min": 0.3})
        self.assertEqual(verdict, "blocked")
        self.assertIn("channel_mid_slope_atr", reason)

    def test_nan_features_fail_open(self):
        trade = _trade({"channel_mid_slope_atr": float("nan"),
                        "efficiency_ratio_10": float("nan")}, 0.0)
        verdict, reason = apply_thresholds(trade, {"slope_min": 0.1, "er_min": 0.3})
        self.assertEqual(verdict, "allowed")
        self.assertIn("fail-open", reason)


class LedgerTests(unittest.TestCase):
    def test_load_trades_nets_fees_and_folds_tranches(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "runtime_ledger"
            ledger.mkdir()
            fills = [
                {"action": "open", "strategy_id": "ak::t2", "symbol": "BTC/USDT",
                 "ts": "2025-03-01T00:00:00+00:00", "qty": 2.0, "atr_risk": 50.0,
                 "fee_usd": 5.0, "price": 100.0},
                {"action": "close", "strategy_id": "ak::t2", "symbol": "BTC/USDT",
                 "ts": "2025-03-02T00:00:00+00:00", "realized_pnl_usd": 105.0,
                 "reason": "take_profit", "price": 155.0, "qty": 2.0},
            ]
            (ledger / "fills.jsonl").write_text("\n".join(json.dumps(f) for f in fills))
            trades = load_trades(Path(tmp))
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["strategy_id"], "ak")
        self.assertAlmostEqual(trades[0]["net_usd"], 100.0)   # 105 realized - 5 entry fee
        self.assertAlmostEqual(trades[0]["r_net"], 1.0)       # 100 / (2 * 50)


class StudyTests(unittest.TestCase):
    def _run_dir(self, tmp: Path, trades: list[tuple[int, float, float]]) -> Path:
        """trades: (entry_ms, entry_price_slope_regime?) encoded via fills."""
        run = tmp / "source_run"
        (run / "runtime_ledger").mkdir(parents=True)
        fills = []
        for i, (entry_ms, net) in enumerate(trades):
            sid = "strat"
            fills.append({"action": "open", "strategy_id": sid, "symbol": "BTC/USDT",
                          "ts": _iso(entry_ms), "qty": 1.0, "atr_risk": 100.0,
                          "fee_usd": 0.0, "price": 100.0})
            fills.append({"action": "close", "strategy_id": sid, "symbol": "BTC/USDT",
                          "ts": _iso(entry_ms + H4_MS), "realized_pnl_usd": net,
                          "reason": "signal", "price": 100.0, "qty": 1.0})
        (run / "runtime_ledger" / "fills.jsonl").write_text(
            "\n".join(json.dumps(f) for f in fills))
        return run

    def test_study_blocks_the_losing_regime_out_of_sample(self):
        # Market: strong uptrend during 2024 H1+H2 train and 2025 H1 eval for
        # winners; a choppy regime hosts every loser. Features must separate
        # them ex ante, so winners sit on trending candles, losers on chop.
        start_ms = _ms("2024-01-01")
        trend = [1000.0 * (1.01 ** i) for i in range(2000)]
        chop = [1000.0 + (30.0 if i % 2 else -30.0) for i in range(2000)]
        provider = SnapshotProvider()

        def series_for(kind: str) -> list[dict]:
            return _candles(trend if kind == "trend" else chop, start_ms=start_ms)

        trades = []
        # Train year 2024: winners in trend hours, losers in chop hours — but a
        # single provider series must host both, so interleave: use two symbols?
        # Simpler: two separate studies would lose the point; instead alternate
        # market segments: first 1000 bars trend, next 1000 chop.
        seg = _candles(trend[:1000], start_ms=start_ms)
        seg += _candles(chop, start_ms=start_ms + 1000 * H4_MS)
        provider.series[("BTC/USDT", "4h")] = seg
        # Winners: entries inside the trend segment (bar 100..900).
        for i in range(100, 900, 40):
            trades.append((start_ms + i * H4_MS, 150.0))
        # Losers: entries inside the chop segment (bar 1100..1900).
        for i in range(1100, 1900, 40):
            trades.append((start_ms + i * H4_MS, -100.0))
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_dir(Path(tmp), trades)
            out = run_regime_study([run], provider, start="2024-01-01", end="2025-07-01",
                                   train_months=6, eval_months=6)
        stats = out["report"]["per_strategy"]["strat"]
        oos = [v for v in out["verdicts"] if v["verdict"] in ("allowed", "blocked")]
        self.assertGreater(len(oos), 0)
        # The OOS eval spans the chop segment: the filter must block losers.
        self.assertGreater(stats["oos_blocked"].get("n", 0), 0)
        self.assertLess(stats["oos_blocked"]["mean_r_net"], 0)
        self.assertGreater(stats["avoided_losses_usd"], 0)
        # Consistency: blocked + allowed = all, forgone/avoided match blocked net.
        self.assertEqual(stats["oos_all"]["n"],
                         stats["oos_allowed"].get("n", 0) + stats["oos_blocked"]["n"])
        self.assertAlmostEqual(
            stats["oos_blocked"]["net_usd"],
            stats["forgone_profits_usd"] - stats["avoided_losses_usd"], places=2)

    def test_trades_before_first_eval_are_train_only(self):
        provider = SnapshotProvider()
        provider.series[("BTC/USDT", "4h")] = _candles(
            [1000.0] * 4000, start_ms=_ms("2024-01-01"))
        trades = [(_ms("2024-02-01"), 50.0), (_ms("2025-02-01"), 50.0)]
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_dir(Path(tmp), trades)
            out = run_regime_study([run], provider, start="2024-01-01", end="2025-07-01",
                                   train_months=12, eval_months=6)
        by_verdict = {v["entry_ts"][:7]: v["verdict"] for v in out["verdicts"]}
        self.assertEqual(by_verdict["2024-02"], "train_only")
        self.assertIn(by_verdict["2025-02"], ("allowed", "blocked"))


if __name__ == "__main__":
    unittest.main()
