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
        market_cards = re.findall(
            r'<section class="card market-terminal-card" id="market-[^"]+-card" data-asset=',
            html,
        )
        self.assertEqual(len(market_cards), 4)
        self.assertNotIn('id="price-card"', html)
        self.assertNotIn("renderProChart(s); renderPrice(s)", js)
        self.assertNotIn("renderPosition(s); renderMarkets(s)", js)
        self.assertIn("LONG ONLY", js)

    def test_operational_chart_renders_audited_paper_fills(self):
        js = (ROOT / "orum/static/dashboard.js").read_text()

        self.assertIn(
            "const realEvents = (sig.real_markers || []).concat(llmMarketEvents(s, asset));",
            js,
        )
        self.assertIn(
            "renderTimelineEvents(realEvents, pairDisplayEvents(realEvents), candles, eventScale)",
            js,
        )
        self.assertIn('if (asset !== "BTC/USDT") return [];', js)
        self.assertNotIn("modelEvents.concat(realEvents)", js)

    def test_terminal_rendering_is_short_and_tranche_aware(self):
        js = (ROOT / "orum/static/dashboard.js").read_text()

        self.assertIn("event.position_id || side", js)
        self.assertIn("position_id: event.position_id", js)
        self.assertIn('const direction = (tranche.side || "long") === "short" ? -1 : 1;', js)
        self.assertIn('kind === "sl" ? side === "short"', js)
        self.assertIn('dynamicRoute && positionSide !== "short"', js)
        self.assertIn('structural_bracket: "bracket structurel SL/TP"', js)

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
        self.assertEqual(html.count('data-layout-preset='), 3)
        self.assertIn('id="layout-save-btn"', html)
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
        self.assertTrue(grid_ids)
        self.assertEqual(len(grid_ids), len(set(grid_ids)))
        for grid_id in grid_ids:
            self.assertGreaterEqual(js.count(f'["{grid_id}",'), 3)

    def test_layout_controller_accepts_partial_state_and_degrades_safely(self):
        css = (ROOT / "orum/static/dashboard.css").read_text()
        js = (ROOT / "orum/static/dashboard.js").read_text()

        self.assertIn("isValidDashboardLayout", js)
        self.assertNotIn("seen.size === DASHBOARD_LAYOUT_IDS.length", js)
        self.assertIn("seen.has(item.id)", js)
        self.assertIn("values.every(Number.isInteger)", js)
        self.assertIn("x + w <= 12", js)
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

    def test_closed_trades_pair_topup_fills_by_position_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            fills = [
                {"ts": "2026-07-12T10:00:00+00:00", "strategy_id": "ut",
                 "position_id": "ut", "symbol": "BTC/USDT", "action": "open"},
                {"ts": "2026-07-12T10:15:00+00:00", "strategy_id": "ut",
                 "position_id": "ut::t2", "symbol": "BTC/USDT", "action": "open"},
                {"ts": "2026-07-12T11:00:00+00:00", "strategy_id": "ut",
                 "position_id": "ut", "symbol": "BTC/USDT", "action": "close",
                 "entry_px": 100, "price": 105, "qty": 1, "realized_pnl_usd": 5},
                {"ts": "2026-07-12T11:15:00+00:00", "strategy_id": "ut",
                 "position_id": "ut::t2", "symbol": "BTC/USDT", "action": "close",
                 "entry_px": 102, "price": 106, "qty": 1, "realized_pnl_usd": 4},
            ]
            (state / "paper_fills.jsonl").write_text(
                "\n".join(json.dumps(row) for row in fills) + "\n"
            )
            with patch.object(dashboard, "STATE_DIR", state):
                trades = dashboard._paper_closed_trades()

        self.assertEqual([trade["opened_at"] for trade in trades], [
            "2026-07-12T10:00:00+00:00",
            "2026-07-12T10:15:00+00:00",
        ])
        self.assertEqual([trade["position_id"] for trade in trades], ["ut", "ut::t2"])

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

    def test_ak_model_overlay_includes_native_paper_short_candidates(self):
        from tests.test_ak_macd_engine_parity import CANDLES

        markers = dashboard._ak_macd_markers(CANDLES[:120])

        self.assertTrue(any(marker["side"] == "short" for marker in markers))

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
        self.assertIn("llmMarketEvents", js)
        self.assertIn('concat(llmMarketEvents(s, asset))', js)
        self.assertIn('event.display ||', js)
        self.assertIn('const source = event.llm ? " llm-marker" : "";', js)
        self.assertIn(".terminal-svg .llm-marker { opacity: .35; }", css)
        self.assertIn("SCÉNARIOS · AFFICHAGE SEUL", js)

    def test_terminal_supports_horizontal_pan_and_forecast_gate_is_gone(self):
        js = (ROOT / "orum/static/dashboard.js").read_text()
        css = (ROOT / "orum/static/dashboard.css").read_text()
        source = (ROOT / "orum/dashboard.py").read_text()

        self.assertIn("terminalPan", js)
        self.assertIn("bindTerminalPan", js)
        self.assertIn("pointerdown", js)
        self.assertIn("cursor: grab", css)
        # The forecast gate was deleted on 2026-07-17 (chantier 3 verdict):
        # no calibrated fan, no forecast state reads, no history overlay.
        self.assertNotIn("buildCalibratedFan", js)
        self.assertNotIn("calibrated_forecast", js)
        self.assertNotIn("forecast", source)

    def test_old_yellow_white_audit_overlay_is_removed(self):
        html = (ROOT / "orum/static/dashboard.html").read_text()
        js = (ROOT / "orum/static/dashboard.js").read_text()
        css = (ROOT / "orum/static/dashboard.css").read_text()

        self.assertNotIn("buildForecastAuditSegments", js)
        self.assertNotIn("renderForecastAudit", js)
        self.assertNotIn("forecast-audit-predicted", js + css)
        self.assertNotIn("forecast-audit-realized", js + css)
        self.assertNotIn("JAUNE 50% = PRÉVU · BLANC = RÉEL", js)
        self.assertNotIn("forecast-history-line", js + css)
        self.assertNotIn("Historique prévision +24 h", js)
        self.assertRegex(
            css,
            r"\.terminal-svg \.scenario-path\.central\s*\{[^}]*opacity:\s*\.5",
        )
        self.assertRegex(html, r'/assets/dashboard\.css\?v=\d+')
        self.assertRegex(html, r'/assets/dashboard\.js\?v=\d+')

    def test_dashboard_derives_dynamic_egide_route_from_portfolio_config(self):
        js = (ROOT / "orum/static/dashboard.js").read_text()

        self.assertIn("strategyConfig.dynamic_exit", js)
        self.assertIn("dynamic_active_target_price", js)
        self.assertIn("runner adaptatif", js)
        self.assertIn("ak_mfe_ssl_v1", js)
        self.assertIn("mfe_ratchet_v1", js)
        self.assertIn("signal_or_stop", js)
        self.assertIn("donchian_signal", js)
        self.assertIn("cot_signal", js)

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
