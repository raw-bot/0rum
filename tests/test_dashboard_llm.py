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
    assert state["opinion"] == {}
    assert state["timeline"] == []
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


def test_llm_dashboard_static_surface_is_read_only_responsive_and_escapes_model_text():
    html = (ROOT / "orum/static/dashboard.html").read_text(encoding="utf-8")
    css = (ROOT / "orum/static/dashboard.css").read_text(encoding="utf-8")
    js = (ROOT / "orum/static/dashboard.js").read_text(encoding="utf-8")

    assert 'gs-id="llm-lab"' in html
    assert 'id="llm-lab-card"' in html
    assert 'aria-label="Laboratoire LLM en lecture seule"' in html
    assert "confirm-paper" not in html
    assert ".llm-lab-grid" in css
    assert "@media (max-width: 900px)" in css
    assert "function renderLlmLab(s)" in js
    assert "esc(opinion.memo_fr" in js
    assert "esc(decision.memo_fr" in js
    assert "renderLlmLab(s)" in js
    assert js.count('["llm-lab",') == 3


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
