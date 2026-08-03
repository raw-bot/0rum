"""Replay-harness invariants: no lookahead, determinism, legacy fidelity
(fees cross-checked against the REAL PaperBroker), degenerate-stop safety,
SL-first collisions, and non-self-referential indicator checks."""

import json
import math
import tempfile
import unittest
from pathlib import Path

from orum.portfolio.paper_broker import Account, PaperBroker

from scripts.replay_harness.features import _atr, efficiency_ratio
from scripts.replay_harness.single_replay import (
    ReplayConfig,
    generate_candidates,
    run_replay,
)
from scripts.replay_harness.reprice import load_trades, reprice
from scripts.replay_harness.timeline import (
    INTERVAL_MS,
    SnapshotProvider,
    cycle_times,
    timestamps_for_signal,
)

H4 = INTERVAL_MS["4h"]
M15 = INTERVAL_MS["15m"]
T0 = 1_700_000_000_000 - (1_700_000_000_000 % H4)  # aligned origin


def _mk_4h(prices: list[tuple[float, float, float, float]]) -> list[dict]:
    return [{"ts": T0 + i * H4, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}
            for i, (o, h, l, c) in enumerate(prices)]


def _mk_15m_from_4h(bars_4h: list[dict]) -> list[dict]:
    """16 sub-bars per 4h bar, linear open->close, extremes on the middle bar
    so the 4h high/low are reachable by the monitor stream."""
    out = []
    for bar in bars_4h:
        for j in range(16):
            frac0, frac1 = j / 16, (j + 1) / 16
            o = bar["open"] + (bar["close"] - bar["open"]) * frac0
            c = bar["open"] + (bar["close"] - bar["open"]) * frac1
            h, l = max(o, c), min(o, c)
            if j == 8:
                h, l = bar["high"], bar["low"]
            out.append({"ts": bar["ts"] + j * M15, "open": o, "high": h, "low": l,
                        "close": c, "volume": 1.0})
    return out


def _trending_market(n_up: int = 90) -> list[dict]:
    prices, px = [], 100.0
    for _ in range(n_up):
        o = px
        px += 0.6
        prices.append((o, px + 0.3, o - 0.3, px))
    for _ in range(4):  # pullback into the zone
        o = px
        px -= 1.6
        prices.append((o, o + 0.1, px - 0.4, px))
    for _ in range(6):  # strong green recovery, then continuation
        o = px
        px += 3.0
        prices.append((o, px + 0.4, o - 0.1, px))
    return _mk_4h(prices)


def _provider(bars_4h: list[dict]) -> SnapshotProvider:
    p = SnapshotProvider()
    p.series[("BTC/USDT", "4h")] = bars_4h
    p.series[("BTC/USDT", "15m")] = _mk_15m_from_4h(bars_4h)
    return p


