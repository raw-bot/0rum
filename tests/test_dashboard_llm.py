import json
from datetime import UTC, datetime
from pathlib import Path

from orum import dashboard


NOW = datetime(2026, 7, 13, 18, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def _write_jsonl(path: Path, rows: list[dict], *, malformed_tail: bool = False) -> None:
    body = "\n".join(json.dumps(row) for row in rows) + "\n"
    if malformed_tail:
        body += '{"incomplete":\n'
    path.write_text(body, encoding="utf-8")


def _outcome(lane: str, decision_id: str, value: float) -> dict:
    return {
        "outcome_id": f"out-{decision_id}",
        "decision_id": decision_id,
        "lane": lane,
        "symbol": "BTC/USDT",
        "side": "long",
        "evaluated_at": NOW.isoformat(),
        "exit_reason": "take_profit" if value > 0 else "stop",
        "exit_price": 101_000,
        "exit_candle_ts": 1_784_000_000_000,
        "gross_return_on_margin": value,
        "net_return_on_margin": value,
        "opposite_net_return_on_margin": -value,
        "hold_return_on_margin": 0,
        "mfe_pct": 2,
        "mae_pct": -1,
        "confidence": 0.7,
        "calibration_squared_error": 0.09,
    }


def test_llm_lab_state_is_safe_when_files_are_absent(tmp_path):
    state = dashboard._llm_lab_state(tmp_path, now=NOW)

    assert state["available"] is False
    assert state["last_observed_mode"] == "off"
    assert state["runtime"] == {
        "enabled": False,
        "running": False,
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "interval_minutes": 60,
        "last_cycle_started_at": None,
        "last_cycle_completed_at": None,
        "last_result": "not_started",
        "last_error": "",
    }
    assert state["opinion"] == {}
    assert state["timeline"] == []
    assert state["equity_curve"] == {"points": [], "events": []}
    assert state["accounts"]["llm_reference"]["status"] == "absent"
    assert state["comparison"]["coverage_status"] == "no_common_window"


def test_llm_lab_state_is_bounded_traceable_and_tolerates_malformed_tail(tmp_path):
    _write_jsonl(
        tmp_path / "llm_market_briefs.jsonl",
        [{
            "kind": "market_brief",
            "status": "valid",
            "recorded_at": NOW.isoformat(),
            "model": "deepseek/deepseek-v4-pro",
            "snapshot_id": "snap-1",
            "snapshot_hash": "hash-1",
            "brief": {
                "brief_id": "brief-1",
                "bias": "bullish",
                "regime": "volatile_range",
                "confidence": 0.71,
                "memo_fr": "Acheteurs présents <sans certitude>.",
                "interpretation": "Absorption des ventes.",
                "invalidation": "Clôture sous le range.",
                "evidence_freshness": "fresh",
            },
        }],
        malformed_tail=True,
    )
    decision_rows = []
    for index in range(35):
        decision_rows.append({
            "kind": "proposed_decision",
            "status": "valid",
            "recorded_at": NOW.isoformat(),
            "lane": "llm_reference" if index % 2 == 0 else "llm_evolving",
            "model": "deepseek/deepseek-v4-pro",
            "snapshot_id": "snap-1",
            "snapshot_hash": "hash-1",
            "brief_id": "brief-1",
            "paper_effective_leverage": 20,
            "fr_retail_eligible_leverage": 2,
            "experimental_only": True,
            "decision": {
                "decision_id": f"dec-{index}",
                "action": "open_long",
                "requested_leverage": 20,
                "equity_fraction": 0.1,
                "confidence": 0.7,
                "memo_fr": f"Décision <{index}>",
                "thesis": "Cassure probable.",
                "counter_thesis": "Fausse cassure.",
                "risk_rationale": "Risque paper borné.",
                "invalidation": "Retour sous le range.",
                "order_type": "market",
                "stop_loss": 97_000,
                "take_profits": [{"price": 105_000, "fraction": 1}],
                "lesson_ids": [],
                "evidence_ids": [],
            },
        })
    decision_rows.append({
        "kind": "paper_validation",
        "recorded_at": NOW.isoformat(),
        "lane": "llm_reference",
        "decision_id": "dec-34",
        "snapshot_id": "snap-1",
        "snapshot_hash": "hash-1",
        "validation": {"accepted": False, "reasons": ["snapshot_stale"]},
    })
    _write_jsonl(tmp_path / "llm_decisions.jsonl", decision_rows, malformed_tail=True)
    _write_jsonl(
        tmp_path / "llm_outcomes.jsonl",
        [
            {"kind": "decision_outcome", "recorded_at": NOW.isoformat(), "outcome": _outcome("llm_reference", "r-1", -0.1)},
            {"kind": "decision_outcome", "recorded_at": NOW.isoformat(), "outcome": _outcome("llm_evolving", "e-1", 0.2)},
        ],
    )
    _write_jsonl(
        tmp_path / "llm_lessons.jsonl",
        [{
            "kind": "lesson_event",
            "recorded_at": NOW.isoformat(),
            "lesson": {
                "lesson_id": "lesson-1",
                "state": "active",
                "error_category": "poor_timing",
                "adjustment": "Attendre la clôture <confirmée>.",
                "supporting_decision_ids": ["r-1", "e-1"],
                "counterexample_decision_ids": [],
                "evidence_strength": 0.8,
                "conditions": {"regime": "volatile_range"},
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
                "expires_at": NOW.isoformat(),
                "version": 2,
            },
        }],
    )
    (tmp_path / "llm_reference_account.json").write_text(json.dumps({
        "lane": "llm_reference", "starting_balance_usd": 10_000,
        "balance_usd": 9_900, "positions": {}, "processed_decision_ids": [],
        "last_processed_candles": {}, "schema_version": 1,
    }), encoding="utf-8")

    state = dashboard._llm_lab_state(tmp_path, now=NOW)

    assert state["available"] is True
    assert state["last_observed_mode"] == "paper_autonomous"
    assert state["opinion"]["brief_id"] == "brief-1"
    assert state["opinion"]["memo_fr"] == "Acheteurs présents <sans certitude>."
    assert len(state["timeline"]) == 30
    assert state["timeline"][0]["kind"] == "paper_validation"
    assert state["timeline"][0]["decision_id"] == "dec-34"
    assert state["timeline"][1]["paper_effective_leverage"] == 20
    assert state["timeline"][1]["fr_retail_eligible_leverage"] == 2
    assert state["accounts"]["llm_reference"]["balance_usd"] == 9_900
    assert state["accounts"]["llm_evolving"]["status"] == "absent"
    assert state["lessons"][0]["lesson_id"] == "lesson-1"
    assert state["comparison"]["coverage_status"] == "common_window"
    assert any(alert["kind"] == "rejection" for alert in state["alerts"])


def test_llm_equity_curve_uses_realised_fill_balances_and_keeps_trade_markers(tmp_path):
    for lane in ("llm_reference", "llm_evolving"):
        (tmp_path / f"{lane}_account.json").write_text(json.dumps({
            "lane": lane,
            "starting_balance_usd": 10_000,
            "balance_usd": 10_000,
            "positions": {},
            "processed_decision_ids": [],
            "last_processed_candles": {},
            "schema_version": 1,
        }), encoding="utf-8")
    _write_jsonl(tmp_path / "llm_paper_fills.jsonl", [
        {
            "lane": "llm_reference", "action": "open", "balance_after_usd": 9_990,
            "candle_ts": 1_000, "decision_id": "r-1", "side": "long", "price": 100,
            "realized_pnl_usd": 0,
        },
        {
            "lane": "llm_evolving", "action": "open", "balance_after_usd": 9_995,
            "candle_ts": 2_000, "decision_id": "e-1", "side": "short", "price": 101,
            "realized_pnl_usd": 0,
        },
        {
            "lane": "llm_reference", "action": "take_profit", "balance_after_usd": 10_050,
            "candle_ts": 3_000, "decision_id": "r-1", "side": "long", "price": 105,
            "realized_pnl_usd": 60,
        },
        {"lane": "llm_reference", "action": "stop", "balance_after_usd": "bad", "candle_ts": 4_000},
    ])

    state = dashboard._llm_lab_state(tmp_path, now=NOW)

    curve = state["equity_curve"]
    assert [point["equity_usd"] for point in curve["points"]] == [20_000, 19_990, 19_985, 20_045]
    assert [event["action"] for event in curve["events"]] == ["open", "open", "take_profit"]
    assert curve["events"][-1]["realized_pnl_usd"] == 60


def test_llm_dashboard_static_surface_is_read_only_responsive_and_escapes_model_text():
    html = (ROOT / "orum/static/dashboard.html").read_text(encoding="utf-8")
    css = (ROOT / "orum/static/dashboard.css").read_text(encoding="utf-8")
    js = (ROOT / "orum/static/dashboard.js").read_text(encoding="utf-8")
    bot_html = (ROOT / "orum/static/bot.html").read_text(encoding="utf-8")
    bot_js = (ROOT / "orum/static/bot.js").read_text(encoding="utf-8")

    assert 'href="/bot"' in html
    assert 'aria-label="Ouvrir les opérations du bot"' in html
    assert 'gs-id="llm-lab"' not in html
    assert 'id="llm-lab-card"' not in html
    assert ".llm-lab-grid" in css
    assert ".bot-page-grid" in css
    assert "@media (max-width: 900px)" in css
    assert "function renderLlmLab(s)" not in js
    assert "renderLlmLab(s)" not in js
    assert '"llm-lab"' not in js
    assert 'href="/"' in bot_html
    assert 'id="bot-unified"' in bot_html
    assert 'id="bot-runtime"' in bot_html
    assert 'id="bot-lanes"' in bot_html
    assert 'id="bot-equity"' not in bot_html
    assert 'id="bot-decisions"' in bot_html
    assert 'id="bot-learning"' in bot_html
    assert "/assets/bot.js?v=3" in bot_html
    assert 'fetch("/api/state"' in bot_js
    assert "method: \"POST\"" not in bot_js
    assert "const esc = (value)" in bot_js
    assert "esc(runtime.last_error" in bot_js
    assert "esc(opinion.memo_fr" in bot_js
    assert "esc(message)" in bot_js
    assert "function renderEquityCurve(snapshot)" not in bot_js
    assert "renderEquityCurve(snapshot);" not in bot_js
    assert "llm-equity-chart" not in bot_js + css
    assert "function llmMarketEvents(snapshot, asset)" in js
    assert 'label = display === "tp" ? "LLM TP"' in js


def test_llm_lab_exposes_only_bounded_runtime_status(tmp_path):
    (tmp_path / "llm_runtime_status.json").write_text(
        json.dumps({
            "enabled": True,
            "running": False,
            "model": "deepseek/deepseek-v4-pro",
            "interval_minutes": 60,
            "last_cycle_started_at": NOW.isoformat(),
            "last_cycle_completed_at": NOW.isoformat(),
            "last_result": "ok",
            "last_error": "OpenRouter HTTP 429: rate limit exceeded",
            "unexpected": "must-not-escape",
        }),
        encoding="utf-8",
    )

    state = dashboard._llm_lab_state(tmp_path, now=NOW)

    assert state["available"] is True
    assert state["runtime"] == {
        "enabled": True,
        "running": False,
        "model": "deepseek/deepseek-v4-pro",
        "interval_minutes": 60,
        "last_cycle_started_at": NOW.isoformat(),
        "last_cycle_completed_at": NOW.isoformat(),
        "last_result": "ok",
        "last_error": "Limite temporaire OpenRouter (quota ou cadence)",
    }
    assert "must-not-escape" not in json.dumps(state)


def test_llm_dashboard_does_not_raise_a_historical_model_error_after_a_success(tmp_path):
    (tmp_path / "llm_runtime_status.json").write_text(
        json.dumps({"enabled": True, "last_result": "ok", "last_error": ""}),
        encoding="utf-8",
    )
    _write_jsonl(
        tmp_path / "llm_market_briefs.jsonl",
        [{
            "kind": "market_brief",
            "status": "model_error",
            "recorded_at": NOW.isoformat(),
            "error": "historical failure",
        }],
    )

    state = dashboard._llm_lab_state(tmp_path, now=NOW)

    assert not any(alert["kind"] == "model_error" for alert in state["alerts"])


def test_llm_dashboard_raises_transient_model_error_on_current_cycle_failure(tmp_path):
    (tmp_path / "llm_runtime_status.json").write_text(
        json.dumps({"enabled": True, "last_result": "cycle_error", "last_error": ""}),
        encoding="utf-8",
    )
    _write_jsonl(
        tmp_path / "llm_decisions.jsonl",
        [{"kind": "proposed_decision", "status": "model_error", "recorded_at": NOW.isoformat(), "error": "timeout"}],
    )

    state = dashboard._llm_lab_state(tmp_path, now=NOW)

    kinds = [alert["kind"] for alert in state["alerts"]]
    assert "model_error" in kinds
    assert "model_error_recurring" not in kinds


def test_llm_dashboard_raises_recurring_model_error_despite_a_healthy_last_cycle(tmp_path):
    # Regression test for the decay-blindness bug: intermittent failures (every
    # other cycle) must stay visible even though the very last cycle succeeded.
    (tmp_path / "llm_runtime_status.json").write_text(
        json.dumps({"enabled": True, "last_result": "ok", "last_error": ""}),
        encoding="utf-8",
    )
    _write_jsonl(
        tmp_path / "llm_decisions.jsonl",
        [
            {"kind": "proposed_decision", "status": "model_error", "recorded_at": NOW.isoformat(), "error": "OpenRouter HTTP 429: rate limit exceeded"},
            {"kind": "proposed_decision", "status": "accepted", "recorded_at": NOW.isoformat()},
            {"kind": "proposed_decision", "status": "model_error", "recorded_at": NOW.isoformat(), "error": "OpenRouter HTTP 429: rate limit exceeded"},
            {"kind": "proposed_decision", "status": "accepted", "recorded_at": NOW.isoformat()},
        ],
    )

    state = dashboard._llm_lab_state(tmp_path, now=NOW)

    recurring = next(alert for alert in state["alerts"] if alert["kind"] == "model_error_recurring")
    assert recurring["level"] == "error"
    assert "2/4" in recurring["message"]
    assert not any(alert["kind"] == "model_error" for alert in state["alerts"])


def test_llm_timeline_presents_language_rejection_in_french(tmp_path):
    _write_jsonl(
        tmp_path / "llm_decisions.jsonl",
        [{
            "kind": "proposed_decision",
            "status": "error",
            "recorded_at": NOW.isoformat(),
            "lane": "llm_reference",
            "error": "response_not_french: CJK narrative detected",
        }],
    )

    state = dashboard._llm_lab_state(tmp_path, now=NOW)

    assert state["timeline"] == [{
        "kind": "proposed_decision",
        "recorded_at": NOW.isoformat(),
        "status": "error",
        "lane": "llm_reference",
        "model": None,
        "snapshot_id": None,
        "snapshot_hash": None,
        "brief_id": None,
        "decision_id": None,
        "fill_id": None,
        "fill_ids": [],
        "action": None,
        "side": None,
        "order_type": None,
        "equity_fraction": None,
        "requested_leverage": None,
        "paper_effective_leverage": None,
        "fr_retail_eligible_leverage": None,
        "experimental_only": None,
        "confidence": None,
        "memo_fr": None,
        "thesis": None,
        "counter_thesis": None,
        "risk_rationale": None,
        "invalidation": None,
        "stop_loss": None,
        "take_profits": [],
        "reasons": [],
        "error": "Réponse du modèle refusée : le texte doit être en français",
    }]


def test_llm_lab_ignores_structurally_corrupt_optional_records(tmp_path):
    (tmp_path / "llm_reference_account.json").write_text(
        '{"lane":"llm_reference","balance_usd":"not-a-number","positions":{}}',
        encoding="utf-8",
    )
    _write_jsonl(
        tmp_path / "llm_outcomes.jsonl",
        [{"kind": "decision_outcome", "outcome": {"decision_id": "partial"}}],
    )

    state = dashboard._llm_lab_state(tmp_path, now=NOW)

    assert state["accounts"]["llm_reference"]["status"] == "invalid"
    assert state["outcomes"] == []
    json.dumps(state, allow_nan=False)


def test_llm_lab_discards_valid_json_with_wrong_optional_types(tmp_path):
    _write_jsonl(
        tmp_path / "llm_decisions.jsonl",
        [{"kind": "paper_execution", "fill_ids": 7, "reasons": {"bad": True}}],
    )
    _write_jsonl(
        tmp_path / "llm_outcomes.jsonl",
        [{"outcome": {
            "lane": "llm_reference", "decision_id": "d", "exit_candle_ts": "bad",
            "net_return_on_margin": "nan", "exit_reason": "close",
            "calibration_squared_error": 0.1,
        }}],
    )

    state = dashboard._llm_lab_state(tmp_path, now=NOW)

    assert state["timeline"][0]["fill_ids"] == []
    assert state["timeline"][0]["reasons"] == []
    assert state["outcomes"] == []
    json.dumps(state, allow_nan=False)
