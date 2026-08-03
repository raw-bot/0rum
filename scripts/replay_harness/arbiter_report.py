"""Before/after comparison of a thesis-arbiter run against a baseline runtime
run. Pure readers over the run directories — never touches state/.

Inputs per run dir: runtime_ledger/{equity,fills}.jsonl, events.jsonl,
runtime_outcome.json (for the sha256 fingerprints). Output: a single dict
(written by the CLI as arbiter_report.json) with equity/DD, per-strategy net
P&L, refusal counts by reason, the budget distribution per strategy, and an
explicit fill-level divergence trace (charter: every divergence explained).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from scripts.replay_harness.arbiter import base_strategy_id
from scripts.replay_harness.events import read_events


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _equity_stats(equity_records: list[dict]) -> dict:
    if not equity_records:
        return {"final_equity_usd": None, "net_return_pct": None, "max_dd_pct_of_peak": None}
    start = equity_records[0]["equity_usd"]
    final = equity_records[-1]["equity_usd"]
    peak = float("-inf")
    max_dd = 0.0
    for record in equity_records:
        eq = record["equity_usd"]
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    return {
        "final_equity_usd": round(final, 2),
        "net_return_pct": round((final / start - 1.0) * 100, 2) if start else None,
        "max_dd_pct_of_peak": round(max_dd * 100, 2),
    }


def _fill_stats(fills: list[dict]) -> dict:
    net: dict[str, float] = defaultdict(float)
    entries: dict[str, int] = Counter()
    exits: dict[str, int] = Counter()
    topup_entries: dict[str, int] = Counter()
    entry_risk_usd: dict[str, list[float]] = defaultdict(list)
    for fill in fills:
        sid = base_strategy_id(fill["strategy_id"])
        position_id = fill.get("position_id", fill["strategy_id"])
        if fill["action"] == "open":
            net[sid] -= fill["fee_usd"]
            entries[sid] += 1
            if position_id != sid:
                topup_entries[sid] += 1
            entry_risk_usd[sid].append(fill["qty"] * fill["atr_risk"])
        elif fill["action"] == "close":
            net[sid] += fill["realized_pnl_usd"]
            exits[sid] += 1
    def _dist(values: list[float]) -> dict:
        if not values:
            return {}
        ordered = sorted(values)
        return {"n": len(values), "total_usd": round(sum(values), 2),
                "min_usd": round(ordered[0], 2), "median_usd": round(ordered[len(ordered) // 2], 2),
                "max_usd": round(ordered[-1], 2)}
    return {
        "net_pnl_by_strategy": {sid: round(value, 2) for sid, value in sorted(net.items())},
        "entries_by_strategy": dict(sorted(entries.items())),
        "topup_entries_by_strategy": dict(sorted(topup_entries.items())),
        "exits_by_strategy": dict(sorted(exits.items())),
        "entry_risk_distribution": {sid: _dist(vals) for sid, vals in sorted(entry_risk_usd.items())},
    }


def _decision_counts(events: list[dict]) -> dict:
    counts: dict[str, Counter] = defaultdict(Counter)
    for event in events:
        if event.get("event") == "portfolio_decision":
            counts[event["strategy_id"]][event["intent"]] += 1
    return {sid: dict(sorted(intent_counts.items())) for sid, intent_counts in sorted(counts.items())}


def _auction_summary(events: list[dict]) -> dict:
    outcomes: Counter = Counter()
    grant_fractions: list[float] = []
    for event in events:
        if event.get("event") != "auction":
            continue
        outcomes[event["outcome"]] += 1
        fraction = event.get("grant_fraction")
        if event["outcome"] == "granted_topup" and fraction is not None:
            grant_fractions.append(float(fraction))
    summary: dict = {"outcomes": dict(sorted(outcomes.items()))}
    if grant_fractions:
        ordered = sorted(grant_fractions)
        summary["topup_grant_fraction"] = {
            "n": len(ordered), "min": round(ordered[0], 4),
            "median": round(ordered[len(ordered) // 2], 4), "max": round(ordered[-1], 4),
        }
    return summary


def _fill_signature(fill: dict) -> tuple:
    return (fill["ts"], fill["strategy_id"],
            fill.get("position_id", fill["strategy_id"]), fill["action"],
            round(fill["price"], 8), round(fill["qty"], 10))


def _divergence(baseline_fills: list[dict], candidate_fills: list[dict]) -> dict:
    base_sigs = [_fill_signature(f) for f in baseline_fills]
    cand_sigs = [_fill_signature(f) for f in candidate_fills]
    if base_sigs == cand_sigs:
        return {"identical_fill_streams": True,
                "n_fills": len(base_sigs)}
    first = next((i for i, (a, b) in enumerate(zip(base_sigs, cand_sigs)) if a != b),
                 min(len(base_sigs), len(cand_sigs)))
    return {
        "identical_fill_streams": False,
        "n_fills_baseline": len(base_sigs), "n_fills_candidate": len(cand_sigs),
        "first_divergence_index": first,
        "baseline_fill": baseline_fills[first] if first < len(baseline_fills) else None,
        "candidate_fill": candidate_fills[first] if first < len(candidate_fills) else None,
    }


def _load_run(run_dir: Path) -> dict:
    ledger = run_dir / "runtime_ledger"
    outcome_path = run_dir / "runtime_outcome.json"
    outcome = json.loads(outcome_path.read_text()) if outcome_path.exists() else {}
    events = read_events(run_dir / "events.jsonl") if (run_dir / "events.jsonl").exists() else []
    fills = _read_jsonl(ledger / "fills.jsonl")
    return {
        "run_id": run_dir.name,
        "data_fingerprint_sha256": outcome.get("data_fingerprint_sha256"),
        "config_sha256": outcome.get("config_sha256"),
        "arbiter": outcome.get("arbiter"),
        "equity": _equity_stats(_read_jsonl(ledger / "equity.jsonl")),
        "fills": fills,
        "fill_stats": _fill_stats(fills),
        "decisions": _decision_counts(events),
        "auction": _auction_summary(events),
    }


def compare_runs(baseline_dir: Path, candidate_dir: Path) -> dict:
    baseline = _load_run(Path(baseline_dir))
    candidate = _load_run(Path(candidate_dir))
    report = {
        "baseline": {k: v for k, v in baseline.items() if k != "fills"},
        "candidate": {k: v for k, v in candidate.items() if k != "fills"},
        "divergence": _divergence(baseline["fills"], candidate["fills"]),
        "same_data_fingerprint": (
            baseline["data_fingerprint_sha256"] == candidate["data_fingerprint_sha256"]
            and baseline["data_fingerprint_sha256"] is not None
        ),
    }
    b_eq, c_eq = baseline["equity"], candidate["equity"]
    if b_eq["final_equity_usd"] and c_eq["final_equity_usd"]:
        report["delta"] = {
            "final_equity_usd": round(c_eq["final_equity_usd"] - b_eq["final_equity_usd"], 2),
            "net_return_pct_points": round(c_eq["net_return_pct"] - b_eq["net_return_pct"], 2),
            "max_dd_pct_points": round(c_eq["max_dd_pct_of_peak"] - b_eq["max_dd_pct_of_peak"], 2),
        }
    return report
