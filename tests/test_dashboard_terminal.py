import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orum import dashboard


ROOT = Path(__file__).resolve().parents[1]


def _candles(seed: float, count: int = 500) -> list[dict]:
    return [
        {
            "ts": 1_780_000_000_000 + index * 3_600_000,
            "open": seed + index,
            "high": seed + index + 2,
            "low": seed + index - 2,
            "close": seed + index + 1,
            "volume": 100 + index,
        }
        for index in range(count)
    ]


class DashboardTerminalContractTests(unittest.TestCase):
    def setUp(self):
        dashboard._sig_cache.clear()

    def test_market_signals_separate_one_hour_display_from_native_engines(self):
        calls: list[tuple[str, str, int]] = []

        def fake_klines(asset: str, interval: str, limit: int = 300):
            calls.append((asset, interval, limit))
            seed = {"BTC/USDT": 60_000, "ETH/USDT": 3_000, "PAXG/USDT": 2_500}[asset]
            return _candles(seed)

        entry = {"ts": 1_780_080_000_000, "price": 100.0, "kind": "entry", "side": "long", "label": "IN"}
        exit_ = {"ts": 1_780_083_600_000, "price": 105.0, "kind": "exit", "side": "long", "label": "TP"}
        with patch.object(dashboard, "_binance_klines", side_effect=fake_klines), \
             patch.object(dashboard, "_ak_macd_markers", return_value=[entry, exit_]), \
             patch.object(dashboard, "_utbot_mtf_markers", return_value=[entry, exit_]), \
             patch.object(dashboard, "_donchian_markers", return_value=[entry, exit_]), \
             patch.object(dashboard, "_gold_cot_markers", return_value=[entry, exit_]), \
             patch.object(dashboard, "_paper_fill_markers", return_value=[entry, exit_]), \
             patch.object(dashboard, "_read_optional_json", return_value={"cot_index": 18, "gate_on": True}):
            signals = dashboard._market_signals({"asset": "BTC/USDT"})

        self.assertEqual(
            set(signals),
            {"BTC/USDT", "BTC/USDT::btc_utbot_m15_h1", "ETH/USDT", "PAXG/USDT"},
        )
        self.assertEqual([signals[a]["timeframe"] for a in signals], ["4h", "15m", "1d", "1d"])
        self.assertEqual(signals["BTC/USDT"]["strategy_id"], "btc_ak_macd_4h")
        self.assertEqual(
            signals["BTC/USDT::btc_utbot_m15_h1"]["strategy_id"],
            "btc_utbot_m15_h1",
        )
        self.assertEqual(
            [signals[a]["display_timeframe"] for a in signals],
            ["1h", "15m", "1h", "1h"],
        )
        self.assertTrue(all(len(sig["candles"]) == 480 for sig in signals.values()))
        self.assertTrue(all(sig["real_markers"] for sig in signals.values()))
        self.assertTrue(all(sig["trades"] for sig in signals.values()))
        self.assertTrue(all(sig["scenario_mode"] == "display_only" for sig in signals.values()))
        self.assertEqual(sum(1 for asset, interval, _ in calls if interval == "1h"), 4)
        self.assertTrue(all(limit == 500 for _, interval, limit in calls if interval == "1h"))
        self.assertIn(("BTC/USDT", "4h", 300), calls)
        self.assertIn(("BTC/USDT", "15m", 500), calls)
        self.assertIn(("ETH/USDT", "1d", 300), calls)
        self.assertIn(("PAXG/USDT", "1d", 300), calls)

    def test_static_terminal_has_two_independent_btc_cards_and_no_legacy_chart(self):
        html = (ROOT / "orum/static/dashboard.html").read_text()
        js = (ROOT / "orum/static/dashboard.js").read_text()

        for asset in ("BTC/USDT", "ETH/USDT", "PAXG/USDT"):
            self.assertIn(f'data-asset="{asset}"', html)
            self.assertIn(f'data-toggle-asset="{asset}"', html)
        for card_id in ("market-btc-card", "market-btc-utbot-card", "market-eth-card", "market-paxg-card"):
            self.assertIn(f'id="{card_id}"', html)
        self.assertIn('data-toggle-asset="BTC/USDT::btc_utbot_m15_h1"', html)
        self.assertEqual(html.count('class="card market-terminal-card"'), 4)
        self.assertNotIn('id="price-card"', html)
        self.assertNotIn("renderProChart(s); renderPrice(s)", js)
        self.assertNotIn("renderPosition(s); renderMarkets(s)", js)

    def test_layout_presets_are_compact_accessible_and_persistent(self):
        html = (ROOT / "orum/static/dashboard.html").read_text()
        css = (ROOT / "orum/static/dashboard.css").read_text()
        js = (ROOT / "orum/static/dashboard.js").read_text()

        self.assertIn('id="layout-presets"', html)
        self.assertLess(html.index('id="layout-presets"'), html.index('id="clock"'))
        for preset, label in (
            ("column", "Une colonne"),
            ("two-column", "Deux colonnes"),
            ("aligned-wall", "Mur aligné"),
        ):
            self.assertIn(f'data-layout-preset="{preset}"', html)
            self.assertIn(f'aria-label="{label}"', html)
        self.assertEqual(html.count('class="layout-preset-btn"'), 3)
        self.assertIn(".layout-preset-btn", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("DASHBOARD_LAYOUT_PRESETS", js)
        self.assertIn('orum-dash-layout-v4-personal', js)
        self.assertIn('orum-dash-layout-v4-active', js)
        self.assertIn('orum-dash-layout-v3', js)
        self.assertIn("applyingLayoutPreset", js)

    def test_each_layout_preset_mentions_every_grid_item(self):
        html = (ROOT / "orum/static/dashboard.html").read_text()
        js = (ROOT / "orum/static/dashboard.js").read_text()
        grid_ids = re.findall(r'gs-id="([^"]+)"', html)
        self.assertEqual(len(grid_ids), 14)
        for grid_id in grid_ids:
            self.assertGreaterEqual(js.count(f'["{grid_id}",'), 3)

    def test_layout_controller_rejects_partial_state_and_degrades_safely(self):
        css = (ROOT / "orum/static/dashboard.css").read_text()
        js = (ROOT / "orum/static/dashboard.js").read_text()

        self.assertIn("DASHBOARD_LAYOUT_IDS", js)
        self.assertIn("isValidDashboardLayout", js)
        self.assertIn("seen.size === DASHBOARD_LAYOUT_IDS.length", js)
        self.assertIn("safeStorageGet", js)
        self.assertIn("safeStorageSet", js)
        self.assertIn("safeStorageRemove", js)
        self.assertIn('classList.add("gridstack-fallback")', js)
        self.assertIn(".gridstack-fallback .grid-stack-item", css)
        self.assertIn("@media (max-width: 900px)", css)

    def test_real_fill_markers_can_be_filtered_by_strategy_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            fills = [
                {"ts": "2026-07-12T10:00:00+00:00", "strategy_id": "btc_ak_macd_4h",
                 "symbol": "BTC/USDT", "action": "open", "side": "long", "price": 60_000},
                {"ts": "2026-07-12T10:15:00+00:00", "strategy_id": "btc_utbot_m15_h1",
                 "symbol": "BTC/USDT", "action": "open", "side": "long", "price": 60_100},
            ]
            (state / "paper_fills.jsonl").write_text("\n".join(json.dumps(row) for row in fills) + "\n")
            with patch.object(dashboard, "STATE_DIR", state):
                markers = dashboard._paper_fill_markers(
                    "BTC/USDT", strategy_id="btc_utbot_m15_h1"
                )
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["strategy_id"], "btc_utbot_m15_h1")
        self.assertEqual(markers[0]["label"], "IN L")

    def test_real_exit_marker_uses_persisted_reason_not_pnl_sign(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            fills = [
                {"ts": "2026-07-12T10:00:00+00:00", "strategy_id": "btc_ak_macd_4h",
                 "symbol": "BTC/USDT", "action": "close", "side": "long", "price": 60_000,
                 "r": 1.0, "reason": "stop_loss"},
                {"ts": "2026-07-12T11:00:00+00:00", "strategy_id": "btc_ak_macd_4h",
                 "symbol": "BTC/USDT", "action": "close", "side": "long", "price": 60_100,
                 "r": -1.0, "reason": "UT Bot M15 sell crossover"},
            ]
            (state / "paper_fills.jsonl").write_text("\n".join(json.dumps(row) for row in fills) + "\n")
            with patch.object(dashboard, "STATE_DIR", state):
                markers = dashboard._paper_fill_markers("BTC/USDT")

        self.assertEqual([marker["label"] for marker in markers], ["SL", "OUT"])

    def test_market_forecast_lookup_is_strictly_strategy_scoped_and_has_history(self):
        reconstructed = [{
            "origin_ts": "2026-07-12T06:00:00+00:00",
            "target_ts": "2026-07-13T06:00:00+00:00",
            "origin_price": 59_000,
            "predicted_price": 60_100,
            "actual_price": 60_000,
            "median_return": 0.018644,
            "median_error": -0.001695,
            "source": "walk_forward",
        }]
        forecast_state = {
            "assets": {"BTC/USDT": {"strategy_id": "btc_utbot_m15_h1", "active": True}},
            "strategies": {"btc_utbot_m15_h1": {
                "strategy_id": "btc_utbot_m15_h1", "active": True,
                "history_24h": reconstructed,
            }},
        }
        history = {
            "record_type": "prediction", "strategy_id": "btc_utbot_m15_h1",
            "symbol": "BTC/USDT", "bucket_ts": "2026-07-13T06:00:00+00:00",
            "origin_ts": "2026-07-13T06:00:00+00:00", "origin_price": 60_000,
            "horizons": {"6": {"p50": .01}, "12": {"p50": .02}, "24": {"p50": .03}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "forecast_gate.json").write_text(json.dumps(forecast_state))
            (state / "forecast_history.jsonl").write_text(json.dumps(history) + "\n")
            with patch.object(dashboard, "STATE_DIR", state), \
                 patch.object(dashboard, "_binance_klines", return_value=_candles(60_000)), \
                 patch.object(dashboard, "_ak_macd_markers", return_value=[]), \
                 patch.object(dashboard, "_utbot_mtf_markers", return_value=[]), \
                 patch.object(dashboard, "_donchian_markers", return_value=[]), \
                 patch.object(dashboard, "_gold_cot_markers", return_value=[]):
                signals = dashboard._market_signals({"asset": "BTC/USDT"})

        self.assertEqual(signals["BTC/USDT"]["calibrated_forecast"], {})
        utbot = signals["BTC/USDT::btc_utbot_m15_h1"]
        self.assertTrue(utbot["calibrated_forecast"]["active"])
        self.assertEqual(len(utbot["forecast_history"]), 1)
        self.assertEqual(utbot["forecast_history"][0]["origin_price"], 60_000)
        self.assertEqual(utbot["forecast_history_24h"], reconstructed)
        self.assertEqual(signals["BTC/USDT"]["forecast_history_24h"], [])

    def test_market_forecast_history_keeps_the_latest_twenty_eight_predictions(self):
        forecast_state = {
            "strategies": {"btc_utbot_m15_h1": {"strategy_id": "btc_utbot_m15_h1", "active": True}},
        }
        history = []
        for index in range(30):
            origin = f"2026-07-{index // 4 + 1:02d}T{(index % 4) * 6:02d}:00:00+00:00"
            history.append({
                "record_type": "prediction", "strategy_id": "btc_utbot_m15_h1",
                "symbol": "BTC/USDT", "bucket_ts": origin, "origin_ts": origin,
                "origin_price": 60_000 + index,
                "horizons": {"6": {"p50": .01}, "12": {"p50": .02}, "24": {"p50": .03}},
            })
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "forecast_gate.json").write_text(json.dumps(forecast_state))
            (state / "forecast_history.jsonl").write_text(
                "\n".join(json.dumps(row) for row in history) + "\n"
            )
            with patch.object(dashboard, "STATE_DIR", state), \
                 patch.object(dashboard, "_binance_klines", return_value=_candles(60_000)), \
                 patch.object(dashboard, "_ak_macd_markers", return_value=[]), \
                 patch.object(dashboard, "_utbot_mtf_markers", return_value=[]), \
                 patch.object(dashboard, "_donchian_markers", return_value=[]), \
                 patch.object(dashboard, "_gold_cot_markers", return_value=[]):
                signals = dashboard._market_signals({"asset": "BTC/USDT"})

        retained = signals["BTC/USDT::btc_utbot_m15_h1"]["forecast_history"]
        self.assertEqual(len(retained), 28)
        self.assertEqual(retained[0]["origin_price"], 60_002)

    def test_continuous_forecast_history_is_one_target_aligned_path(self):
        origin = 1_780_000_000_000
        candles = [
            {"ts": origin, "close": 100},
            {"ts": origin + 3_600_000, "close": 99},
            {"ts": origin + 7_200_000, "close": 103},
        ]
        history = [
            {"target_ts": origin - 3_600_000, "predicted_price": 98},
            {"target_ts": origin + 3_600_000, "predicted_price": 101},
            {"target_ts": origin + 7_200_000, "predicted_price": 102},
            {"target_ts": origin + 10_800_000, "predicted_price": 104},
        ]

        result = self._run_forecast_history_path(history, candles)

        self.assertEqual(result, [
            {"ts": origin + 3_600_000, "price": 101},
            {"ts": origin + 7_200_000, "price": 102},
        ])

    def _run_forecast_history_path(self, history: list[dict], candles: list[dict]) -> list[dict]:
        js = (ROOT / "orum/static/dashboard.js").read_text()
        self.assertIn("function buildForecastHistoryPath", js)
        start = js.index("function buildForecastHistoryPath")
        end = js.index("\nfunction pairDisplayEvents", start)
        program = (
            js[start:end]
            + "\nconst result = buildForecastHistoryPath("
            + json.dumps(history)
            + ","
            + json.dumps(candles)
            + "); process.stdout.write(JSON.stringify(result));"
        )
        completed = subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, check=True
        )
        return json.loads(completed.stdout)

    def test_utbot_markers_ignore_the_forming_m15_candle(self):
        now_ms = 1_780_001_000_000
        m15 = [
            {"ts": now_ms - 1_800_000, "open": 100, "high": 102, "low": 99,
             "close": 101, "volume": 1},
            {"ts": now_ms - 300_000, "open": 101, "high": 110, "low": 100,
             "close": 109, "volume": 1},
        ]
        h1 = _candles(100, count=220)
        for index, candle in enumerate(h1):
            candle["ts"] = now_ms - (220 - index) * 3_600_000
        with patch.object(dashboard.time, "time", return_value=now_ms / 1000), \
             patch("orum.strategies.utbot_mtf.utbot_signal_series") as signal_series, \
             patch("orum.strategies.utbot_mtf.ema_last", return_value=1.0):
            def fake_signals(candles, *, key_value, atr_period):
                count = len(candles)
                buys = ([False] * (count - 1) + [True]) if key_value == 6.0 and count > 1 else [False] * count
                return buys, [False] * count, [0.0] * count

            signal_series.side_effect = fake_signals
            markers = dashboard._utbot_mtf_markers(m15, h1)

        self.assertEqual(markers, [])
        self.assertEqual(len(signal_series.call_args_list[0].args[0]), 1)

    def test_model_entries_are_directional_and_signal_exits_are_not_fake_tp_sl(self):
        now_ms = 1_780_001_000_000
        m15 = [{"ts": now_ms - 1_800_000, "open": 100, "high": 102, "low": 99,
                "close": 101, "volume": 1}]
        h1 = _candles(100, count=220)
        for index, candle in enumerate(h1):
            candle["ts"] = now_ms - (220 - index) * 3_600_000
        with patch.object(dashboard.time, "time", return_value=now_ms / 1000), \
             patch("orum.strategies.utbot_mtf.utbot_signal_series") as signal_series, \
             patch("orum.strategies.utbot_mtf.ema_last", return_value=1.0):
            signal_series.side_effect = [([True], [False], [0.0]),
                                         ([False], [False], [0.0])]
            utbot = dashboard._utbot_mtf_markers(m15, h1)

        donchian_candles = [
            {"ts": index, "open": close, "high": close, "low": close,
             "close": close, "volume": 1}
            for index, close in enumerate(([100.0] * 22) + [110.0, 110.0, 90.0])
        ]
        donchian = dashboard._donchian_markers(donchian_candles)

        self.assertEqual(utbot[0]["label"], "InL")
        self.assertEqual(
            [marker["label"] for marker in donchian if marker["kind"] == "exit"],
            ["OUT"],
        )
        self.assertFalse(any(marker["side"] == "short" for marker in donchian))

    def test_ak_model_overlay_matches_the_operational_long_only_policy(self):
        from tests.test_ak_macd_engine_parity import CANDLES

        markers = dashboard._ak_macd_markers(CANDLES[:120])

        self.assertFalse(any(marker["side"] == "short" for marker in markers))

    def test_terminal_renderer_is_proportional_zoomable_and_semantic(self):
        css = (ROOT / "orum/static/dashboard.css").read_text()
        js = (ROOT / "orum/static/dashboard.js").read_text()

        self.assertIn("renderMarketTerminal", js)
        self.assertIn("terminalZoom", js)
        self.assertIn("terminalVisibility", js)
        self.assertIn("ResizeObserver", js)
        self.assertIn('preserveAspectRatio="xMidYMid meet"', js)
        self.assertIn("vector-effect: non-scaling-stroke", css)
        self.assertIn("terminal-close", css)
        self.assertIn("scenario-stem", css)
        self.assertIn("event-stem", css)
        self.assertIn("trade-link", css)
        self.assertIn("buildScenarioFan", js)
        self.assertIn("renderTimelineEvents", js)
        self.assertIn("SCÉNARIOS · AFFICHAGE SEUL", js)

    def test_terminal_supports_horizontal_pan_and_calibrated_forecast_state(self):
        js = (ROOT / "orum/static/dashboard.js").read_text()
        css = (ROOT / "orum/static/dashboard.css").read_text()
        source = (ROOT / "orum/dashboard.py").read_text()

        self.assertIn("terminalPan", js)
        self.assertIn("bindTerminalPan", js)
        self.assertIn("pointerdown", js)
        self.assertIn("buildCalibratedFan", js)
        self.assertIn("calibrated_forecast", js)
        self.assertIn("cursor: grab", css)
        self.assertIn('STATE_DIR / "forecast_gate.json"', source)

    def test_old_yellow_white_audit_overlay_is_removed(self):
        html = (ROOT / "orum/static/dashboard.html").read_text()
        js = (ROOT / "orum/static/dashboard.js").read_text()
        css = (ROOT / "orum/static/dashboard.css").read_text()

        self.assertNotIn("buildForecastAuditSegments", js)
        self.assertNotIn("renderForecastAudit", js)
        self.assertNotIn("forecast-audit-predicted", js + css)
        self.assertNotIn("forecast-audit-realized", js + css)
        self.assertNotIn("JAUNE 50% = PRÉVU · BLANC = RÉEL", js)
        self.assertEqual(js.count('class="forecast-history-line"'), 1)
        self.assertIn(".forecast-history-line", css)
        self.assertRegex(
            css,
            r"\.terminal-svg \.forecast-history-line\s*\{[^}]*stroke-width:\s*1;[^}]*opacity:\s*\.5;[^}]*stroke-dasharray:\s*4 3",
        )
        self.assertIn("Historique prévision +24 h", js)
        self.assertRegex(
            css,
            r"\.terminal-svg \.scenario-path\.central\s*\{[^}]*opacity:\s*\.5",
        )
        self.assertIn('/assets/dashboard.css?v=32', html)
        self.assertIn('/assets/dashboard.js?v=33', html)

    def test_event_pins_and_scenario_join_have_explicit_layers(self):
        css = (ROOT / "orum/static/dashboard.css").read_text()
        js = (ROOT / "orum/static/dashboard.js").read_text()

        for layer in (
            "event-timeline-dot",
            "event-halo",
            "trade-endpoint",
            "history-future-divider",
            "scenario-join",
        ):
            self.assertIn(layer, css)
            self.assertIn(layer, js)
        self.assertIn("central[0]", js)
        self.assertIn("candles.length - 1", js)


if __name__ == "__main__":
    unittest.main()
