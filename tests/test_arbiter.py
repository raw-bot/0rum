"""Thesis-budget arbiter (scripts/replay_harness/arbiter.py).

Contract under test:
  * with no budget contention the arbiter reproduces the production engine's
    fills exactly (the auction degenerates to the baseline);
  * same-cycle contention on one (symbol, direction) thesis is resolved by
    merit order, the second intent TOPS UP the remaining budget instead of
    being hard-refused;
  * an exhausted thesis refuses with `thesis_already_funded`; a binding
    portfolio-wide cap refuses with `risk_cap_total`;
  * re-entry while holding is an explicit policy: "hold" never adds,
    "topup" opens a separate tranche with its own bracket;
  * a strategy EXIT closes every tranche; protective exits fire per tranche;
  * allocation is deterministic across identical runs.
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from orum.portfolio.paper_engine import PaperEngine
from orum.strategies import _ENGINES, register_engine
from orum.strategies.base import Side, Signal

from scripts.replay_harness.arbiter import base_strategy_id, make_arbiter_engine_class
from scripts.replay_harness.arbiter_report import _fill_stats


def _candles(closes: list[float], start_ts: int = 0) -> list[dict]:
    return [
        {"ts": start_ts + i, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0}
        for i, c in enumerate(closes)
    ]


class ScriptedEngine:
    """Emits a scripted side on every fresh candle (default LONG)."""
    name = "scripted_long"
    version = "1"
    required_timeframes: list[str] = []
    required_indicators: list[str] = []
    warmup_period = 1
    script: dict[str, Side] = {}  # strategy never sees its id; keyed by symbol
    suggested_stops: dict[str, float] = {}

    def init(self, config):
        pass

    def on_candle(self, candle, context):
        side = self.script.get(context.symbol, Side.LONG)
        return Signal(side, context.symbol, context.timeframe, "scripted",
                      suggested_stop=self.suggested_stops.get(context.symbol))


class ArbiterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        register_engine("scripted_long", ScriptedEngine)
        ScriptedEngine.script = {}
        ScriptedEngine.suggested_stops = {}
        self.market: dict[tuple[str, str], list[dict]] = {}

    def tearDown(self) -> None:
        _ENGINES.pop("scripted_long", None)
        self._dir.cleanup()

    # ---- helpers ----------------------------------------------------------
    def _provider(self, symbol: str, timeframe: str, limit: int) -> list[dict]:
        return self.market.get((symbol, timeframe), [])

    def _paths(self, label: str) -> dict:
        d = self.root / label
        d.mkdir(exist_ok=True)
        return {
            "positions_path": d / "positions.json",
            "fills_path": d / "fills.jsonl",
            "equity_path": d / "equity.jsonl",
        }

    def _config(self, strategies: list[dict], *, symbol_cap=0.03, total_cap=0.05) -> dict:
        return {
            "starting_balance_usd": 10_000.0,
            "max_total_stop_risk_pct": total_cap,
            "max_symbol_stop_risk_pct": symbol_cap,
            "strategies": strategies,
        }

    def _strategy(self, sid: str, symbol: str, risk_pct: float, timeframe: str = "4h",
                  monitor: str | None = None) -> dict:
        raw = {"id": sid, "engine": "scripted_long", "symbol": symbol,
               "timeframe": timeframe, "risk_pct": risk_pct, "params": {}}
        raw["monitor_timeframe"] = monitor
        return raw

    def _arbiter(self, config: dict, label: str, **kwargs):
        cls = make_arbiter_engine_class()
        return cls(config, candle_provider=self._provider, **self._paths(label), **kwargs)

    def _now(self, i: int) -> datetime:
        return datetime(2026, 1, 1, i, tzinfo=timezone.utc)

    @staticmethod
    def _read(path: Path) -> list[dict]:
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    # ---- parity -----------------------------------------------------------
    def test_harness_uses_production_run_cycle(self):
        engine_class = make_arbiter_engine_class()
        self.assertNotIn("run_cycle", engine_class.__dict__)

    def test_report_counts_position_topups_and_legacy_atr_budget(self):
        stats = _fill_stats([{
            "strategy_id": "a", "position_id": "a::t2", "action": "open",
            "fee_usd": 0.0, "qty": 2.0, "atr_risk": 10.0,
            "risk_distance": 25.0,
        }])

        self.assertEqual(stats["topup_entries_by_strategy"], {"a": 1})
        self.assertEqual(stats["entry_risk_distribution"]["a"]["total_usd"], 20.0)

    def test_no_contention_matches_production_engine(self):
        strategies = [self._strategy("btc", "BTC/USDT", 0.02),
                      self._strategy("eth", "ETH/USDT", 0.02)]
        config = self._config(strategies)
        baseline = PaperEngine(config, candle_provider=self._provider,
                               **self._paths("baseline"))
        arbiter = self._arbiter(config, "arbiter", reentry_policy="hold")
        for cycle in range(3):
            closes = [2000.0] * 20 + [2600.0 + 10 * j for j in range(cycle + 1)]
            self.market = {("BTC/USDT", "4h"): _candles(closes),
                           ("ETH/USDT", "4h"): _candles(closes)}
            baseline.run_cycle(now=self._now(cycle))
            arbiter.run_cycle(now=self._now(cycle))
        base_fills = self._read(self._paths("baseline")["fills_path"])
        arb_fills = self._read(self._paths("arbiter")["fills_path"])
        self.assertEqual(base_fills, arb_fills)
        self.assertEqual(self._read(self._paths("baseline")["equity_path"]),
                         self._read(self._paths("arbiter")["equity_path"]))

    # ---- same-cycle auction -------------------------------------------------
    def test_same_cycle_contention_second_intent_tops_up(self):
        config = self._config([self._strategy("a", "BTC/USDT", 0.02),
                               self._strategy("b", "BTC/USDT", 0.02)], symbol_cap=0.03)
        self.market = {("BTC/USDT", "4h"): _candles([2000.0] * 21)}
        summary = self._arbiter(config, "run").run_cycle(now=self._now(0))
        self.assertEqual(summary["intents"], {"a": "open", "b": "open"})
        by_sid = {r["strategy_id"]: r for r in summary["auction"]}
        self.assertEqual(by_sid["a"]["outcome"], "granted_full")
        self.assertEqual(by_sid["b"]["outcome"], "granted_topup")
        self.assertAlmostEqual(by_sid["a"]["granted_risk_usd"], 200.0, places=4)
        self.assertAlmostEqual(by_sid["b"]["granted_risk_usd"], 100.0, places=4)
        self.assertAlmostEqual(by_sid["b"]["grant_fraction"], 0.5, places=6)

    def test_merit_order_decides_who_gets_the_full_grant(self):
        config = self._config([self._strategy("a", "BTC/USDT", 0.02),
                               self._strategy("b", "BTC/USDT", 0.02)], symbol_cap=0.03)
        self.market = {("BTC/USDT", "4h"): _candles([2000.0] * 21)}
        summary = self._arbiter(config, "run", merit_order=["b", "a"]).run_cycle(now=self._now(0))
        by_sid = {r["strategy_id"]: r for r in summary["auction"]}
        self.assertEqual(by_sid["b"]["outcome"], "granted_full")
        self.assertEqual(by_sid["a"]["outcome"], "granted_topup")

    def test_unknown_merit_strategy_rejected(self):
        config = self._config([self._strategy("a", "BTC/USDT", 0.02)])
        with self.assertRaises(ValueError):
            self._arbiter(config, "run", merit_order=["ghost"])

    def test_exhausted_thesis_refuses_thesis_already_funded(self):
        config = self._config([self._strategy("a", "BTC/USDT", 0.02),
                               self._strategy("b", "BTC/USDT", 0.005)], symbol_cap=0.02)
        self.market = {("BTC/USDT", "4h"): _candles([2000.0] * 21)}
        summary = self._arbiter(config, "run").run_cycle(now=self._now(0))
        self.assertEqual(summary["intents"]["a"], "open")
        self.assertEqual(summary["intents"]["b"], "thesis_already_funded")
        self.assertEqual(summary["open_positions"], ["a"])

    def test_total_cap_binding_refuses_risk_cap_total(self):
        config = self._config([self._strategy("btc", "BTC/USDT", 0.02),
                               self._strategy("eth", "ETH/USDT", 0.02)],
                              symbol_cap=0.03, total_cap=0.02)
        self.market = {("BTC/USDT", "4h"): _candles([2000.0] * 21),
                       ("ETH/USDT", "4h"): _candles([2000.0] * 21)}
        summary = self._arbiter(config, "run").run_cycle(now=self._now(0))
        self.assertEqual(summary["intents"]["btc"], "open")
        self.assertEqual(summary["intents"]["eth"], "risk_cap_total")

    # ---- re-entry policy ----------------------------------------------------
    def _reentry_summaries(self, policy: str, cycles: int = 2, **kwargs) -> list[dict]:
        config = self._config([self._strategy("a", "BTC/USDT", 0.02)], symbol_cap=0.03)
        engine = self._arbiter(config, f"run_{policy}", reentry_policy=policy, **kwargs)
        out = []
        for cycle in range(cycles):
            self.market = {("BTC/USDT", "4h"): _candles([2000.0] * (21 + cycle))}
            out.append(engine.run_cycle(now=self._now(cycle)))
        return out

    def test_reentry_hold_never_adds(self):
        summaries = self._reentry_summaries("hold")
        self.assertEqual(summaries[0]["intents"]["a"], "open")
        self.assertEqual(summaries[1]["intents"]["a"], "reentry_hold")
        self.assertEqual(summaries[1]["open_positions"], ["a"])

    def test_reentry_topup_opens_tranche_sized_to_remaining_budget(self):
        summaries = self._reentry_summaries("topup", cycles=3)
        self.assertEqual(summaries[1]["intents"]["a"], "open_topup")
        self.assertEqual(summaries[1]["open_positions"], ["a", "a::t2"])
        record = summaries[1]["auction"][0]
        self.assertEqual(record["outcome"], "granted_topup")
        # The tranche completes the thesis budget, it does not stack on top.
        self.assertAlmostEqual(record["granted_risk_usd"], record["remaining_thesis_usd"], places=6)
        self.assertLess(record["granted_risk_usd"], record["requested_risk_usd"])
        # Third signal: the thesis is fully funded now.
        self.assertEqual(summaries[2]["intents"]["a"], "thesis_already_funded")
        self.assertEqual(base_strategy_id("a::t2"), "a")
        topup_fill = summaries[1]["fills"][0]
        self.assertEqual(topup_fill["strategy_id"], "a")
        self.assertEqual(topup_fill["position_id"], "a::t2")

    def test_min_topup_fraction_floors_dust_grants(self):
        summaries = self._reentry_summaries("topup", min_topup_fraction=0.9)
        # Remaining budget (~half the request) is below the 90% floor -> refuse.
        self.assertEqual(summaries[1]["intents"]["a"], "thesis_already_funded")
        self.assertEqual(summaries[1]["open_positions"], ["a"])

    def test_exit_closes_every_tranche(self):
        config = self._config([self._strategy("a", "BTC/USDT", 0.02)], symbol_cap=0.03)
        engine = self._arbiter(config, "run", reentry_policy="topup")
        for cycle in range(2):
            self.market = {("BTC/USDT", "4h"): _candles([2000.0] * (21 + cycle))}
            engine.run_cycle(now=self._now(cycle))
        ScriptedEngine.script = {"BTC/USDT": Side.EXIT}
        self.market = {("BTC/USDT", "4h"): _candles([2000.0] * 23)}
        summary = engine.run_cycle(now=self._now(2))
        self.assertEqual(summary["intents"]["a"], "close")
        self.assertEqual(summary["open_positions"], [])
        closes = [f for f in self._read(self._paths("run")["fills_path"]) if f["action"] == "close"]
        self.assertEqual({f["strategy_id"] for f in closes}, {"a"})
        self.assertEqual({f["position_id"] for f in closes}, {"a", "a::t2"})

    def test_protective_exit_fires_per_tranche(self):
        config = self._config(
            [self._strategy("a", "BTC/USDT", 0.02, monitor="15m")], symbol_cap=0.03)
        engine = self._arbiter(config, "run", reentry_policy="topup")
        base_4h = _candles([2000.0] * 21)
        monitor = _candles([2000.0] * 4, start_ts=1000)
        # Tranche 1 with a tight stop (below the candles' ~1980 lows so it
        # survives normal noise), tranche 2 (next cycle) with a deep one.
        ScriptedEngine.suggested_stops = {"BTC/USDT": 1975.0}
        self.market = {("BTC/USDT", "4h"): base_4h, ("BTC/USDT", "15m"): monitor}
        engine.run_cycle(now=self._now(0))
        ScriptedEngine.suggested_stops = {"BTC/USDT": 1500.0}
        self.market = {("BTC/USDT", "4h"): _candles([2000.0] * 22),
                       ("BTC/USDT", "15m"): monitor + _candles([2000.0], start_ts=1004)}
        summary = engine.run_cycle(now=self._now(1))
        self.assertEqual(summary["open_positions"], ["a", "a::t2"])
        # A monitor candle dips through the tight stop only.
        dip = {"ts": 1005, "open": 2000.0, "high": 2000.0, "low": 1970.0,
               "close": 1995.0, "volume": 1.0}
        self.market = {("BTC/USDT", "4h"): _candles([2000.0] * 22),
                       ("BTC/USDT", "15m"): monitor + _candles([2000.0], start_ts=1004) + [dip]}
        summary = engine.run_cycle(now=self._now(2))
        self.assertEqual(summary["intents"]["a"], "close_stop_loss")
        self.assertEqual(summary["open_positions"], ["a::t2"])

    def test_identical_runs_produce_identical_fill_streams(self):
        def run(label: str) -> list[dict]:
            config = self._config([self._strategy("a", "BTC/USDT", 0.02),
                                   self._strategy("b", "BTC/USDT", 0.02)], symbol_cap=0.03)
            engine = self._arbiter(config, label, reentry_policy="topup")
            for cycle in range(3):
                self.market = {("BTC/USDT", "4h"): _candles([2000.0 + cycle] * (21 + cycle))}
                engine.run_cycle(now=self._now(cycle))
            return self._read(self._paths(label)["fills_path"])
        self.assertEqual(run("first"), run("second"))


if __name__ == "__main__":
    unittest.main()
