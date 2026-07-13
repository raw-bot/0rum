import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from orum.portfolio.paper_engine import PaperEngine
from orum.strategies import register_engine
from orum.strategies.base import Side, Signal


def _candles(closes: list[float]) -> list[dict]:
    """Candles with a real high/low range so ATR (and thus position sizing) > 0."""
    return [
        {"ts": i, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0}
        for i, c in enumerate(closes)
    ]


BREAKOUT = _candles([2000.0] * 20 + [2600.0])   # donchian LONG (close tops 20-bar high)
BREAKDOWN = _candles([2600.0] * 20 + [2000.0])  # donchian EXIT (close < 10-bar low)
INSIDE = _candles([2000.0] * 21)                 # flat: neither channel edge crossed -> no signal


def _fresh_gate(gate_on: bool) -> dict:
    return {
        "report_date": "2026-06-30", "usable_from": "2026-07-03",
        "cot_index": 12.0 if gate_on else 53.9, "gate_on": gate_on,
        "threshold": 20.0, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class PaperEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        d = Path(self._dir.name)
        self.positions_path = d / "paper_positions.json"
        self.fills_path = d / "paper_fills.jsonl"
        self.equity_path = d / "paper_equity.jsonl"
        self.forecast_state_path = d / "forecast_gate.json"
        self.forecast_audit_path = d / "forecast_audit.jsonl"
        self.forecast_history_path = d / "forecast_history.jsonl"
        self.gate_path = d / "cot_gate.json"
        self.market: dict[str, list[dict]] = {}
        self.raise_for: set[str] = set()

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _provider(self, symbol: str, timeframe: str, limit: int) -> list[dict]:
        if symbol in self.raise_for:
            raise RuntimeError(f"provider boom for {symbol}")
        return self.market.get(symbol, [])

    def _engine(self, strategies=None) -> PaperEngine:
        config = {
            "starting_balance_usd": 10_000.0,
            "strategies": strategies or [
                {"id": "btc_donchian", "engine": "donchian", "symbol": "BTC/USDT",
                 "timeframe": "4h", "risk_pct": 0.02, "params": {}},
                {"id": "eth_donchian", "engine": "donchian", "symbol": "ETH/USDT",
                 "timeframe": "1d", "risk_pct": 0.02, "params": {}},
                {"id": "gold_cot", "engine": "gold_cot", "symbol": "PAXG/USDT",
                 "timeframe": "1d", "risk_pct": 0.02, "params": {"gate_path": str(self.gate_path)}},
            ],
        }
        return PaperEngine(config, candle_provider=self._provider,
                           positions_path=self.positions_path, fills_path=self.fills_path,
                           equity_path=self.equity_path)

    def _forecast_engine(self, report: dict) -> PaperEngine:
        config = {
            "starting_balance_usd": 10_000.0,
            "forecast_gate": {"enabled": True, "history_limit": 700},
            "strategies": [{"id": "eth_donchian", "engine": "donchian", "symbol": "ETH/USDT",
                            "timeframe": "1d", "risk_pct": 0.02, "params": {}}],
        }
        return PaperEngine(
            config, candle_provider=self._provider, forecast_evaluator=lambda candles, **kwargs: report,
            positions_path=self.positions_path, fills_path=self.fills_path, equity_path=self.equity_path,
            forecast_state_path=self.forecast_state_path,
            forecast_audit_path=self.forecast_audit_path,
            forecast_history_path=self.forecast_history_path,
        )

    def _read_lines(self, path: Path) -> list[dict]:
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    def test_three_strategies_run_in_parallel(self):
        self.market = {"BTC/USDT": INSIDE, "ETH/USDT": BREAKOUT, "PAXG/USDT": _candles([4000.0] * 20)}
        self.gate_path.write_text(json.dumps(_fresh_gate(gate_on=True)))
        summary = self._engine().run_cycle()
        self.assertEqual(summary["intents"]["eth_donchian"], "open")
        self.assertEqual(summary["intents"]["gold_cot"], "open")
        self.assertEqual(summary["intents"]["btc_donchian"], "no_trade")
        self.assertEqual(set(summary["open_positions"]), {"eth_donchian", "gold_cot"})

    def test_strategy_receives_primary_and_declared_secondary_timeframes(self):
        class CaptureMtfEngine:
            name = "capture_mtf"
            version = "1"
            required_timeframes = ["15m", "1h"]
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                self.context = None

            def on_candle(self, candle, context):
                self.context = context
                return None

        register_engine("capture_mtf", CaptureMtfEngine)
        calls: list[tuple[str, str, int]] = []

        def provider(symbol: str, timeframe: str, limit: int):
            calls.append((symbol, timeframe, limit))
            seed = 100.0 if timeframe == "15m" else 200.0
            return _candles([seed] * 21)

        config = {
            "starting_balance_usd": 10_000.0,
            "strategies": [{
                "id": "btc_capture", "engine": "capture_mtf", "symbol": "BTC/USDT",
                "timeframe": "15m", "risk_pct": 0.005, "params": {},
            }],
        }
        try:
            engine = PaperEngine(
                config, candle_provider=provider,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            )
            engine.run_cycle()
            context = engine._engines["btc_capture"].context
            self.assertEqual(context.timeframe, "15m")
            self.assertEqual(context.candles[-1]["close"], 100.0)
            self.assertEqual(context.candles_by_timeframe["15m"][-1]["close"], 100.0)
            self.assertEqual(context.candles_by_timeframe["1h"][-1]["close"], 200.0)
            self.assertEqual({tf for _, tf, _ in calls}, {"15m", "1h"})
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("capture_mtf", None)

    def test_same_symbol_sleeves_can_open_below_shared_risk_caps(self):
        class AlwaysLongEngine:
            name = "always_long"
            version = "1"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                return Signal(Side.LONG, context.symbol, context.timeframe, "test long")

        register_engine("always_long", AlwaysLongEngine)
        config = {
            "starting_balance_usd": 10_000.0,
            "max_total_stop_risk_pct": 0.05,
            "max_symbol_stop_risk_pct": 0.03,
            "strategies": [
                {"id": "btc_h4", "engine": "always_long", "symbol": "BTC/USDT",
                 "timeframe": "4h", "risk_pct": 0.02, "params": {}},
                {"id": "btc_m15", "engine": "always_long", "symbol": "BTC/USDT",
                 "timeframe": "15m", "risk_pct": 0.005, "params": {}},
            ],
        }
        try:
            engine = PaperEngine(
                config, candle_provider=lambda *_: BREAKOUT,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            )
            summary = engine.run_cycle()
            self.assertEqual(set(summary["open_positions"]), {"btc_h4", "btc_m15"})
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("always_long", None)

    def test_symbol_risk_cap_blocks_only_second_same_symbol_entry(self):
        class AlwaysLongEngine:
            name = "always_long_capped"
            version = "1"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                return Signal(Side.LONG, context.symbol, context.timeframe, "test long")

        register_engine("always_long_capped", AlwaysLongEngine)
        config = {
            "starting_balance_usd": 10_000.0,
            "max_total_stop_risk_pct": 0.05,
            "max_symbol_stop_risk_pct": 0.024,
            "strategies": [
                {"id": "btc_h4", "engine": "always_long_capped", "symbol": "BTC/USDT",
                 "timeframe": "4h", "risk_pct": 0.02, "params": {}},
                {"id": "btc_m15", "engine": "always_long_capped", "symbol": "BTC/USDT",
                 "timeframe": "15m", "risk_pct": 0.005, "params": {}},
            ],
        }
        try:
            engine = PaperEngine(
                config, candle_provider=lambda *_: BREAKOUT,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            )
            summary = engine.run_cycle()
            self.assertEqual(summary["open_positions"], ["btc_h4"])
            self.assertEqual(summary["intents"]["btc_m15"], "risk_cap_symbol")
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("always_long_capped", None)

    def test_symbol_cap_uses_open_dollar_risk_not_historical_percentage(self):
        class AlwaysLongEngine:
            name = "always_long_dollar_cap"
            version = "1"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                return Signal(Side.LONG, context.symbol, context.timeframe, "test long")

        register_engine("always_long_dollar_cap", AlwaysLongEngine)
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_000.0,
            "positions": {"btc_old": {
                "strategy_id": "btc_old", "symbol": "BTC/USDT", "side": "long",
                "qty": 1.0, "entry_px": 2600.0, "notional_usd": 2600.0,
                "risk_pct": 0.02, "atr_risk": 400.0,
                "opened_ts": 1, "entry_reason": "existing",
            }},
        }))
        config = {
            "starting_balance_usd": 10_000.0,
            "max_total_stop_risk_pct": 1.0,
            "max_symbol_stop_risk_pct": 0.04,
            "strategies": [{"id": "btc_new", "engine": "always_long_dollar_cap",
                            "symbol": "BTC/USDT", "timeframe": "15m",
                            "risk_pct": 0.005, "params": {}}],
        }
        try:
            summary = PaperEngine(
                config, candle_provider=lambda *_: BREAKOUT,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            ).run_cycle()
            self.assertEqual(summary["intents"]["btc_new"], "risk_cap_symbol")
            self.assertEqual(summary["open_positions"], ["btc_old"])
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("always_long_dollar_cap", None)

    def test_cot_absence_does_not_break_other_strategies(self):
        # No gate file at all -> gold_cot no-trade; ETH must still open.
        self.market = {"BTC/USDT": INSIDE, "ETH/USDT": BREAKOUT, "PAXG/USDT": _candles([4000.0] * 20)}
        summary = self._engine().run_cycle()
        self.assertEqual(summary["intents"]["gold_cot"], "no_trade")
        self.assertNotIn("gold_cot", summary["open_positions"])
        self.assertEqual(summary["intents"]["eth_donchian"], "open")

    def test_one_strategy_raising_is_isolated(self):
        self.market = {"ETH/USDT": BREAKOUT, "PAXG/USDT": _candles([4000.0] * 20)}
        self.raise_for = {"BTC/USDT"}  # provider explodes for BTC only
        self.gate_path.write_text(json.dumps(_fresh_gate(gate_on=True)))
        summary = self._engine().run_cycle()
        self.assertIn("btc_donchian", summary["errors"])          # BTC failed…
        self.assertEqual(summary["intents"]["eth_donchian"], "open")  # …ETH unaffected
        self.assertEqual(summary["intents"]["gold_cot"], "open")

    def test_fills_written_to_single_journal(self):
        self.market = {"BTC/USDT": INSIDE, "ETH/USDT": BREAKOUT, "PAXG/USDT": _candles([4000.0] * 20)}
        self.gate_path.write_text(json.dumps(_fresh_gate(gate_on=True)))
        self._engine().run_cycle()
        fills = self._read_lines(self.fills_path)
        self.assertEqual(len(fills), 2)  # eth + gold opens, one unified journal
        self.assertEqual({f["strategy_id"] for f in fills}, {"eth_donchian", "gold_cot"})

    def test_equity_reflects_open_position_then_realizes_on_exit(self):
        eng = self._engine([{"id": "eth_donchian", "engine": "donchian", "symbol": "ETH/USDT",
                             "timeframe": "1d", "risk_pct": 0.02, "params": {}}])
        # Cycle 1: breakout -> open at 2600.
        self.market = {"ETH/USDT": BREAKOUT}
        eng.run_cycle()
        # Cycle 2: price marked higher, still above channel -> hold; equity > start.
        self.market = {"ETH/USDT": _candles([2000.0] * 20 + [2600.0, 2800.0])}
        s2 = eng.run_cycle()
        self.assertEqual(s2["intents"]["eth_donchian"], "hold")  # already holding, no re-entry
        self.assertGreater(s2["equity_usd"], 10_000.0)
        # Cycle 3: breakdown -> exit; balance realizes a loss (exit 2000 < entry 2600).
        self.market = {"ETH/USDT": BREAKDOWN}
        s3 = eng.run_cycle()
        self.assertEqual(s3["intents"]["eth_donchian"], "close")
        self.assertNotIn("eth_donchian", s3["open_positions"])
        self.assertLess(s3["balance_usd"], 10_000.0)
        self.assertEqual(len(self._read_lines(self.equity_path)), 3)  # one equity point per cycle

    def test_state_persists_across_engine_instances(self):
        self.market = {"ETH/USDT": BREAKOUT}
        strat = [{"id": "eth_donchian", "engine": "donchian", "symbol": "ETH/USDT",
                  "timeframe": "1d", "risk_pct": 0.02, "params": {}}]
        self._engine(strat).run_cycle()  # opens, saves paper_positions.json
        # A brand-new engine reads the saved account and still holds the position.
        self.market = {"ETH/USDT": _candles([2000.0] * 20 + [2600.0, 2650.0])}
        s = self._engine(strat).run_cycle()
        self.assertIn("eth_donchian", s["open_positions"])

    def test_processed_candle_persists_across_once_processes(self):
        strategy = [{"id": "btc_donchian", "engine": "donchian", "symbol": "BTC/USDT",
                     "timeframe": "15m", "risk_pct": 0.005, "entry_enabled": True, "params": {}}]
        self.market = {"BTC/USDT": BREAKOUT}

        blocked_config = {
            "starting_balance_usd": 10_000.0,
            "max_symbol_stop_risk_pct": 0.0,
            "strategies": strategy,
        }
        first = PaperEngine(
            blocked_config, candle_provider=self._provider,
            positions_path=self.positions_path, fills_path=self.fills_path,
            equity_path=self.equity_path,
        ).run_cycle()
        self.assertEqual(first["intents"]["btc_donchian"], "risk_cap_symbol")

        second = self._engine(strategy).run_cycle()
        self.assertEqual(second["intents"]["btc_donchian"], "duplicate_candle")
        self.assertEqual(second["open_positions"], [])

    def test_processed_candle_is_not_re_evaluated_when_position_is_open(self):
        strategy = [{"id": "eth_donchian", "engine": "donchian", "symbol": "ETH/USDT",
                     "timeframe": "1d", "risk_pct": 0.02, "params": {}}]
        self.market = {"ETH/USDT": BREAKOUT}
        first = self._engine(strategy).run_cycle()
        self.assertEqual(first["intents"]["eth_donchian"], "open")

        second = self._engine(strategy).run_cycle()
        self.assertEqual(second["intents"]["eth_donchian"], "duplicate_candle")
        self.assertEqual(second["open_positions"], ["eth_donchian"])

    def test_entry_disabled_blocks_new_long_but_still_allows_existing_position_to_exit(self):
        enabled = [{"id": "eth_donchian", "engine": "donchian", "symbol": "ETH/USDT",
                    "timeframe": "1d", "risk_pct": 0.02, "entry_enabled": True, "params": {}}]
        disabled = [{"id": "eth_donchian", "engine": "donchian", "symbol": "ETH/USDT",
                     "timeframe": "1d", "risk_pct": 0.02, "entry_enabled": False, "params": {}}]

        self.market = {"ETH/USDT": BREAKOUT}
        blocked = self._engine(disabled).run_cycle()
        self.assertEqual(blocked["intents"]["eth_donchian"], "entry_disabled")
        self.assertNotIn("eth_donchian", blocked["open_positions"])

        self._engine(enabled).run_cycle()
        self.assertTrue(self.positions_path.exists())
        later_breakdown = [dict(candle, ts=candle["ts"] + 100) for candle in BREAKDOWN]
        self.market = {"ETH/USDT": later_breakdown}
        closed = self._engine(disabled).run_cycle()
        self.assertEqual(closed["intents"]["eth_donchian"], "close")
        self.assertNotIn("eth_donchian", closed["open_positions"])

    def test_entry_enabled_rejects_ambiguous_non_boolean_config(self):
        strategies = [{"id": "eth_donchian", "engine": "donchian", "symbol": "ETH/USDT",
                       "timeframe": "1d", "risk_pct": 0.02, "entry_enabled": "false", "params": {}}]

        with self.assertRaisesRegex(ValueError, "entry_enabled must be boolean"):
            self._engine(strategies)

    def test_existing_ak_position_gets_audited_atr_fallback_bracket(self):
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_000.0,
            "positions": {"btc_ak_macd_4h": {
                "strategy_id": "btc_ak_macd_4h", "symbol": "BTC/USDT", "side": "long",
                "qty": 1.0, "entry_px": 100.0, "notional_usd": 100.0,
                "risk_pct": 0.02, "atr_risk": 10.0,
                "opened_ts": "2026-07-09T14:40:08+00:00", "entry_reason": "existing",
            }},
            "processed_candles": {"btc_ak_macd_4h": 20},
        }))
        strategy = [{
            "id": "btc_ak_macd_4h", "engine": "ak_macd", "symbol": "BTC/USDT",
            "timeframe": "4h", "risk_pct": 0.02, "entry_enabled": False,
            "exit_policy": "structural_bracket", "monitor_timeframe": "15m",
            "reward_risk_ratio": 1.5, "params": {},
        }]

        def provider(_symbol, timeframe, _limit):
            rows = _candles([100.0] * 21)
            rows[-1] = dict(rows[-1], ts=20, high=105.0, low=95.0, close=101.0)
            return rows

        PaperEngine(
            {"starting_balance_usd": 10_000.0, "strategies": strategy},
            candle_provider=provider,
            positions_path=self.positions_path, fills_path=self.fills_path,
            equity_path=self.equity_path,
        ).run_cycle(now=datetime(2026, 7, 13, 8, tzinfo=timezone.utc))

        position = json.loads(self.positions_path.read_text())["positions"]["btc_ak_macd_4h"]
        self.assertEqual(position["exit_policy"], "structural_bracket")
        self.assertEqual(position["monitor_timeframe"], "15m")
        self.assertEqual(position["stop_loss_price"], 90.0)
        self.assertEqual(position["take_profit_price"], 115.0)
        self.assertEqual(position["sl_basis"], "atr_fallback_migration")

    def test_protective_stop_closes_on_duplicate_primary_candle_at_frozen_level(self):
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_000.0,
            "positions": {"btc_ak_macd_4h": {
                "strategy_id": "btc_ak_macd_4h", "symbol": "BTC/USDT", "side": "long",
                "qty": 1.0, "entry_px": 100.0, "notional_usd": 100.0,
                "risk_pct": 0.02, "atr_risk": 10.0,
                "opened_ts": "2026-07-09T14:40:08+00:00", "entry_reason": "existing",
                "exit_policy": "structural_bracket", "monitor_timeframe": "15m",
                "stop_loss_price": 90.0, "take_profit_price": 115.0,
                "sl_basis": "baseline", "reward_risk_ratio": 1.5,
            }},
            "processed_candles": {"btc_ak_macd_4h": 20},
        }))
        strategy = [{
            "id": "btc_ak_macd_4h", "engine": "ak_macd", "symbol": "BTC/USDT",
            "timeframe": "4h", "risk_pct": 0.02, "entry_enabled": False,
            "exit_policy": "structural_bracket", "monitor_timeframe": "15m",
            "reward_risk_ratio": 1.5, "params": {},
        }]

        def provider(_symbol, timeframe, _limit):
            rows = _candles([100.0] * 21)
            if timeframe == "4h":
                rows[-1] = dict(rows[-1], ts=20, high=103.0, low=97.0, close=100.0)
            else:
                rows[-1] = dict(rows[-1], ts=21, high=116.0, low=89.0, close=100.0)
            return rows

        summary = PaperEngine(
            {"starting_balance_usd": 10_000.0, "strategies": strategy},
            candle_provider=provider,
            positions_path=self.positions_path, fills_path=self.fills_path,
            equity_path=self.equity_path,
        ).run_cycle(now=datetime(2026, 7, 13, 8, 15, tzinfo=timezone.utc))

        self.assertEqual(summary["intents"]["btc_ak_macd_4h"], "close_stop_loss")
        self.assertEqual(summary["fills"][0]["reason"], "stop_loss")
        self.assertEqual(summary["fills"][0]["price"], 90.0)
        self.assertEqual(summary["open_positions"], [])

    def test_protective_monitor_replays_all_closed_candles_after_an_outage(self):
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_000.0,
            "positions": {"btc_ak_macd_4h": {
                "strategy_id": "btc_ak_macd_4h", "symbol": "BTC/USDT", "side": "long",
                "qty": 1.0, "entry_px": 100.0, "notional_usd": 100.0,
                "risk_pct": 0.02, "atr_risk": 10.0,
                "opened_ts": "2026-07-09T14:40:08+00:00", "entry_reason": "existing",
                "exit_policy": "structural_bracket", "monitor_timeframe": "15m",
                "stop_loss_price": 90.0, "take_profit_price": 115.0,
                "sl_basis": "baseline", "reward_risk_ratio": 1.5,
                "last_monitor_candle_ts": 20,
            }},
            "processed_candles": {"btc_ak_macd_4h": 20},
        }))
        strategy = [{
            "id": "btc_ak_macd_4h", "engine": "ak_macd", "symbol": "BTC/USDT",
            "timeframe": "4h", "risk_pct": 0.02, "entry_enabled": False,
            "exit_policy": "structural_bracket", "monitor_timeframe": "15m",
            "reward_risk_ratio": 1.5, "params": {"allow_short": False},
        }]

        def provider(_symbol, timeframe, _limit):
            if timeframe == "4h":
                rows = _candles([100.0] * 21)
                rows[-1] = dict(rows[-1], ts=20, high=103.0, low=97.0, close=100.0)
                return rows
            rows = _candles([100.0] * 23)
            rows[-2] = dict(rows[-2], ts=21, high=101.0, low=89.0, close=95.0)
            rows[-1] = dict(rows[-1], ts=22, high=103.0, low=94.0, close=100.0)
            return rows

        summary = PaperEngine(
            {"starting_balance_usd": 10_000.0, "strategies": strategy},
            candle_provider=provider,
            positions_path=self.positions_path, fills_path=self.fills_path,
            equity_path=self.equity_path,
        ).run_cycle(now=datetime(2026, 7, 13, 9, tzinfo=timezone.utc))

        self.assertEqual(summary["intents"]["btc_ak_macd_4h"], "close_stop_loss")
        self.assertEqual(summary["fills"][0]["price"], 90.0)
        self.assertEqual(summary["open_positions"], [])

    def test_new_signal_exit_policy_persists_suggested_stop_without_fake_tp(self):
        class SuggestedStopEngine:
            name = "suggested_stop_test"
            version = "1"
            required_timeframes = ["15m"]
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                return Signal(
                    Side.LONG, context.symbol, context.timeframe, "test long",
                    suggested_stop=95.0,
                )

        register_engine("suggested_stop_test", SuggestedStopEngine)
        self.market = {"BTC/USDT": BREAKOUT}
        strategy = [{
            "id": "btc_utbot", "engine": "suggested_stop_test", "symbol": "BTC/USDT",
            "timeframe": "15m", "risk_pct": 0.005, "entry_enabled": True,
            "exit_policy": "signal_or_stop", "monitor_timeframe": "15m", "params": {},
        }]
        try:
            summary = self._engine(strategy).run_cycle()
            self.assertEqual(summary["intents"]["btc_utbot"], "open")
            position = json.loads(self.positions_path.read_text())["positions"]["btc_utbot"]
            self.assertEqual(position["stop_loss_price"], 95.0)
            self.assertIsNone(position["take_profit_price"])
            self.assertEqual(position["exit_policy"], "signal_or_stop")
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("suggested_stop_test", None)

    def test_signal_fill_uses_primary_close_while_equity_marks_monitor_close(self):
        class PrimaryPriceEngine:
            name = "primary_price_test"
            version = "1"
            required_timeframes = ["15m"]
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                return Signal(Side.LONG, context.symbol, context.timeframe, "primary close")

        register_engine("primary_price_test", PrimaryPriceEngine)

        def provider(_symbol, timeframe, _limit):
            return BREAKOUT if timeframe == "4h" else _candles([2500.0] * 21)

        strategy = [{
            "id": "btc_h4", "engine": "primary_price_test", "symbol": "BTC/USDT",
            "timeframe": "4h", "risk_pct": 0.005, "entry_enabled": True,
            "exit_policy": "strategy_signal", "monitor_timeframe": "15m", "params": {},
        }]
        try:
            summary = PaperEngine(
                {"starting_balance_usd": 10_000.0, "strategies": strategy},
                candle_provider=provider,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            ).run_cycle()

            self.assertEqual(summary["fills"][0]["price"], 2600.0)
            position = json.loads(self.positions_path.read_text())["positions"]["btc_h4"]
            self.assertEqual(position["entry_px"], 2600.0)
            self.assertLess(summary["equity_usd"], summary["balance_usd"])
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("primary_price_test", None)

    def test_active_forecast_gate_resizes_new_entry_and_records_counterfactual(self):
        report = {
            "active": True, "origin_ts": "2026-07-11T00:00:00+00:00", "horizons": {
                "12": {"quantiles": {"p10": -0.01, "p25": 0.002, "p50": 0.01, "p75": 0.02, "p90": 0.03}, "metrics": {"direction_accuracy": 0.56}},
                "24": {"quantiles": {"p10": -0.015, "p25": 0.003, "p50": 0.015, "p75": 0.03, "p90": 0.05}, "metrics": {"direction_accuracy": 0.58}},
            },
        }
        self.market = {"ETH/USDT": BREAKOUT}
        summary = self._forecast_engine(report).run_cycle()

        self.assertEqual(summary["intents"]["eth_donchian"], "open")
        fill = summary["fills"][0]
        self.assertAlmostEqual(fill["risk_pct"], 0.023)
        audit = self._read_lines(Path(self._dir.name) / "forecast_audit.jsonl")[0]
        self.assertEqual(audit["baseline_intent"], "open")
        self.assertEqual(audit["action"], "boost")
        self.assertEqual(audit["multiplier"], 1.15)

    def test_forecast_veto_blocks_only_new_entry(self):
        report = {
            "active": True, "horizons": {
                "12": {"quantiles": {"p10": -0.04, "p25": -0.03, "p50": -0.01, "p75": 0, "p90": 0.01}, "metrics": {"direction_accuracy": 0.56}},
                "24": {"quantiles": {"p10": -0.05, "p25": -0.04, "p50": -0.02, "p75": -0.01, "p90": -0.001}, "metrics": {"direction_accuracy": 0.57}},
            },
        }
        self.market = {"ETH/USDT": BREAKOUT}
        summary = self._forecast_engine(report).run_cycle()
        self.assertEqual(summary["intents"]["eth_donchian"], "forecast_veto")
        self.assertEqual(summary["fills"], [])
        self.assertEqual(summary["open_positions"], [])

    def test_forecast_state_refreshes_without_an_entry_signal(self):
        report = {"active": False, "horizons": {}, "lock_reasons": ["quality lock"]}
        self.market = {"ETH/USDT": INSIDE}
        engine = self._forecast_engine(report)

        summary = engine.run_cycle()

        self.assertEqual(summary["intents"]["eth_donchian"], "no_trade")
        state = json.loads((Path(self._dir.name) / "forecast_gate.json").read_text())
        self.assertEqual(state["assets"]["ETH/USDT"]["lock_reasons"], ["quality lock"])
        self.assertEqual(state["strategies"]["eth_donchian"]["lock_reasons"], ["quality lock"])
        self.assertFalse((Path(self._dir.name) / "forecast_audit.jsonl").exists())

    def test_forecast_state_merge_preserves_other_strategy_on_duplicate_cycle(self):
        self.forecast_state_path.write_text(json.dumps({
            "updated_at": "2026-07-13T05:00:00+00:00",
            "assets": {"BTC/USDT": {"strategy_id": "btc_ak_macd_4h", "active": False}},
            "strategies": {"btc_ak_macd_4h": {
                "strategy_id": "btc_ak_macd_4h", "active": False,
                "lock_reasons": ["btc quality lock"],
            }},
        }))
        report = {"active": False, "horizons": {}, "lock_reasons": ["eth quality lock"]}
        self.market = {"ETH/USDT": INSIDE}
        engine = self._forecast_engine(report)

        engine.run_cycle()
        engine.run_cycle()

        state = json.loads(self.forecast_state_path.read_text())
        self.assertEqual(
            set(state["strategies"]), {"btc_ak_macd_4h", "eth_donchian"}
        )
        self.assertEqual(
            state["strategies"]["btc_ak_macd_4h"]["lock_reasons"],
            ["btc quality lock"],
        )

    def test_forecast_history_archives_once_per_six_hour_bucket(self):
        report = {
            "active": False,
            "origin_ts": "2026-07-13T06:00:00+00:00",
            "origin_price": 100.0,
            "horizons": {
                "6": {"quantiles": {"p10": -0.02, "p50": 0.01, "p90": 0.03}},
                "12": {"quantiles": {"p10": -0.03, "p50": 0.02, "p90": 0.04}},
                "24": {"quantiles": {"p10": -0.04, "p50": 0.03, "p90": 0.05}},
            },
            "lock_reasons": ["quality lock"],
        }
        self.market = {"ETH/USDT": INSIDE}
        engine = self._forecast_engine(report)

        engine.run_cycle(now=datetime(2026, 7, 13, 6, 10, tzinfo=timezone.utc))
        engine.run_cycle(now=datetime(2026, 7, 13, 6, 25, tzinfo=timezone.utc))

        records = self._read_lines(self.forecast_history_path)
        predictions = [row for row in records if row["record_type"] == "prediction"]
        self.assertEqual(len(predictions), 1)
        self.assertEqual(predictions[0]["bucket_ts"], "2026-07-13T06:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
