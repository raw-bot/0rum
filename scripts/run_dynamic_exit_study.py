"""Paired, offline dynamic-exit study for every paper portfolio strategy.

Each sleeve is replayed alone twice (native baseline, then its configured
dynamic policy), followed by full-portfolio all-off/all-on interaction runs.
All market data comes from the immutable replay snapshots. The historical
gold COT gate is reconstructed only from reports whose ``usable_from`` date is
already known at the simulated cycle.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

STRATEGY_IDS = (
    "btc_ak_macd_4h",
    "btc_utbot_m15_h1",
    "btc_ha_trend_4h",
    "eth_donchian",
    "gold_cot",
)

CANDIDATE_POLICIES = {
    "btc_ak_macd_4h": {
        "mode": "execute", "version": "ak_mfe_ssl_v1",
        "activation_r": 1.5, "giveback_r": 1.0, "floor_r": 0.25,
        "ema_len": 30, "atr_len": 14, "ssl_atr_mult": 1.0,
        "close_on_ssl_invalidation": True,
        "adaptive_target": True, "target_review_buffer_r": 0.5,
        "target_step_r": 1.0, "target_strength_min": 3,
    },
    "btc_utbot_m15_h1": {
        "mode": "execute", "version": "mfe_ratchet_v1",
        "activation_r": 5.0, "giveback_r": 3.0, "floor_r": 1.0,
    },
    "btc_ha_trend_4h": {
        "mode": "execute", "version": "mfe_ratchet_v1",
        "activation_r": 1.0, "giveback_r": 0.5, "floor_r": 0.1,
        "ema_len": 20, "adaptive_target": True,
        "target_review_buffer_r": 0.5, "target_step_r": 1.0,
        "target_strength_min": 3,
    },
    "eth_donchian": {
        "mode": "execute", "version": "mfe_ratchet_v1",
        "activation_r": 3.0, "giveback_r": 2.0, "floor_r": 0.5,
    },
    "gold_cot": {
        "mode": "execute", "version": "mfe_ratchet_v1",
        "activation_r": 5.0, "giveback_r": 3.0, "floor_r": 1.0,
    },
}


def _ms(value: str) -> int:
    return int(
        datetime.fromisoformat(value)
        .replace(tzinfo=timezone.utc)
        .timestamp()
        * 1000
    )


def _payload_hash(value: object) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode()).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HistoricalCotGate:
    def __init__(self, csv_path: Path, output_path: Path) -> None:
        with csv_path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        values = [float(row["comm_net"]) for row in rows]
        indices: list[float] = []
        for index, value in enumerate(values):
            window = values[max(0, index - 155):index + 1]
            low, high = min(window), max(window)
            indices.append(50.0 if high == low else 100.0 * (value - low) / (high - low))
        self._rows = rows
        self._indices = indices
        self._usable = [row["usable_from"] for row in rows]
        self._output_path = output_path
        self._last_index: int | None = None

    def publish(self, now: datetime) -> None:
        index = bisect_right(self._usable, now.date().isoformat()) - 1
        if index < 0 or index == self._last_index:
            return
        from orum.fsio import atomic_write_json

        row = self._rows[index]
        value = self._indices[index]
        atomic_write_json(
            self._output_path,
            {
                "report_date": row["report_date"],
                "usable_from": row["usable_from"],
                "cot_index": value,
                "gate_on": value <= 20.0,
                "threshold": 20.0,
                # Freshness validates the replay cache publication, while the
                # decision itself remains strictly keyed to usable_from above.
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        self._last_index = index


def _variant(base: dict, strategy_ids: tuple[str, ...], dynamic_ids: set[str]) -> dict:
    config = copy.deepcopy(base)
    selected = []
    for strategy in config.get("strategies", []):
        if strategy.get("id") not in strategy_ids:
            continue
        strategy["entry_enabled"] = True
        if strategy["id"] in dynamic_ids:
            strategy["dynamic_exit"] = copy.deepcopy(
                CANDIDATE_POLICIES[strategy["id"]]
            )
        else:
            strategy.pop("dynamic_exit", None)
        selected.append(strategy)
    config["strategies"] = selected
    config["merit_order"] = [sid for sid in config.get("merit_order", []) if sid in strategy_ids]
    configured_dynamic = {
        strategy["id"] for strategy in selected if strategy.get("dynamic_exit")
    }
    if configured_dynamic != dynamic_ids:
        raise ValueError(
            f"dynamic policy mismatch: requested={sorted(dynamic_ids)} "
            f"configured={sorted(configured_dynamic)}"
        )
    return config


def _summary(run_dir: Path) -> dict:
    fills_path = run_dir / "runtime_ledger" / "fills.jsonl"
    equity_path = run_dir / "runtime_ledger" / "equity.jsonl"
    fills = [json.loads(line) for line in fills_path.read_text().splitlines()]
    equity = [json.loads(line)["equity_usd"] for line in equity_path.read_text().splitlines()]
    closes = [fill for fill in fills if fill.get("action") == "close"]
    peak = equity[0]
    max_drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, 1.0 - value / peak)
    return {
        "final_equity_usd": equity[-1],
        "net_return_pct": 100.0 * (equity[-1] / equity[0] - 1.0),
        "max_drawdown_pct": 100.0 * max_drawdown,
        "opens": sum(fill.get("action") == "open" for fill in fills),
        "closes": len(closes),
        "dynamic_closes": sum(
            str(fill.get("reason", "")).startswith("dynamic_") for fill in closes
        ),
        "realized_pnl_usd": sum(float(fill.get("realized_pnl_usd", 0.0)) for fill in closes),
        "close_reasons": {
            reason: sum(fill.get("reason") == reason for fill in closes)
            for reason in sorted({str(fill.get("reason")) for fill in closes})
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-07-15")
    parser.add_argument("--portfolio-config", default=str(REPO_ROOT / "config" / "portfolio.yaml"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    study_dir = REPO_ROOT / "backtests" / "runs" / args.run_id
    if study_dir.exists() and not args.resume:
        raise SystemExit(f"study directory already exists: {study_dir}")
    study_dir.mkdir(parents=True, exist_ok=True)
    isolated_state = study_dir / "isolated_state"
    isolated_state.mkdir(exist_ok=True)
    os.environ["0RUM_STATE_DIR"] = str(isolated_state)

    from scripts.replay_harness.runtime_replay import run_runtime_replay
    from scripts.replay_harness.snapshot import manifest_fingerprint
    from scripts.replay_harness.timeline import SnapshotProvider

    portfolio_path = Path(args.portfolio_config).resolve()
    base = yaml.safe_load(portfolio_path.read_text())
    cot_csv_path = (
        REPO_ROOT / "state" / "data_cache"
        / "cot_gold__commodity_exchange_2006_2026.csv"
    )
    cot_sha256 = _file_hash(cot_csv_path)
    provider = SnapshotProvider()
    for symbol, interval in (
        ("BTC/USDT", "4h"), ("BTC/USDT", "1h"), ("BTC/USDT", "15m"),
        ("ETH/USDT", "1d"), ("PAXG/USDT", "1d"),
    ):
        provider.load(symbol, interval)
    cot_gate = HistoricalCotGate(
        cot_csv_path,
        isolated_state / "cot_gate.json",
    )

    outcomes: dict[str, dict] = {}

    def run(name: str, strategy_ids: tuple[str, ...], dynamic_ids: set[str]) -> None:
        cot_gate._last_index = None
        run_dir = study_dir / name
        if args.resume and (run_dir / "runtime_summaries.json").exists():
            expected_contract = {
                "config_sha256": _payload_hash(_variant(base, strategy_ids, dynamic_ids)),
                "cot_sha256": cot_sha256,
                "start": args.start,
                "end": args.end,
            }
            contract_path = run_dir / "study_contract.json"
            existing_contract = (
                json.loads(contract_path.read_text()) if contract_path.exists() else None
            )
            if existing_contract != expected_contract:
                raise SystemExit(
                    f"cannot resume stale run {name}: study contract changed"
                )
            outcomes[name] = _summary(run_dir)
            print(name, "reused", json.dumps(outcomes[name], sort_keys=True), flush=True)
            return
        if run_dir.exists():
            suffix = 1
            while (study_dir / f"{name}__retry{suffix}").exists():
                suffix += 1
            run_dir = study_dir / f"{name}__retry{suffix}"
        config = _variant(base, strategy_ids, dynamic_ids)
        contract = {
            "config_sha256": _payload_hash(config),
            "cot_sha256": cot_sha256,
            "start": args.start,
            "end": args.end,
        }
        run_runtime_replay(
            config,
            provider,
            run_dir=run_dir,
            start_ms=_ms(args.start),
            end_ms=_ms(args.end),
            before_cycle=cot_gate.publish,
        )
        (run_dir / "study_contract.json").write_text(
            json.dumps(contract, indent=2, sort_keys=True)
        )
        outcomes[name] = _summary(run_dir)
        print(name, json.dumps(outcomes[name], sort_keys=True), flush=True)

    for strategy_id in STRATEGY_IDS:
        run(f"{strategy_id}__baseline", (strategy_id,), set())
        run(f"{strategy_id}__dynamic", (strategy_id,), {strategy_id})
    run("portfolio__baseline", STRATEGY_IDS, set())
    selected_dynamic = {"btc_ak_macd_4h", "btc_ha_trend_4h"}
    run("portfolio__selected_dynamic", STRATEGY_IDS, selected_dynamic)
    run("portfolio__all_dynamic", STRATEGY_IDS, set(STRATEGY_IDS))
    operational_ids = ("btc_ak_macd_4h", "btc_utbot_m15_h1")
    run("operational__baseline", operational_ids, set())
    run("operational__selected_dynamic", operational_ids, {"btc_ak_macd_4h"})

    comparisons = {}
    for strategy_id in STRATEGY_IDS:
        baseline = outcomes[f"{strategy_id}__baseline"]
        dynamic = outcomes[f"{strategy_id}__dynamic"]
        comparisons[strategy_id] = {
            "equity_delta_usd": dynamic["final_equity_usd"] - baseline["final_equity_usd"],
            "net_return_delta_pct": dynamic["net_return_pct"] - baseline["net_return_pct"],
            "max_drawdown_delta_pct": dynamic["max_drawdown_pct"] - baseline["max_drawdown_pct"],
            "dynamic_closes": dynamic["dynamic_closes"],
        }
    comparisons["portfolio_interaction"] = {
        "equity_delta_usd": (
            outcomes["portfolio__all_dynamic"]["final_equity_usd"]
            - outcomes["portfolio__baseline"]["final_equity_usd"]
        ),
        "net_return_delta_pct": (
            outcomes["portfolio__all_dynamic"]["net_return_pct"]
            - outcomes["portfolio__baseline"]["net_return_pct"]
        ),
        "max_drawdown_delta_pct": (
            outcomes["portfolio__all_dynamic"]["max_drawdown_pct"]
            - outcomes["portfolio__baseline"]["max_drawdown_pct"]
        ),
    }
    comparisons["portfolio_selected"] = {
        "equity_delta_usd": (
            outcomes["portfolio__selected_dynamic"]["final_equity_usd"]
            - outcomes["portfolio__baseline"]["final_equity_usd"]
        ),
        "net_return_delta_pct": (
            outcomes["portfolio__selected_dynamic"]["net_return_pct"]
            - outcomes["portfolio__baseline"]["net_return_pct"]
        ),
        "max_drawdown_delta_pct": (
            outcomes["portfolio__selected_dynamic"]["max_drawdown_pct"]
            - outcomes["portfolio__baseline"]["max_drawdown_pct"]
        ),
    }
    comparisons["operational_selected"] = {
        "equity_delta_usd": (
            outcomes["operational__selected_dynamic"]["final_equity_usd"]
            - outcomes["operational__baseline"]["final_equity_usd"]
        ),
        "net_return_delta_pct": (
            outcomes["operational__selected_dynamic"]["net_return_pct"]
            - outcomes["operational__baseline"]["net_return_pct"]
        ),
        "max_drawdown_delta_pct": (
            outcomes["operational__selected_dynamic"]["max_drawdown_pct"]
            - outcomes["operational__baseline"]["max_drawdown_pct"]
        ),
    }
    report = {
        "run_id": args.run_id,
        "start": args.start,
        "end": args.end,
        "portfolio_config_path": str(
            portfolio_path.relative_to(REPO_ROOT)
            if portfolio_path.is_relative_to(REPO_ROOT) else portfolio_path
        ),
        "portfolio_config_file_sha256": _file_hash(portfolio_path),
        "portfolio_config_normalized_sha256": _payload_hash(base),
        "cot_csv_sha256": cot_sha256,
        "data_fingerprint_sha256": manifest_fingerprint([
            "BTCUSDT_4h.json", "BTCUSDT_1h.json", "BTCUSDT_15m.json",
            "ETHUSDT_1d.json", "PAXGUSDT_1d.json",
        ]),
        "outcomes": outcomes,
        "comparisons": comparisons,
    }
    (study_dir / "study_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(comparisons, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
