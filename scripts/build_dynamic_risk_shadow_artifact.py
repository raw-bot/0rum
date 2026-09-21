"""Build the reproducible UT Bot dynamic-risk research artifact.

The output is intentionally marked research-only. It was designed after the
source sample was inspected and therefore has no untouched holdout. The live
paper engine may log its proposals but cannot apply them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "backtests" / "runs" / "iso_utbot"
FILLS_PATH = RUN_DIR / "runtime_ledger" / "fills.jsonl"
OUTCOME_PATH = RUN_DIR / "runtime_outcome.json"
OUTPUT_PATH = ROOT / "backtests" / "reports" / "dynamic_risk_shadow_v1.json"

STRATEGY_ID = "btc_utbot_m15_h1"
PRIOR_WEIGHT = 100.0
WIN_R_CAP = 4.0
KELLY_FRACTION = 0.10
MAX_RISK_PCT = 0.02
BUCKETS = (
    ("stop_to_atr_risk_below_3", 0.0, 3.0),
    ("stop_to_atr_risk_3_or_more", 3.0, None),
)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _episodes(fills: list[dict]) -> list[dict]:
    opened: dict[str, dict] = {}
    episodes = []
    for fill in fills:
        if fill.get("strategy_id") != STRATEGY_ID:
            continue
        key = str(fill.get("position_id") or fill.get("strategy_id"))
        if fill.get("action") == "open":
            opened[key] = fill
            continue
        if fill.get("action") != "close" or key not in opened:
            continue
        entry = opened.pop(key)
        try:
            price = float(entry["price"])
            stop = float(entry["stop_loss_price"])
            qty = float(entry["qty"])
            atr_risk = float(entry["atr_risk"])
            entry_fee = float(entry.get("fee_usd", 0.0))
            net_pnl = float(fill["realized_pnl_usd"]) - entry_fee
        except (KeyError, TypeError, ValueError):
            continue
        risk_distance = abs(price - stop)
        stop_risk_usd = qty * risk_distance
        if atr_risk <= 0 or stop_risk_usd <= 0:
            continue
        episodes.append({
            "entry_ts": entry.get("ts"),
            "exit_ts": fill.get("ts"),
            "won": net_pnl > 0,
            "r_stop_net": net_pnl / stop_risk_usd,
            # `atr_risk` is the historical 2x-ATR sizing basis, not raw ATR.
            "stop_distance_atr_risk_ratio": risk_distance / atr_risk,
        })
    return episodes


def _bucket_record(
    bucket_id: str,
    lower: float,
    upper: float | None,
    episodes: list[dict],
    *,
    prior_win_rate: float,
) -> dict:
    selected = [
        row
        for row in episodes
        if row["stop_distance_atr_risk_ratio"] >= lower
        and (upper is None or row["stop_distance_atr_risk_ratio"] < upper)
    ]
    wins = [row["r_stop_net"] for row in selected if row["won"]]
    losses = [-row["r_stop_net"] for row in selected if not row["won"]]
    if not selected or not wins or not losses:
        raise ValueError(f"bucket {bucket_id} needs wins and losses")
    raw_win_rate = len(wins) / len(selected)
    posterior = (
        len(wins) + prior_win_rate * PRIOR_WEIGHT
    ) / (len(selected) + PRIOR_WEIGHT)
    avg_win = sum(min(value, WIN_R_CAP) for value in wins) / len(wins)
    avg_loss = sum(losses) / len(losses)
    payoff = avg_win / avg_loss
    kelly = max(0.0, posterior - (1.0 - posterior) / payoff)
    return {
        "id": bucket_id,
        "stop_distance_atr_risk_min": lower,
        "stop_distance_atr_risk_max": upper,
        "sample_n": len(selected),
        "wins": len(wins),
        "raw_win_rate": raw_win_rate,
        "p_win_research_estimate": posterior,
        "payoff_r_winsorized": payoff,
        "kelly_full_research": kelly,
        "kelly_fraction": KELLY_FRACTION,
        "max_risk_pct": MAX_RISK_PCT,
        "shadow_risk_pct_pre_drawdown": min(
            kelly * KELLY_FRACTION, MAX_RISK_PCT
        ),
    }


def build_artifact(
    fills: list[dict],
    *,
    source_sha256: str,
    data_fingerprint: str,
    config_sha256: str,
) -> dict:
    episodes = _episodes(fills)
    if not episodes:
        raise ValueError("no completed UT Bot episodes")
    wins = sum(row["won"] for row in episodes)
    prior_win_rate = wins / len(episodes)
    buckets = [
        _bucket_record(
            bucket_id,
            lower,
            upper,
            episodes,
            prior_win_rate=prior_win_rate,
        )
        for bucket_id, lower, upper in BUCKETS
    ]
    return {
        "schema_version": 1,
        "status": "research_only",
        "strategy_id": STRATEGY_ID,
        "promotion_eligible": False,
        "promotion_blocker": (
            "bucket boundary and policy were chosen after inspecting this sample; "
            "an untouched prospective holdout is required"
        ),
        "label": "net_pnl_after_both_fees_gt_zero",
        "completed_episodes": len(episodes),
        "observed_from": min(row["entry_ts"] for row in episodes),
        "observed_to": max(row["exit_ts"] for row in episodes),
        "prior": {
            "source": "same_strategy_global_empirical_rate",
            "sample_n": len(episodes),
            "wins": wins,
            "win_rate": prior_win_rate,
            "shrinkage_weight": PRIOR_WEIGHT,
        },
        "policy": {
            "positive_r_winsor_cap": WIN_R_CAP,
            "negative_r_cap": None,
            "kelly_fraction": KELLY_FRACTION,
            "max_risk_pct": MAX_RISK_PCT,
            "applied": False,
        },
        "buckets": buckets,
        "source": {
            "run_id": "iso_utbot",
            "fills_sha256": source_sha256,
            "data_fingerprint_sha256": data_fingerprint,
            "config_sha256": config_sha256,
        },
    }


def main() -> int:
    fills_raw = FILLS_PATH.read_bytes()
    fills = [
        json.loads(line)
        for line in fills_raw.decode().splitlines()
        if line.strip()
    ]
    outcome = json.loads(OUTCOME_PATH.read_text())
    artifact = build_artifact(
        fills,
        source_sha256=_sha256_bytes(fills_raw),
        data_fingerprint=str(outcome["data_fingerprint_sha256"]),
        config_sha256=str(outcome["config_sha256"]),
    )
    OUTPUT_PATH.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(
        f"dynamic-risk shadow artifact: {OUTPUT_PATH} "
        f"episodes={artifact['completed_episodes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