class TimelineTests(unittest.TestCase):
    def test_as_of_never_serves_unclosed_bars(self):
        provider = _provider(_trending_market())
        for now in range(T0, T0 + 100 * H4, 7 * M15):  # deliberately unaligned steps
            for tf in ("4h", "15m"):
                for bar in provider.as_of("BTC/USDT", tf, 0, now):
                    self.assertLessEqual(bar["ts"] + INTERVAL_MS[tf], now,
                                         f"{tf} bar served before it closed")

    def test_signal_timestamps_chain(self):
        ts = timestamps_for_signal(T0, "4h")
        self.assertEqual(ts["bar_close_time"], T0 + H4)
        self.assertEqual(ts["decision_available_time"], T0 + H4)
        self.assertGreaterEqual(ts["cycle_observed_time"], ts["decision_available_time"])
        self.assertEqual(ts["cycle_observed_time"] % 900_000, 0)

    def test_reprice_pairs_tranches_by_position_id(self):
        fills = [
            {"ts": "2026-01-01T00:00:00+00:00", "strategy_id": "ut",
             "position_id": "ut", "action": "open", "price": 100.0,
             "stop_loss_price": 90.0, "take_profit_price": None,
             "atr_risk": 5.0, "risk_distance": 10.0, "risk_pct": 0.02},
            {"ts": "2026-01-01T00:15:00+00:00", "strategy_id": "ut",
             "position_id": "ut::t2", "action": "open", "price": 110.0,
             "stop_loss_price": 90.0, "take_profit_price": None,
             "atr_risk": 6.0, "risk_distance": 20.0, "risk_pct": 0.01},
            {"ts": "2026-01-01T01:00:00+00:00", "strategy_id": "ut",
             "position_id": "ut", "action": "close", "price": 120.0,
             "reason": "signal"},
            {"ts": "2026-01-01T01:00:00+00:00", "strategy_id": "ut",
             "position_id": "ut::t2", "action": "close", "price": 120.0,
             "reason": "signal"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp)
            (ledger / "fills.jsonl").write_text(
                "\n".join(json.dumps(fill) for fill in fills) + "\n"
            )
            trades = load_trades(ledger)

        self.assertEqual({trade["position_id"] for trade in trades}, {"ut", "ut::t2"})
        self.assertEqual({trade["sid"] for trade in trades}, {"ut"})
        self.assertEqual({trade["risk_distance"] for trade in trades}, {10.0, 20.0})

    def test_reprice_preserves_legacy_atr_sizing(self):
        provider = SnapshotProvider()
        provider.series[("BTC/USDT", "15m")] = [
            {"ts": T0, "open": 100.0, "high": 100.0, "low": 100.0,
             "close": 100.0, "volume": 1.0},
            {"ts": T0 + M15, "open": 110.0, "high": 110.0, "low": 110.0,
             "close": 110.0, "volume": 1.0},
        ]
        trades = [{
            "sid": "ut", "position_id": "ut", "open_ts": T0,
            "legacy_entry": 100.0, "stop": None, "tp": None,
            "atr_risk": 10.0, "risk_distance": 20.0, "risk_pct": 0.02,
            "close_ts": T0 + M15, "close_reason": "signal",
            "legacy_exit": 110.0,
        }]

        result = reprice(
            trades, provider, symbol="BTC/USDT", slippage_bps=0.0,
            max_leverage=None, starting_balance=10_000.0,
        )

        self.assertEqual(result["n_trades"], 1)
        self.assertAlmostEqual(result["net_return_pct"], 1.98, places=2)

    def test_reprice_short_uses_directional_slippage_pnl_and_mark_to_market(self):
        provider = SnapshotProvider()
        provider.series[("BTC/USDT", "15m")] = [
            {"ts": T0, "open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.0, "volume": 1.0},
            {"ts": T0 + M15, "open": 90.0, "high": 91.0, "low": 89.0,
             "close": 90.0, "volume": 1.0},
        ]
        trades = [{
            "sid": "ak", "position_id": "ak", "side": "short", "open_ts": T0,
            "legacy_entry": 100.0, "stop": 110.0, "tp": 80.0,
            "atr_risk": 10.0, "risk_distance": 10.0, "risk_pct": 0.02,
            "close_ts": T0 + M15, "close_reason": "signal",
            "legacy_exit": 90.0,
        }]

        result = reprice(
            trades, provider, symbol="BTC/USDT", slippage_bps=0.0,
            max_leverage=None, starting_balance=10_000.0,
        )

        self.assertEqual(result["n_trades"], 1)
        self.assertAlmostEqual(result["net_return_pct"], 1.98, places=2)

    def test_cycle_grid(self):
        grid = cycle_times(T0 + 1, T0 + 3_600_000)
        self.assertTrue(all(t % 900_000 == 0 for t in grid))
        self.assertTrue(all(t > T0 for t in grid))


class CandidateTests(unittest.TestCase):
    def test_candidates_emitted_and_deterministic(self):
        provider = _provider(_trending_market())
        a = generate_candidates(provider, symbol="BTC/USDT", timeframe="4h", candles_limit=300)
        b = generate_candidates(provider, symbol="BTC/USDT", timeframe="4h", candles_limit=300)
        self.assertTrue(a, "fixture must produce at least one candidate")
        self.assertEqual(json.dumps(a, sort_keys=True, default=str),
                         json.dumps(b, sort_keys=True, default=str))
        for c in a:
            self.assertLess(c["stop"], c["signal_close"])
            self.assertGreater(c["atr_risk"], 0)
            self.assertIn("stop_distance_atr", c["features"])


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.provider = _provider(_trending_market())
        self.candidates = generate_candidates(self.provider, symbol="BTC/USDT",
                                              timeframe="4h", candles_limit=300)

    def test_legacy_fill_is_signal_close_and_fees_match_paper_broker(self):
        cfg = ReplayConfig(name="legacy", sizing="legacy_atr_2")
        result = run_replay(cfg, self.candidates, self.provider, symbol="BTC/USDT")
        self.assertTrue(result["trades"] or result["open_position_at_end"])
        opened = [d for d in result["decisions"] if d["action"] == "accepted"]
        self.assertTrue(opened)
        first = self.candidates[0]
        # Legacy convention: entry price IS the signal-candle close.
        trade_or_pos_entry = (result["trades"][0]["entry_price"] if result["trades"]
                              else None)
        if trade_or_pos_entry is not None:
            matching = [c for c in self.candidates
                        if c["signal_close"] == trade_or_pos_entry]
            self.assertTrue(matching, "entry price must equal a candidate signal close")
        # Fee cross-check against the REAL broker for the first accepted trade.
        if result["trades"]:
            t = result["trades"][0]
            account = Account(balance_usd=cfg.starting_balance)
            broker = PaperBroker()
            broker.open(account, strategy_id="x", symbol="BTC/USDT",
                        price=t["entry_price"], atr_risk=t["atr_risk"],
                        risk_pct=cfg.risk_pct, equity_for_sizing=cfg.starting_balance,
                        max_leverage=cfg.max_leverage)
            broker.close(account, strategy_id="x", price=t["exit_price"])
            harness_balance = cfg.starting_balance + t["account_net_pnl"]
            self.assertAlmostEqual(account.balance_usd, harness_balance, places=6,
                                   msg="harness accounting must match PaperBroker")

    def test_all_policies_share_the_candidate_stream(self):
        totals = set()
        for sizing in ("legacy_atr_2", "stop_exact", "stop_floor_1atr", "stop_floor_2atr"):
            cfg = ReplayConfig(name=sizing, sizing=sizing)
            result = run_replay(cfg, self.candidates, self.provider, symbol="BTC/USDT")
            totals.add(len(result["decisions"]))
        self.assertEqual(len(totals), 1, "every policy must see every candidate")

    def test_degenerate_stop_is_rejected_not_divided(self):
        bad = dict(self.candidates[0])
        bad["stop"] = bad["signal_close"] + 5.0  # wrong side of the fill
        cfg = ReplayConfig(name="x", sizing="stop_exact")
        result = run_replay(cfg, [bad], self.provider, symbol="BTC/USDT")
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["decisions"][0]["action"], "rejected")
        self.assertIn(result["decisions"][0]["reason"], ("gap_through_stop", "invalid_stop"))

    def test_sl_first_on_collision(self):
        candidate = dict(self.candidates[0])
        entry = candidate["signal_close"]
        candidate["stop"] = entry - 0.5
        cfg = ReplayConfig(name="col", sizing="stop_exact", target_ref="signal_close")
        # Craft a monitor bar that spans both the stop and the 3R target.
        provider = _provider(_trending_market())
        target = entry + candidate["rr"] * (entry - candidate["stop"])
        wild = {"ts": candidate["cycle_observed_time"] + M15, "open": entry,
                "high": target + 1, "low": candidate["stop"] - 1, "close": entry,
                "volume": 1.0}
        series = [b for b in provider.series[("BTC/USDT", "15m")]
                  if b["ts"] < wild["ts"]] + [wild]
        provider.series[("BTC/USDT", "15m")] = series
        result = run_replay(cfg, [candidate], provider, symbol="BTC/USDT")
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["exit_reason"], "stop_loss")
        self.assertTrue(result["trades"][0]["sl_tp_collision"])

    def test_sizing_bases(self):
        candidate = dict(self.candidates[0])
        entry = candidate["signal_close"]
        stop_dist = entry - candidate["stop"]
        atr1 = candidate["atr_risk"] / 2
        expected = {
            "legacy_atr_2": candidate["atr_risk"],
            "stop_exact": stop_dist,
            "stop_floor_1atr": max(stop_dist, atr1),
            "stop_floor_2atr": max(stop_dist, candidate["atr_risk"]),
        }
        for sizing, basis in expected.items():
            cfg = ReplayConfig(name=sizing, sizing=sizing, max_leverage=None)
            result = run_replay(cfg, [candidate], self.provider, symbol="BTC/USDT")
            accepted = [d for d in result["decisions"] if d["action"] == "accepted"]
            self.assertTrue(accepted, sizing)
            self.assertAlmostEqual(accepted[0]["sizing_basis"], basis, places=9, msg=sizing)
            self.assertAlmostEqual(accepted[0]["qty"],
                                   cfg.risk_pct * cfg.starting_balance / basis, places=9)


