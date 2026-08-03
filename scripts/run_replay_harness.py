"""CLI for the offline replay harness. NEVER touches state/ or live processes.

Usage (from the repo root, project venv):
  PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py snapshot
  PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py single --run-id sizing_v1
  PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py runtime --run-id rt_v1 \
      --start 2026-01-01 --end 2026-07-15
  PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py parity --run-id sizing_v1 \
      --golden backtests/fixtures/pine_golden_btcusdt_4h.json

Isolation: 0RUM_STATE_DIR is forced into the run directory before any orum
import, so even path constants resolved at import time cannot reach state/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "backtests" / "runs"
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_START = "2024-01-01"
BTC = "BTC/USDT"


def _ms(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _isolate_state(run_dir: Path) -> None:
    """Must run before ANY orum import (orum.paths freezes at import time)."""
    assert not any(mod.startswith("orum") for mod in sys.modules), \
        "orum imported before state isolation"
    os.environ["0RUM_STATE_DIR"] = str(run_dir / "isolated_state")
    (run_dir / "isolated_state").mkdir(parents=True, exist_ok=True)


def _config_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def cmd_snapshot(args) -> int:
    from scripts.replay_harness.snapshot import build_snapshot
    series = [(BTC, "4h"), (BTC, "1h"), (BTC, "15m"),
              ("ETH/USDT", "1d"), ("ETH/USDT", "1h"),
              ("PAXG/USDT", "1d"), ("PAXG/USDT", "1h")]
    # Warmup margin before the replay start so 300-bar windows are full.
    manifest = build_snapshot(series, _ms(args.warmup_from), _ms(args.end) if args.end else None)
    print(json.dumps({k: v["bars"] for k, v in manifest["series"].items()}, indent=2))
    return 0


DEFAULT_SINGLE_CONFIGS = [
    # name, sizing, fill, target_ref  — the review's step-2 grid.
    ("legacy_atr2_close_tclose", "legacy_atr_2", "legacy_close_fill", "signal_close"),
    ("legacy_atr2_next_tfill", "legacy_atr_2", "next_available_fill", "fill"),
    ("stop_exact_close_tclose", "stop_exact", "legacy_close_fill", "signal_close"),
    ("stop_exact_next_tfill", "stop_exact", "next_available_fill", "fill"),
    ("stop_floor1_next_tfill", "stop_floor_1atr", "next_available_fill", "fill"),
    ("stop_floor2_next_tfill", "stop_floor_2atr", "next_available_fill", "fill"),
]


def cmd_single(args) -> int:
    run_dir = RUNS_DIR / args.run_id
    _isolate_state(run_dir)
    from scripts.replay_harness.events import EventLog
    from scripts.replay_harness.report import write_report
    from scripts.replay_harness.single_replay import ReplayConfig, generate_candidates, run_replay
    from scripts.replay_harness.snapshot import manifest_fingerprint
    from scripts.replay_harness.timeline import SnapshotProvider

    provider = SnapshotProvider()
    provider.load(BTC, "4h")
    provider.load(BTC, "15m")
    start, end = _ms(args.start), _ms(args.end) if args.end else None

    with EventLog(run_dir / "events.jsonl") as log:
        candidates = generate_candidates(provider, symbol=BTC, timeframe="4h",
                                         start_ms=start, end_ms=end)
        for c in candidates:
            log.emit("candidate", **{k: v for k, v in c.items() if k != "features"},
                     features=c["features"])
        print(f"{len(candidates)} candidate signals")

        configs = [ReplayConfig(name=n, sizing=s, fill=f, target_ref=t,
                                risk_pct=args.risk_pct, max_leverage=args.max_leverage)
                   for n, s, f, t in DEFAULT_SINGLE_CONFIGS]
        results = [run_replay(cfg, candidates, provider, symbol=BTC, event_log=log)
                   for cfg in configs]

    fingerprint = manifest_fingerprint(["BTCUSDT_4h.json", "BTCUSDT_15m.json"])
    cfg_hash = _config_hash({"configs": [c["config"] for c in results],
                             "start": args.start, "end": args.end,
                             "params": "ha_trend defaults"})
    path = write_report(run_dir, results, data_fingerprint=fingerprint, config_hash=cfg_hash,
                        extra={"baseline": "strategy_legacy", "symbol": BTC,
                               "run_id": args.run_id})
    print(f"report: {path}")
    for summary in json.loads(path.read_text())["summaries"]:
        print(f"  {summary['config']:<28} n={summary['n_trades']:<4} "
              f"net={summary['net_return_pct']:>7.2f}%  PF={summary['profit_factor']}  "
              f"DD={summary['max_drawdown_pct_of_start']}%  fees=${summary['total_fees_usd']}")
    return 0


def cmd_runtime(args) -> int:
    run_dir = RUNS_DIR / args.run_id
    _isolate_state(run_dir)
    import yaml
    from scripts.replay_harness.events import EventLog
    from scripts.replay_harness.runtime_replay import run_runtime_replay
    from scripts.replay_harness.snapshot import manifest_fingerprint
    from scripts.replay_harness.timeline import SnapshotProvider

    config = yaml.safe_load(Path(args.portfolio_config).read_text())
    provider = SnapshotProvider()
    for symbol, interval in [(BTC, "4h"), (BTC, "1h"), (BTC, "15m"),
                             ("ETH/USDT", "1d"), ("PAXG/USDT", "1d")]:
        provider.load(symbol, interval)

    merit_order = [sid for sid in (args.merit or "").split(",") if sid] or None
    with EventLog(run_dir / "events.jsonl") as log:
        if args.arbiter == "off":
            outcome = run_runtime_replay(config, provider, run_dir=run_dir,
                                         start_ms=_ms(args.start), end_ms=_ms(args.end),
                                         event_log=log)
        else:
            from scripts.replay_harness.arbiter import run_arbiter_replay
            outcome = run_arbiter_replay(config, provider, run_dir=run_dir,
                                         start_ms=_ms(args.start), end_ms=_ms(args.end),
                                         event_log=log,
                                         reentry_policy=args.arbiter,
                                         merit_order=merit_order,
                                         min_topup_fraction=args.min_topup_fraction)
    outcome["data_fingerprint_sha256"] = manifest_fingerprint(
        ["BTCUSDT_4h.json", "BTCUSDT_1h.json", "BTCUSDT_15m.json",
         "ETHUSDT_1d.json", "PAXGUSDT_1d.json"])
    # Baseline runs keep the historical hash payload (comparable with existing
    # reference runs); arbiter runs fold their policy into the hash.
    outcome["config_sha256"] = _config_hash(config) if args.arbiter == "off" else _config_hash({
        "portfolio": config,
        "arbiter": {"mode": args.arbiter, "merit": merit_order,
                    "min_topup_fraction": args.min_topup_fraction},
    })
    (run_dir / "runtime_outcome.json").write_text(json.dumps(outcome, indent=2, default=str))
    print(json.dumps({k: outcome[k] for k in ("cycles", "data_fingerprint_sha256")}, indent=2))
    print(f"final: {outcome['final']['ts']}  equity=${outcome['final']['equity_usd']:.2f}  "
          f"open={outcome['final']['open_positions']}")
    return 0


def cmd_compare_arbiter(args) -> int:
    run_dir = RUNS_DIR / args.candidate
    _isolate_state(run_dir)
    from scripts.replay_harness.arbiter_report import compare_runs
    report = compare_runs(RUNS_DIR / args.baseline, run_dir)
    out_path = run_dir / "arbiter_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    for label in ("baseline", "candidate"):
        side = report[label]
        print(f"{label}: {side['run_id']}  equity=${side['equity']['final_equity_usd']}  "
              f"net={side['equity']['net_return_pct']}%  DD={side['equity']['max_dd_pct_of_peak']}%")
    print(f"delta: {json.dumps(report.get('delta', {}))}")
    print(f"identical fill streams: {report['divergence'].get('identical_fill_streams', False)}")
    print(f"report: {out_path}")
    return 0


def cmd_overlap(args) -> int:
    run_dir = RUNS_DIR / args.run_id
    _isolate_state(run_dir)
    from scripts.replay_harness.overlap import overlap_matrix
    dirs = [RUNS_DIR / rid for rid in args.runs.split(",")]
    matrix = overlap_matrix(dirs, start_ms=_ms(args.start))
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "overlap.json").write_text(json.dumps(matrix, indent=2))
    print(json.dumps(matrix, indent=2))
    return 0


def cmd_regime(args) -> int:
    run_dir = RUNS_DIR / args.run_id
    _isolate_state(run_dir)
    from scripts.replay_harness.regime import run_regime_study
    from scripts.replay_harness.snapshot import manifest_fingerprint
    from scripts.replay_harness.timeline import SnapshotProvider

    provider = SnapshotProvider()
    provider.load(BTC, args.feature_timeframe)
    dirs = [RUNS_DIR / rid for rid in args.runs.split(",")]
    outcome = run_regime_study(dirs, provider, start=args.start, end=args.end,
                               train_months=args.train_months, eval_months=args.eval_months,
                               feature_timeframe=args.feature_timeframe,
                               min_keep_fraction=args.min_keep_fraction)
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "verdicts.jsonl").open("w") as f:
        for verdict in outcome["verdicts"]:
            f.write(json.dumps(verdict, default=str) + "\n")
    report = outcome["report"]
    report["data_fingerprint_sha256"] = manifest_fingerprint(
        [f"BTCUSDT_{args.feature_timeframe}.json"])
    report["config_sha256"] = _config_hash({
        "runs": args.runs, "start": args.start, "end": args.end,
        "train_months": args.train_months, "eval_months": args.eval_months,
        "feature_timeframe": args.feature_timeframe,
        "min_keep_fraction": args.min_keep_fraction,
    })
    (run_dir / "regime_report.json").write_text(json.dumps(report, indent=2, default=str))
    for sid, stats in report["per_strategy"].items():
        kept = stats["trades_kept_fraction"]
        print(f"{sid}: OOS n={stats['oos_all'].get('n', 0)} "
              f"E[R] all={stats['oos_all'].get('mean_r_net')} "
              f"allowed={stats['oos_allowed'].get('mean_r_net')} "
              f"blocked={stats['oos_blocked'].get('mean_r_net')} "
              f"kept={kept}  forgone=${stats['forgone_profits_usd']} "
              f"avoided=${stats['avoided_losses_usd']}")
    print(f"report: {run_dir / 'regime_report.json'}")
    return 0


def cmd_parity(args) -> int:
    run_dir = RUNS_DIR / args.run_id
    _isolate_state(run_dir)
    from scripts.replay_harness.parity import compare_to_golden
    from scripts.replay_harness.single_replay import generate_candidates
    from scripts.replay_harness.timeline import SnapshotProvider

    provider = SnapshotProvider()
    provider.load(BTC, "4h")
    candidates = generate_candidates(provider, symbol=BTC, timeframe="4h",
                                     start_ms=_ms(args.start),
                                     end_ms=_ms(args.end) if args.end else None)
    verdict = compare_to_golden(Path(args.golden), candidates, provider, symbol=BTC)
    (run_dir / "parity.json").write_text(json.dumps(verdict, indent=2))
    print(json.dumps({k: verdict[k] for k in
                      ("golden_signals", "python_signals", "matched", "unexplained", "verdict")},
                     indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("snapshot", help="fetch data snapshots (network, once)")
    p.add_argument("--warmup-from", default="2023-10-01")
    p.add_argument("--end", default=None)
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("single", help="strategy_legacy + sizing/fill matrix")
    p.add_argument("--run-id", required=True)
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=None)
    p.add_argument("--risk-pct", type=float, default=0.01)
    p.add_argument("--max-leverage", type=float, default=3.0)
    p.set_defaults(func=cmd_single)

    p = sub.add_parser("runtime", help="runtime_legacy_exact via the real PaperEngine")
    p.add_argument("--run-id", required=True)
    p.add_argument("--start", default="2026-01-01")
    p.add_argument("--end", default="2026-07-15")
    p.add_argument("--portfolio-config", default=str(REPO_ROOT / "state" / "portfolio.yaml"))
    p.add_argument("--arbiter", choices=["off", "hold", "topup"], default="off",
                   help="thesis-budget auction variant: hold = no pyramiding, "
                        "topup = re-entry funds the remaining thesis budget as a new tranche")
    p.add_argument("--merit", default=None,
                   help="comma-separated strategy ids, highest merit first (default: config order)")
    p.add_argument("--min-topup-fraction", type=float, default=0.0,
                   help="refuse a partial grant below this fraction of the requested risk")
    p.set_defaults(func=cmd_runtime)

    p = sub.add_parser("compare-arbiter", help="before/after report: arbiter run vs baseline run")
    p.add_argument("--baseline", required=True, help="baseline run id (e.g. duo_ak_utbot)")
    p.add_argument("--candidate", required=True, help="arbiter run id")
    p.set_defaults(func=cmd_compare_arbiter)

    p = sub.add_parser("overlap", help="overlap matrix between isolated runtime runs")
    p.add_argument("--run-id", required=True, help="output run id for the matrix")
    p.add_argument("--runs", required=True, help="comma-separated isolated run ids")
    p.add_argument("--start", default=DEFAULT_START)
    p.set_defaults(func=cmd_overlap)

    p = sub.add_parser("regime", help="shadow regime study: walk-forward frozen thresholds "
                                      "over finished replay ledgers (never touches live signals)")
    p.add_argument("--run-id", required=True, help="output run id for the study")
    p.add_argument("--runs", required=True, help="comma-separated source run ids (runtime ledgers)")
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default="2026-07-15")
    p.add_argument("--train-months", type=int, default=12)
    p.add_argument("--eval-months", type=int, default=6)
    p.add_argument("--feature-timeframe", default="4h")
    p.add_argument("--min-keep-fraction", type=float, default=0.3)
    p.set_defaults(func=cmd_regime)

    p = sub.add_parser("parity", help="explained Pine parity vs a golden export")
    p.add_argument("--run-id", required=True)
    p.add_argument("--golden", required=True)
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=None)
    p.set_defaults(func=cmd_parity)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
