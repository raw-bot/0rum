import json
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from orum.strategies.cot_gate import read_gate as actual_read_gate
from pathlib import Path
from unittest.mock import patch

from orum.portfolio.paper_broker import Account, Position
from orum.portfolio.paper_engine import PaperEngine, _entry_risk_distance
from orum.portfolio.position_manager import DynamicExitPolicy, DynamicExitState
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
        "threshold": 20.0, "updated_at": "2026-07-08T00:00:00+00:00",
    }


class PaperEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        gate_clock = patch("orum.strategies.gold_cot.read_gate", side_effect=lambda *a, **kw: actual_read_gate(*a, now=datetime(2026, 7, 8, tzinfo=timezone.utc), **kw))
        gate_clock.start()
        self.addCleanup(gate_clock.stop)
        self._dir = tempfile.TemporaryDirectory()
        d = Path(self._dir.name)
        self.positions_path = d / "paper_positions.json"
        self.fills_path = d / "paper_fills.jsonl"
        self.equity_path = d / "paper_equity.jsonl"
        self.gate_path = d / "cot_gate.json"
        self.market: dict[str, list[dict]] = {}
        self.raise_for: set[str] = set()

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _provider(self, symbol: str, timeframe: str, limit: int) -> list[dict]:
        if symbol in self.raise_for:
            raise RuntimeError(f"provider boom for {symbol}")
        rows = self.market.get(symbol, [])
        if symbol == "PAXG/USDT":
            return [{**row, "ts": 1783382400000 + i * 1000} for i, row in enumerate(rows)]
        return rows

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

    def test_short_risk_distance_and_static_bracket_are_directional(self):
        self.assertEqual(_entry_risk_distance(100.0, 110.0, 8.0, side="short"), 10.0)
        self.assertIsNone(_entry_risk_distance(100.0, 90.0, 8.0, side="short"))
        self.assertIsNone(
            _entry_risk_distance(100.0, "not-a-number", 8.0, side="short")
        )
        position = Position(
            strategy_id="ak",
            symbol="BTC/USDT",
            side="short",
            qty=1.0,
            entry_px=100.0,
            notional_usd=100.0,
            risk_pct=0.01,
            atr_risk=10.0,
            stop_loss_price=110.0,
            take_profit_price=80.0,
        )

        self.assertEqual(
            PaperEngine._static_protective_exit(
                position, {"open": 100.0, "high": 111.0, "low": 79.0, "close": 90.0}
            ),
            ("stop_loss", 110.0),
        )

    def test_short_exit_metadata_migration_mirrors_long_geometry(self):
        strategy = [{
            "id": "ak", "engine": "donchian", "symbol": "BTC/USDT",
            "timeframe": "4h", "risk_pct": 0.02,
            "exit_policy": "structural_bracket", "reward_risk_ratio": 2.0,
            "params": {},
        }]
        engine = self._engine(strategy)
        account = Account(
            balance_usd=10_000.0,
            positions={"ak": Position(
                strategy_id="ak", symbol="BTC/USDT", side="short", qty=1.0,
                entry_px=100.0, notional_usd=100.0, risk_pct=0.02,
                atr_risk=10.0, risk_distance=10.0,
            )},
        )

        engine._migrate_exit_metadata(account)

        position = account.positions["ak"]
        self.assertEqual(position.stop_loss_price, 110.0)
        self.assertEqual(position.take_profit_price, 80.0)
        self.assertEqual(position.reward_risk_ratio, 2.0)

    def test_auction_reverses_long_to_short_on_the_signal_candle(self):
        class ReversingEngine:
            name = "paper_short_reversal_test"
            version = "1"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                self.calls = 0

            def on_candle(self, candle, context):
                self.calls += 1
                price = float(candle["close"])
                if self.calls == 1:
                    return Signal(
                        Side.LONG, context.symbol, context.timeframe, "long pulse",
                        suggested_stop=price - 100.0,
                        suggested_take_profit=price + 200.0,
                    )
                return Signal(
                    Side.SHORT, context.symbol, context.timeframe, "short pulse",
                    suggested_stop=price + 100.0,
                    suggested_take_profit=price - 200.0,
                )

        register_engine("paper_short_reversal_test", ReversingEngine)
        strategy = [{
            "id": "ak", "engine": "paper_short_reversal_test", "symbol": "BTC/USDT",
            "timeframe": "4h", "risk_pct": 0.02, "entry_enabled": True,
            "exit_policy": "structural_bracket", "monitor_timeframe": "4h",
            "reward_risk_ratio": 2.0,
            "dynamic_exit": {
                "mode": "execute", "version": "mfe_ratchet_v1",
                "activation_r": 1.0, "giveback_r": 0.5, "floor_r": 0.1,
            },
            "params": {},
        }]
        config = {
            "starting_balance_usd": 10_000.0,
            "reentry_policy": "topup",
            "strategies": strategy,
        }
        try:
            self.market = {"BTC/USDT": BREAKOUT}
            engine = PaperEngine(
                config, candle_provider=self._provider,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            )
            first = engine.run_cycle()
            self.assertEqual(first["intents"]["ak"], "open")

            next_market = [dict(candle) for candle in BREAKOUT]
            next_market[-1] = dict(next_market[-1], ts=21, close=2500.0)
            self.market = {"BTC/USDT": next_market}
            second = engine.run_cycle()

            state = json.loads(self.positions_path.read_text())
            self.assertEqual(second["intents"]["ak"], "reverse_open")
            self.assertEqual([fill["action"] for fill in second["fills"]], ["close", "open"])
            self.assertEqual([fill["side"] for fill in second["fills"]], ["long", "short"])
            self.assertEqual(state["positions"]["ak"]["side"], "short")
            self.assertNotIn("dynamic_exit", state["positions"]["ak"])
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("paper_short_reversal_test", None)

    def test_short_without_complete_bracket_fails_before_candle_is_processed(self):
        class MalformedShortEngine:
            name = "paper_short_malformed_test"
            version = "1"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                return Signal(
                    Side.SHORT, context.symbol, context.timeframe, "bad short",
                    suggested_stop=float(candle["close"]) + 100.0,
                    suggested_take_profit=None,
                )

        register_engine("paper_short_malformed_test", MalformedShortEngine)
        strategy = [{
            "id": "ak", "engine": "paper_short_malformed_test", "symbol": "BTC/USDT",
            "timeframe": "4h", "risk_pct": 0.02, "entry_enabled": True,
            "exit_policy": "structural_bracket", "monitor_timeframe": "4h",
            "reward_risk_ratio": 2.0, "params": {},
        }]
        try:
            self.market = {"BTC/USDT": BREAKOUT}
            summary = PaperEngine(
                {"starting_balance_usd": 10_000.0, "reentry_policy": "topup", "strategies": strategy},
                candle_provider=self._provider,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            ).run_cycle()
            state = json.loads(self.positions_path.read_text())

            self.assertEqual(summary["intents"]["ak"], "invalid_bracket")
            self.assertEqual(state["positions"], {})
            self.assertNotIn("ak", state["processed_candles"])
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("paper_short_malformed_test", None)

    def test_legacy_reversal_closes_every_opposite_tranche(self):
        class AlwaysShortEngine:
            name = "paper_legacy_short_reversal_test"
            version = "1"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                price = float(candle["close"])
                return Signal(
                    Side.SHORT, context.symbol, context.timeframe, "short pulse",
                    suggested_stop=price + 100.0,
                    suggested_take_profit=price - 200.0,
                )

        register_engine("paper_legacy_short_reversal_test", AlwaysShortEngine)
        base = {
            "strategy_id": "ak", "symbol": "BTC/USDT", "side": "long",
            "qty": 1.0, "entry_px": 2600.0, "notional_usd": 2600.0,
            "risk_pct": 0.01, "atr_risk": 100.0,
        }
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_000.0,
            "positions": {"ak": base, "ak::t2": base},
        }))
        strategy = [{
            "id": "ak", "engine": "paper_legacy_short_reversal_test",
            "symbol": "BTC/USDT", "timeframe": "4h", "risk_pct": 0.02,
            "entry_enabled": True, "exit_policy": "structural_bracket",
            "params": {},
        }]
        try:
            self.market = {"BTC/USDT": BREAKOUT}
            summary = self._engine(strategy).run_cycle()
            state = json.loads(self.positions_path.read_text())

            self.assertEqual(summary["intents"]["ak"], "reverse_open")
            self.assertEqual(
                [fill["action"] for fill in summary["fills"]],
                ["close", "close", "open"],
            )
            self.assertEqual(state["positions"]["ak"]["side"], "short")
            self.assertNotIn("ak::t2", state["positions"])
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("paper_legacy_short_reversal_test", None)

    def test_legacy_exit_closes_every_hydrated_tranche(self):
        class AlwaysExitEngine:
            name = "paper_legacy_exit_all_test"
            version = "1"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                return Signal(
                    Side.EXIT, context.symbol, context.timeframe, "exit all"
                )

        register_engine("paper_legacy_exit_all_test", AlwaysExitEngine)
        base = {
            "strategy_id": "ak", "symbol": "BTC/USDT", "side": "long",
            "qty": 1.0, "entry_px": 2600.0, "notional_usd": 2600.0,
            "risk_pct": 0.01, "atr_risk": 100.0,
        }
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_000.0,
            "positions": {"ak": base, "ak::t2": base},
        }))
        strategy = [{
            "id": "ak", "engine": "paper_legacy_exit_all_test",
            "symbol": "BTC/USDT", "timeframe": "4h", "risk_pct": 0.02,
            "entry_enabled": True, "params": {},
        }]
        try:
            self.market = {"BTC/USDT": BREAKOUT}
            summary = self._engine(strategy).run_cycle()
            state = json.loads(self.positions_path.read_text())

            self.assertEqual(summary["intents"]["ak"], "close")
            self.assertEqual(len(summary["fills"]), 2)
            self.assertEqual(state["positions"], {})
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("paper_legacy_exit_all_test", None)

    def test_reversal_rechecks_risk_caps_after_realizing_loss(self):
        class AlwaysShortEngine:
            name = "paper_short_post_loss_cap_test"
            version = "1"
            required_timeframes = ["15m"]
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                price = float(candle["close"])
                return Signal(
                    Side.SHORT, context.symbol, context.timeframe, "short pulse",
                    suggested_stop=price + 10.0,
                    suggested_take_profit=price - 20.0,
                )

        register_engine("paper_short_post_loss_cap_test", AlwaysShortEngine)
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_000.0,
            "positions": {
                "ak": {
                    "strategy_id": "ak", "symbol": "BTC/USDT", "side": "long",
                    "qty": 20.0, "entry_px": 100.0, "notional_usd": 2000.0,
                    "risk_pct": 0.02, "atr_risk": 10.0,
                },
                "other": {
                    "strategy_id": "other", "symbol": "ETH/USDT", "side": "long",
                    "qty": 48.0, "entry_px": 100.0, "notional_usd": 4800.0,
                    "risk_pct": 0.048, "atr_risk": 10.0,
                },
            },
        }))
        primary = _candles([100.0] * 20 + [80.0])
        monitor = _candles([100.0] * 21)

        def provider(_symbol, timeframe, _limit):
            return primary if timeframe == "4h" else monitor

        strategy = [{
            "id": "ak", "engine": "paper_short_post_loss_cap_test",
            "symbol": "BTC/USDT", "timeframe": "4h", "risk_pct": 0.02,
            "entry_enabled": True, "exit_policy": "structural_bracket",
            "monitor_timeframe": "15m", "params": {},
        }]
        try:
            summary = PaperEngine(
                {
                    "starting_balance_usd": 10_000.0,
                    "max_total_stop_risk_pct": 0.05,
                    "max_symbol_stop_risk_pct": 1.0,
                    "reentry_policy": "topup",
                    "strategies": strategy,
                },
                candle_provider=provider,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            ).run_cycle()
            state = json.loads(self.positions_path.read_text())

            self.assertEqual(summary["intents"]["ak"], "risk_cap_total")
            self.assertEqual([fill["action"] for fill in summary["fills"]], ["close"])
            self.assertNotIn("ak", state["positions"])
            self.assertIn("other", state["positions"])
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("paper_short_post_loss_cap_test", None)

    def test_disabled_opposite_entry_still_closes_existing_direction(self):
        class AlwaysShortEngine:
            name = "paper_disabled_reverse_test"
            version = "1"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                price = float(candle["close"])
                return Signal(
                    Side.SHORT, context.symbol, context.timeframe, "short pulse",
                    suggested_stop=price + 100.0,
                    suggested_take_profit=price - 200.0,
                )

        register_engine("paper_disabled_reverse_test", AlwaysShortEngine)
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_000.0,
            "positions": {"ak": {
                "strategy_id": "ak", "symbol": "BTC/USDT", "side": "long",
                "qty": 1.0, "entry_px": 2600.0, "notional_usd": 2600.0,
                "risk_pct": 0.01, "atr_risk": 100.0,
            }},
        }))
        strategy = [{
            "id": "ak", "engine": "paper_disabled_reverse_test",
            "symbol": "BTC/USDT", "timeframe": "4h", "risk_pct": 0.02,
            "entry_enabled": False, "exit_policy": "structural_bracket",
            "params": {},
        }]
        try:
            self.market = {"BTC/USDT": BREAKOUT}
            summary = PaperEngine(
                {
                    "starting_balance_usd": 10_000.0,
                    "reentry_policy": "topup",
                    "strategies": strategy,
                },
                candle_provider=self._provider,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            ).run_cycle()
            state = json.loads(self.positions_path.read_text())

            self.assertEqual(summary["intents"]["ak"], "reverse_close")
            self.assertEqual([fill["action"] for fill in summary["fills"]], ["close"])
            self.assertEqual(state["positions"], {})
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("paper_disabled_reverse_test", None)

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

    def test_invalid_reward_risk_is_rejected(self):
        strategy = [{
            "id": "ak", "engine": "ak_macd", "symbol": "BTC/USDT",
            "timeframe": "4h", "risk_pct": 0.02,
            "reward_risk_ratio": float("inf"), "params": {},
        }]

        with self.assertRaisesRegex(ValueError, "finite and positive"):
            self._engine(strategy)

        strategy[0]["reward_risk_ratio"] = True
        with self.assertRaisesRegex(ValueError, "finite and positive"):
            self._engine(strategy)

    def test_corrupt_account_state_fails_closed(self):
        self.positions_path.write_text('{"balance_usd":')

        with self.assertRaisesRegex(RuntimeError, "account state is unreadable"):
            self._engine([]).run_cycle()

        self.positions_path.write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "invalid root schema"):
            self._engine([]).run_cycle()

        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_000.0,
            "positions": {"ak": "corrupt"},
        }))
        with self.assertRaisesRegex(RuntimeError, "positions schema"):
            self._engine([]).run_cycle()

    def test_monitor_gap_detection_does_not_trust_short_response(self):
        self.assertTrue(PaperEngine._monitor_history_has_gap(
            [{"ts": 1_800_000}], cursor=0, timeframe="15m"
        ))
        self.assertTrue(PaperEngine._monitor_history_has_gap(
            [{"ts": 900_000}, {"ts": 2_700_000}],
            cursor=0, timeframe="15m",
        ))
        self.assertFalse(PaperEngine._monitor_history_has_gap(
            [{"ts": 900_000}, {"ts": 1_800_000}],
            cursor=0, timeframe="15m",
        ))

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

    def test_ak_reward_risk_config_reaches_bracket_engine(self):
        strategy = [{
            "id": "btc_ak_macd_4h", "engine": "ak_macd", "symbol": "BTC/USDT",
            "timeframe": "4h", "risk_pct": 0.02, "reward_risk_ratio": 2.0,
            "params": {},
        }]

        engine = self._engine(strategy)

        self.assertEqual(
            engine._engines["btc_ak_macd_4h"]._reward_risk_ratio, 2.0
        )

    def test_dynamic_stop_beats_static_tp_on_same_candle(self):
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_000.0,
            "positions": {"ak": {
                "strategy_id": "ak", "position_id": "ak", "symbol": "BTC/USDT",
                "side": "long", "qty": 1.0, "entry_px": 100.0,
                "notional_usd": 100.0, "risk_pct": 0.02, "atr_risk": 10.0,
                "risk_distance": 10.0, "opened_ts": 1, "entry_reason": "existing",
                "exit_policy": "structural_bracket", "monitor_timeframe": "15m",
                "stop_loss_price": 90.0, "take_profit_price": 115.0,
                "last_monitor_candle_ts": 20,
                "dynamic_exit": {
                    "policy": {
                        "mode": "execute", "version": "ak_mfe_ssl_v1",
                        "activation_r": 1.0, "giveback_r": 0.5, "floor_r": 0.1,
                        "ema_len": 3, "atr_len": 2, "ssl_atr_mult": 1.0,
                        "close_on_ssl_invalidation": True,
                    },
                    "peak_favorable_price": 120.0, "mfe_r": 2.0,
                    "dynamic_stop_price": 107.0, "armed": True,
                    "last_candle_ts": 20,
                },
            }},
            "processed_candles": {"ak": 19},
        }))
        strategy = [{
            "id": "ak", "engine": "ak_macd", "symbol": "BTC/USDT",
            "timeframe": "4h", "risk_pct": 0.02, "entry_enabled": False,
            "exit_policy": "structural_bracket", "monitor_timeframe": "15m",
            "reward_risk_ratio": 1.5, "params": {},
        }]

        def provider(_symbol, timeframe, _limit):
            rows = _candles([100.0] * 22)
            if timeframe == "4h":
                rows[-1] = dict(rows[-1], ts=20, close=100.0)
            else:
                rows[-1] = dict(
                    rows[-1], ts=21, open=110.0, low=106.0,
                    high=116.0, close=110.0,
                )
            return rows

        summary = PaperEngine(
            {"starting_balance_usd": 10_000.0, "reentry_policy": "hold",
             "strategies": strategy},
            candle_provider=provider,
            positions_path=self.positions_path, fills_path=self.fills_path,
            equity_path=self.equity_path,
        ).run_cycle(now=datetime(2026, 7, 13, 8, 15, tzinfo=timezone.utc))

        self.assertEqual(summary["intents"]["ak"], "close_dynamic_stop")
        self.assertEqual(summary["fills"][0]["price"], 107.0)
        self.assertEqual(summary["fills"][0]["reason"], "dynamic_stop")
        self.assertTrue(summary["fills"][0]["fill_id"])
        self.assertEqual(len(self._read_lines(self.fills_path)), 1)
        saved = json.loads(self.positions_path.read_text())
        self.assertEqual(saved["processed_candles"]["ak"], 20)

    def test_adaptive_target_replaces_original_tp_only_after_persisted_extension(self):
        policy = DynamicExitPolicy(
            mode="execute", adaptive_target=True, ema_len=4, atr_len=2,
        )
        state = DynamicExitState.initial(
            policy, entry_price=100.0, last_candle_ts=20,
            initial_target_price=120.0, atr_risk=10.0,
        )
        state = DynamicExitState(**{
            **state.__dict__, "active_target_price": 130.0,
            "active_target_r": 3.0, "target_extensions": 1,
        })
        position = Position(
            strategy_id="ak", position_id="ak", symbol="BTC/USDT",
            side="long", qty=1.0, entry_px=100.0, notional_usd=100.0,
            risk_pct=0.02, atr_risk=10.0, risk_distance=10.0,
            stop_loss_price=90.0, take_profit_price=120.0,
            dynamic_exit=state.to_dict(),
        )
        self.assertIsNone(PaperEngine._protective_exit(
            position,
            {"open": 115.0, "high": 125.0, "low": 110.0, "close": 124.0},
        ))
        self.assertEqual(PaperEngine._protective_exit(
            position,
            {"open": 125.0, "high": 131.0, "low": 124.0, "close": 130.0},
        ), ("take_profit", 130.0))

    def test_observe_runner_never_moves_the_real_take_profit(self):
        policy = DynamicExitPolicy(
            mode="observe", adaptive_target=True, ema_len=4, atr_len=2,
        )
        state = DynamicExitState.initial(
            policy, entry_price=100.0, last_candle_ts=20,
            initial_target_price=120.0, atr_risk=10.0,
        )
        state = DynamicExitState(**{
            **state.__dict__, "active_target_price": 130.0,
            "active_target_r": 3.0, "target_extensions": 1,
        })
        position = Position(
            strategy_id="ak", position_id="ak", symbol="BTC/USDT",
            side="long", qty=1.0, entry_px=100.0, notional_usd=100.0,
            risk_pct=0.02, atr_risk=10.0, risk_distance=10.0,
            stop_loss_price=90.0, take_profit_price=120.0,
            dynamic_exit=state.to_dict(),
        )
        self.assertEqual(PaperEngine._protective_exit(
            position,
            {"open": 115.0, "high": 125.0, "low": 110.0, "close": 124.0},
        ), ("take_profit", 120.0))

    def test_observe_shadows_dynamic_stop_before_real_static_tp(self):
        dynamic = {
            "policy": {
                "mode": "observe", "version": "ak_mfe_ssl_v1",
                "activation_r": 1.0, "giveback_r": 0.5, "floor_r": 0.1,
                "ema_len": 3, "atr_len": 2, "ssl_atr_mult": 1.0,
                "close_on_ssl_invalidation": True,
            },
            "peak_favorable_price": 120.0, "mfe_r": 2.0,
            "dynamic_stop_price": 107.0, "armed": True,
            "last_candle_ts": 20,
        }
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_000.0,
            "positions": {"ak": {
                "strategy_id": "ak", "position_id": "ak", "symbol": "BTC/USDT",
                "side": "long", "qty": 1.0, "entry_px": 100.0,
                "notional_usd": 100.0, "risk_pct": 0.02, "atr_risk": 10.0,
                "risk_distance": 10.0, "opened_ts": 1, "entry_reason": "existing",
                "exit_policy": "structural_bracket", "monitor_timeframe": "15m",
                "stop_loss_price": 90.0, "take_profit_price": 115.0,
                "last_monitor_candle_ts": 20, "dynamic_exit": dynamic,
            }},
            "processed_candles": {"ak": 20},
        }))
        strategy = [{
            "id": "ak", "engine": "ak_macd", "symbol": "BTC/USDT",
            "timeframe": "4h", "risk_pct": 0.02, "entry_enabled": False,
            "exit_policy": "structural_bracket", "monitor_timeframe": "15m",
            "reward_risk_ratio": 1.5, "params": {},
        }]

        def provider(_symbol, timeframe, _limit):
            rows = _candles([100.0] * 22)
            if timeframe == "4h":
                rows[-1] = dict(rows[-1], ts=20, close=100.0)
            else:
                rows[-1] = dict(
                    rows[-1], ts=21, open=110.0, low=106.0,
                    high=116.0, close=110.0,
                )
            return rows

        summary = PaperEngine(
            {"starting_balance_usd": 10_000.0, "reentry_policy": "hold",
             "strategies": strategy},
            candle_provider=provider,
            positions_path=self.positions_path, fills_path=self.fills_path,
            equity_path=self.equity_path,
        ).run_cycle()

        self.assertEqual(summary["fills"][0]["reason"], "take_profit")
        decisions = self._read_lines(
            self.fills_path.with_name("paper_dynamic_exits.jsonl")
        )
        self.assertEqual(decisions[-1]["reason"], "dynamic_stop")
        self.assertEqual(decisions[-1]["desired_price"], 107.0)

    def test_pending_dynamic_fill_recovers_without_duplicate(self):
        pending = {
            "fill_id": "stable-fill", "action": "close", "strategy_id": "ak",
            "position_id": "ak", "price": 110.0,
        }
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_100.0,
            "positions": {},
            "processed_candles": {},
            "pending_fills": [pending],
        }))
        self.fills_path.write_text(json.dumps(pending) + "\n")
        engine = PaperEngine(
            {"starting_balance_usd": 10_000.0, "strategies": []},
            candle_provider=self._provider,
            positions_path=self.positions_path, fills_path=self.fills_path,
            equity_path=self.equity_path,
        )

        engine.run_cycle()

        self.assertEqual(len(self._read_lines(self.fills_path)), 1)
        saved = json.loads(self.positions_path.read_text())
        self.assertNotIn("pending_fills", saved)
        self.assertEqual(saved["balance_usd"], 10_100.0)

    def test_pending_dynamic_fill_is_published_after_account_commit(self):
        pending = {
            "fill_id": "missing-fill", "action": "close", "strategy_id": "ak",
            "position_id": "ak", "price": 110.0,
        }
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_100.0, "positions": {},
            "processed_candles": {}, "pending_fills": [pending],
        }))
        engine = PaperEngine(
            {"starting_balance_usd": 10_000.0, "strategies": []},
            candle_provider=self._provider,
            positions_path=self.positions_path, fills_path=self.fills_path,
            equity_path=self.equity_path,
        )

        engine.run_cycle()

        self.assertEqual(self._read_lines(self.fills_path), [pending])
        self.assertNotIn(
            "pending_fills", json.loads(self.positions_path.read_text())
        )

    def test_pending_fill_repairs_torn_jsonl_tail(self):
        pending = {
            "fill_id": "after-torn-tail", "action": "close",
            "strategy_id": "ak", "position_id": "ak", "price": 110.0,
        }
        self.positions_path.write_text(json.dumps({
            "balance_usd": 10_100.0, "positions": {},
            "processed_candles": {}, "pending_fills": [pending],
        }))
        self.fills_path.write_bytes(b'{"fill_id":"torn"')
        engine = PaperEngine(
            {"starting_balance_usd": 10_000.0, "strategies": []},
            candle_provider=self._provider,
            positions_path=self.positions_path, fills_path=self.fills_path,
            equity_path=self.equity_path,
        )

        engine.run_cycle()

        self.assertEqual(self._read_lines(self.fills_path), [pending])

    def test_static_short_fill_recovers_after_publish_crash_without_duplicate(self):
        class AlwaysShortEngine:
            name = "paper_static_outbox_test"
            version = "1"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                price = float(candle["close"])
                return Signal(
                    Side.SHORT, context.symbol, context.timeframe, "short pulse",
                    suggested_stop=price + 100.0,
                    suggested_take_profit=price - 200.0,
                )

        register_engine("paper_static_outbox_test", AlwaysShortEngine)
        strategy = [{
            "id": "ak", "engine": "paper_static_outbox_test",
            "symbol": "BTC/USDT", "timeframe": "4h", "risk_pct": 0.02,
            "entry_enabled": True, "exit_policy": "structural_bracket",
            "params": {},
        }]
        config = {
            "starting_balance_usd": 10_000.0,
            "reentry_policy": "topup",
            "strategies": strategy,
        }
        try:
            self.market = {"BTC/USDT": BREAKOUT}
            crashing = PaperEngine(
                config, candle_provider=self._provider,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            )
            with patch.object(
                crashing, "_append_durable", side_effect=OSError("publish crash")
            ):
                with self.assertRaisesRegex(OSError, "publish crash"):
                    crashing.run_cycle()

            pending_state = json.loads(self.positions_path.read_text())
            self.assertEqual(len(pending_state["pending_fills"]), 1)
            self.assertFalse(self.fills_path.exists())

            summary = PaperEngine(
                config, candle_provider=self._provider,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            ).run_cycle()

            ledger = self._read_lines(self.fills_path)
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["side"], "short")
            self.assertEqual(summary["intents"]["ak"], "duplicate_candle")
            self.assertNotIn(
                "pending_fills", json.loads(self.positions_path.read_text())
            )
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("paper_static_outbox_test", None)

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
            "exit_policy": "signal_or_stop", "monitor_timeframe": "15m",
            "dynamic_exit": {
                "mode": "execute", "version": "mfe_ratchet_v1",
                "activation_r": 1.0, "giveback_r": 0.5, "floor_r": 0.1,
            },
            "params": {},
        }]
        try:
            summary = PaperEngine(
                {
                    "starting_balance_usd": 10_000.0,
                    "reentry_policy": "topup",
                    "strategies": strategy,
                },
                candle_provider=self._provider,
                positions_path=self.positions_path,
                fills_path=self.fills_path,
                equity_path=self.equity_path,
            ).run_cycle()
            self.assertEqual(summary["intents"]["btc_utbot"], "open")
            position = json.loads(self.positions_path.read_text())["positions"]["btc_utbot"]
            self.assertEqual(position["stop_loss_price"], 95.0)
            self.assertIsNone(position["take_profit_price"])
            self.assertEqual(position["exit_policy"], "signal_or_stop")
            self.assertEqual(
                position["dynamic_exit"]["policy"]["version"],
                "mfe_ratchet_v1",
            )
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("suggested_stop_test", None)

    def _run_stop_distance_case(self, suggested_stop):
        class StopDistanceEngine:
            name = "stop_distance_test"
            version = "1"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                return Signal(
                    Side.LONG,
                    context.symbol,
                    context.timeframe,
                    "test stop distance",
                    suggested_stop=suggested_stop,
                )

        register_engine("stop_distance_test", StopDistanceEngine)
        strategy = [{
            "id": "btc_utbot", "engine": "stop_distance_test",
            "symbol": "BTC/USDT", "timeframe": "15m", "risk_pct": 0.02,
            "entry_enabled": True, "params": {},
        }]
        try:
            self.market = {"BTC/USDT": BREAKOUT}
            return self._engine(strategy).run_cycle()
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("stop_distance_test", None)

    def test_explicit_stop_keeps_legacy_atr_sizing(self):
        summary = self._run_stop_distance_case(2500.0)

        self.assertEqual(summary["intents"]["btc_utbot"], "open")
        self.assertEqual(summary["fills"][0]["risk_distance"], 100.0)
        self.assertAlmostEqual(
            summary["fills"][0]["qty"] * summary["fills"][0]["atr_risk"],
            200.0,
        )

    def test_missing_stop_uses_atr_fallback(self):
        summary = self._run_stop_distance_case(None)

        self.assertEqual(summary["intents"]["btc_utbot"], "open")
        self.assertEqual(
            summary["fills"][0]["risk_distance"],
            summary["fills"][0]["atr_risk"],
        )

    def test_malformed_long_stop_is_rejected(self):
        summary = self._run_stop_distance_case(2700.0)

        self.assertEqual(summary["intents"]["btc_utbot"], "invalid_stop")
        self.assertEqual(summary["fills"], [])

    def test_explicit_topup_opens_separate_tranche_with_stable_strategy_id(self):
        class AlwaysLongWithStop:
            name = "paper_topup_test"
            version = "1"
            required_timeframes: list[str] = []
            required_indicators: list[str] = []
            warmup_period = 1

            def init(self, config):
                pass

            def on_candle(self, candle, context):
                return Signal(
                    Side.LONG, context.symbol, context.timeframe, "paper topup",
                    suggested_stop=2500.0,
                )

        register_engine("paper_topup_test", AlwaysLongWithStop)
        config = {
            "starting_balance_usd": 10_000.0,
            "max_total_stop_risk_pct": 0.05,
            "max_symbol_stop_risk_pct": 0.03,
            "reentry_policy": "topup",
            "merit_order": ["btc_utbot"],
            "min_topup_fraction": 0.0,
            "strategies": [{
                "id": "btc_utbot", "engine": "paper_topup_test",
                "symbol": "BTC/USDT", "timeframe": "15m", "risk_pct": 0.02,
                "entry_enabled": True, "params": {},
            }],
        }
        try:
            engine = PaperEngine(
                config, candle_provider=self._provider,
                positions_path=self.positions_path, fills_path=self.fills_path,
                equity_path=self.equity_path,
            )
            self.market = {"BTC/USDT": BREAKOUT}
            first = engine.run_cycle()
            self.market = {
                "BTC/USDT": BREAKOUT + [dict(BREAKOUT[-1], ts=21)],
            }
            second = engine.run_cycle()

            self.assertEqual(first["intents"]["btc_utbot"], "open")
            self.assertEqual(second["intents"]["btc_utbot"], "open_topup")
            self.assertEqual(second["open_positions"], ["btc_utbot", "btc_utbot::t2"])
            self.assertEqual(second["fills"][0]["strategy_id"], "btc_utbot")
            self.assertEqual(second["fills"][0]["position_id"], "btc_utbot::t2")
            self.assertAlmostEqual(second["fills"][0]["risk_pct"], 0.01, places=4)
        finally:
            from orum.strategies import _ENGINES
            _ENGINES.pop("paper_topup_test", None)

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


if __name__ == "__main__":
    unittest.main()