class IndicatorCrossChecks(unittest.TestCase):
    """Independent (pandas) implementations — not the harness's own code."""

    def test_ema_matches_pandas_with_sma_seed(self):
        import pandas as pd
        from orum.strategies.ha_trend import _ema
        values = [float(v) + math.sin(v / 3.0) * 4 for v in range(1, 120)]
        ours = _ema(values, 20)
        s = pd.Series(values)
        seed = s.rolling(20).mean().iloc[19]
        ref = s.copy()
        ref.iloc[:19] = float("nan")
        ref.iloc[19] = seed
        alpha = 2 / 21
        for i in range(20, len(values)):
            ref.iloc[i] = alpha * values[i] + (1 - alpha) * ref.iloc[i - 1]
        for i in range(19, len(values)):
            self.assertAlmostEqual(ours[i], float(ref.iloc[i]), places=9)

    def test_rsi_matches_pandas_wilder(self):
        import pandas as pd
        from orum.strategies.ha_trend import _rsi_last
        values = [100 + math.sin(v / 5.0) * 10 + v * 0.05 for v in range(200)]
        s = pd.Series(values)
        delta = s.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        ref = 100 - 100 / (1 + gain / loss)
        # pandas ewm seeds the recursion on the first delta; TV/Wilder (and
        # ha_trend) seed on the SMA of the first 14 deltas. The seeding
        # difference decays exponentially — after 200 bars it is ~1e-5.
        self.assertAlmostEqual(_rsi_last(values, 14), float(ref.iloc[-1]), places=3)

    def test_atr_matches_pandas_wilder(self):
        import pandas as pd
        bars = _trending_market()
        df = pd.DataFrame(bars)
        tr = pd.concat([df["high"] - df["low"],
                        (df["high"] - df["close"].shift()).abs(),
                        (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
        ref = tr.ewm(alpha=1 / 14, adjust=False).mean()
        self.assertAlmostEqual(_atr(bars, 14), float(ref.iloc[-1]), places=9)

    def test_efficiency_ratio_bounds(self):
        rising = [float(v) for v in range(50)]
        self.assertAlmostEqual(efficiency_ratio(rising, 10), 1.0)
        flat_chop = [100.0 + (1 if i % 2 else -1) for i in range(50)]
        self.assertLess(efficiency_ratio(flat_chop, 10), 0.2)


GOLDEN = Path(__file__).resolve().parents[1] / "backtests" / "fixtures" / "pine_golden_btcusdt_4h.json"


@unittest.skipUnless(GOLDEN.exists(), "Pine golden fixture not exported yet")
class PineGoldenIndicatorCheck(unittest.TestCase):
    """Non-self-referential: harness EMA/RSI vs values computed BY TradingView,
    at the Pine signal bars, on the snapshot data (full-history windows)."""

    def test_indicator_values_against_pine(self):
        from orum.strategies.ha_trend import _ema, _rsi_last
        from scripts.replay_harness.snapshot import load_series
        golden = json.loads(GOLDEN.read_text())
        bars = load_series("BTC/USDT", "4h")
        by_ts = {b["ts"]: i for i, b in enumerate(bars)}
        # Skip Pine's left-edge seeding transient: its EMA/RSI only converge
        # ~250 bars after ITS chart history starts (fixture records where).
        pine_converged = golden.get("pine_history_start_ms", 0) + 250 * 14_400_000
        checked = 0
        for sig in golden["signals"]:
            i = by_ts.get(int(sig["bar_open_time"]))
            if i is None or i < 250 or int(sig["bar_open_time"]) < pine_converged:
                continue
            window = bars[: i + 1]
            highs = [b["high"] for b in window]
            closes = [b["close"] for b in window]
            self.assertAlmostEqual(_ema(highs, 20)[-1], sig["ema_high"],
                                   delta=sig["close"] * 5e-4)
            self.assertAlmostEqual(_rsi_last(closes, 14), sig["rsi"], delta=1.0)
            checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
