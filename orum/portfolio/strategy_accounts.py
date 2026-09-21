"""Independent paper accounts; every financial path belongs to the experiment."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from math import isfinite

from orum.fsio import atomic_write_json
from orum.portfolio.paper_broker import PaperBroker
from orum.portfolio.paper_engine import PaperEngine, _TIMEFRAME_MS

STARTING_BALANCE = 10_000.0
REFERENCE = "shared_reference"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source_hash(root: Path) -> str:
    paths = sorted((root / "orum").rglob("*.py")) + [
        root / "scripts" / name for name in
        ("run_strategy_accounts.py", "run_paper_portfolio.py", "data_layer.py")
    ]
    return digest({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in paths})


def initialize(root: Path, config: dict, code_hash: str, source_root: Path) -> dict:
    """Caller holds the experiment lock. Existing experiments are never reset."""
    path = root / "manifest.json"
    if path.exists():
        return json.loads(path.read_text())
    cfg = copy.deepcopy(config)
    ids = [s["id"] for s in cfg["strategies"]]
    if not ids or len(ids) != len(set(ids)) or any(
        not re.fullmatch(r"[a-z][a-z0-9_]*", sid) or sid == REFERENCE for sid in ids
    ):
        raise ValueError("strategy account identifiers must be unique safe names")
    cfg["starting_balance_usd"] = STARTING_BALANCE
    cfg["research_fee_roundtrip"] = PaperBroker().fee_rt
    cfg.pop("entry_drawdown_risk_scale", None)
    cfg.pop("entry_drawdown_kill_pct", None)
    if cfg.get("execution_mode") != "observed_mark":
        raise ValueError("strategy research requires observed_mark execution")
    observer = cfg.get("dynamic_risk_shadow", {})
    if observer.get("enabled"):
        artifact = Path(observer["artifact_path"])
        if not artifact.is_absolute():
            artifact = source_root / artifact
        frozen = json.loads(artifact.read_text())
        atomic_write_json(root / "dynamic_risk_artifact.json", frozen)
        observer["artifact_path"] = str(root / "dynamic_risk_artifact.json")
    configs = {REFERENCE: cfg}
    for strategy in cfg["strategies"]:
        individual = copy.deepcopy(cfg)
        individual["strategies"] = [copy.deepcopy(strategy)]
        individual["merit_order"] = [s for s in cfg.get("merit_order", []) if s == strategy["id"]]
        configs[strategy["id"]] = individual
    manifest = {
        "version": 1, "research_only": True, "promotion_eligible": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "starting_balance_usd": STARTING_BALANCE,
        "policy": "nominal_without_drawdown_scaling",
        "source_sha256": code_hash, "configs": configs, "config_sha256": digest(configs),
        "fee_roundtrip": PaperBroker().fee_rt,
        "comparison": "Separate 10k budgets versus one shared 10k budget; not additive performance.",
    }
    atomic_write_json(path, manifest)
    return manifest


def build_account(root: Path, account_id: str, config: dict, provider, gate_path: Path, *, now: str | None = None, marks: dict | None = None) -> PaperEngine:
    cfg = copy.deepcopy(config)
    for strategy in cfg["strategies"]:
        if strategy["engine"] == "gold_cot":
            strategy.setdefault("params", {})["gate_path"] = str(gate_path)
            if now is not None:
                strategy["params"]["gate_reference_time"] = now
    account_dir = root / "accounts" / account_id
    if not (account_dir / "paper_positions.json").exists() and any(
        (account_dir / name).exists() for name in ("paper_fills.jsonl", "paper_equity.jsonl")
    ):
        raise RuntimeError("account checkpoint missing with existing history; refusing balance reset")
    return PaperEngine(
        cfg, candle_provider=provider, broker=PaperBroker(fee_rt=cfg["research_fee_roundtrip"]),
        valuation_marks=marks,
        positions_path=account_dir / "paper_positions.json",
        fills_path=account_dir / "paper_fills.jsonl",
        equity_path=account_dir / "paper_equity.jsonl",
        dynamic_exit_path=account_dir / "paper_dynamic_exits.jsonl",
        shadow_regime_path=account_dir / "paper_regime_shadow.jsonl",
        dynamic_risk_shadow_path=account_dir / "paper_dynamic_risk_shadow.jsonl",
    )


def _requests(config: dict) -> list[tuple]:
    # Construction initializes pure strategies and the frozen observer only;
    # ledger checkpoints are checked separately inside each account's try block.
    engine = PaperEngine(config, candle_provider=lambda *_: [])
    requests = set()
    for sc in engine._strategies:
        timeframes = [sc.timeframe, *engine._engines[sc.id].required_timeframes]
        if sc.monitor_timeframe:
            timeframes.append(sc.monitor_timeframe)
        shadow = engine._shadow_regime_filters.get(sc.symbol)
        if shadow:
            timeframes.append(shadow["timeframe"])
        requests.update((sc.symbol, tf, engine._candles_limit) for tf in timeframes)
    return sorted(requests)


def freeze_inputs(requests: list[tuple], provider, now: datetime, cot_path: Path) -> dict:
    market = {}
    cutoff = int(now.timestamp() * 1000)
    for symbol, timeframe, limit in requests:
        key = f"{symbol}|{timeframe}|{limit}"
        try:
            rows = provider(symbol, timeframe, limit)
            rows = [copy.deepcopy(r) for r in rows if float(r["ts"]) + _TIMEFRAME_MS[timeframe] <= cutoff][-limit:]
            market[key] = {"rows": rows}
        except Exception as exc:
            market[key] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        cot = json.loads(cot_path.read_text())
    except (OSError, ValueError) as exc:
        cot = {"snapshot_error": f"{type(exc).__name__}: {exc}"}
    return {"ts": now.isoformat(), "market": market, "cot": cot}


def common_marks(inputs: dict) -> dict:
    """Latest complete close per asset, identical for all account valuations."""
    selected = {}
    cutoff = int(datetime.fromisoformat(inputs["ts"]).timestamp() * 1000)
    for key, item in inputs["market"].items():
        if not item.get("rows"):
            continue
        symbol, timeframe, _ = key.split("|")
        row = item["rows"][-1]
        close_time = float(row["ts"]) + _TIMEFRAME_MS[timeframe]
        close = float(row["close"])
        if not isfinite(close) or close <= 0 or close_time > cutoff:
            continue
        if symbol != "NVDA" and cutoff - close_time >= _TIMEFRAME_MS[timeframe] + 60_000:
            continue
        if close_time > selected.get(symbol, (-1, None))[0]:
            selected[symbol] = (close_time, close)
    return {symbol: pair[1] for symbol, pair in selected.items()}


def frozen_provider(inputs: dict):
    def provide(symbol, timeframe, limit):
        item = inputs["market"][f"{symbol}|{timeframe}|{limit}"]
        if "error" in item:
            raise ValueError(item["error"])
        return copy.deepcopy(item["rows"])
    return provide


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def account_row(root: Path, account_id: str, config: dict, summary: dict) -> dict:
    directory = root / "accounts" / account_id
    curve = _records(directory / "paper_equity.jsonl")
    fills = _records(directory / "paper_fills.jsonl")
    checkpoint = directory / "paper_positions.json"
    positions = json.loads(checkpoint.read_text()).get("positions", {}) if checkpoint.exists() else {}
    peak, max_dd = STARTING_BALANCE, 0.0
    for point in curve:
        peak = max(peak, point["equity_usd"])
        max_dd = max(max_dd, 1 - point["equity_usd"] / peak)
    complete = summary.get("valuation_complete", False)
    equity = summary.get("equity_usd") if complete else None
    return {
        "id": account_id, "reference": account_id == REFERENCE,
        "risk_pct": None if account_id == REFERENCE else config["strategies"][0]["risk_pct"],
        "status": "error" if "fatal_error" in summary else "degraded" if summary.get("errors") or not complete else "ok",
        "ts": summary.get("ts"), "equity_usd": equity,
        "balance_usd": summary.get("balance_usd"),
        "pnl_pct": equity / STARTING_BALANCE - 1 if equity is not None else None,
        "drawdown": max(0, 1 - equity / peak) if equity is not None else None,
        "max_drawdown": max_dd,
        "open_positions": summary.get("open_positions"),
        "positions": positions, "marks": summary.get("marks", {}),
        "closed_trades": sum(f.get("action") == "close" for f in fills),
        "fees_usd": sum(f.get("fee_usd", 0) for f in fills),
        "intents": summary.get("intents", {}), "errors": summary.get("errors", {}),
        "fatal_error": summary.get("fatal_error"),
        "curve": curve[::max(1, len(curve) // 60)][-60:] + (curve[-1:] if curve else []),
    }


def run_cycle(root: Path, provider, cot_path: Path, code_hash: str, *, now: datetime | None = None) -> dict:
    """Caller holds one root lock; resume unfinished accounts on persisted inputs."""
    manifest = json.loads((root / "manifest.json").read_text())
    configs = manifest["configs"]
    if digest(configs) != manifest["config_sha256"]:
        raise ValueError("frozen experiment configuration was modified")
    pending = root / "pending.json"
    if pending.exists():
        cycle_id = json.loads(pending.read_text())["cycle_id"]
        cycle_dir = root / "cycles" / cycle_id
        inputs = json.loads((cycle_dir / "inputs.json").read_text())
    else:
        now = now or datetime.now(timezone.utc)
        cycle_id = now.strftime("%Y%m%dT%H%M%S%fZ")
        cycle_dir = root / "cycles" / cycle_id
        inputs = freeze_inputs(_requests(configs[REFERENCE]), provider, now, cot_path)
        inputs["source_sha256"] = code_hash
        atomic_write_json(cycle_dir / "inputs.json", inputs)
        atomic_write_json(pending, {"cycle_id": cycle_id})
    atomic_write_json(cycle_dir / "cot_gate.json", inputs["cot"])
    provide = frozen_provider(inputs)
    rows = []
    account_source_changed = False
    marks = common_marks(inputs)
    for account_id, config in configs.items():
        result_path = cycle_dir / f"{account_id}.json"
        if result_path.exists():
            summary = json.loads(result_path.read_text())
        else:
            try:
                engine = build_account(root, account_id, config, provide, cycle_dir / "cot_gate.json", now=inputs["ts"], marks=marks)
                summary = engine.run_cycle(now=datetime.fromisoformat(inputs["ts"]))
            except Exception as exc:
                summary = {"ts": inputs["ts"], "fatal_error": f"{type(exc).__name__}: {exc}"}
            summary["source_sha256"] = code_hash
            atomic_write_json(result_path, summary)
        account_source_changed |= summary.get("source_sha256") != manifest["source_sha256"]
        try:
            rows.append(account_row(root, account_id, config, summary))
        except Exception as exc:
            rows.append({"id": account_id, "reference": account_id == REFERENCE,
                         "status": "error", "fatal_error": f"ledger read: {type(exc).__name__}: {exc}"})
    prior_path = root / "summary.json"
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else {}
    result = {
        "version": 1, "research_only": True, "started_at": manifest["started_at"],
        "ts": inputs["ts"], "completed_at": datetime.now(timezone.utc).isoformat(),
        "cycle_id": cycle_id, "input_sha256": digest(inputs),
        "source_sha256": code_hash, "config_sha256": manifest["config_sha256"],
        "method_changed": bool(prior.get("method_changed")) or account_source_changed or code_hash != manifest["source_sha256"] or code_hash != inputs["source_sha256"],
        "starting_balance_usd": STARTING_BALANCE, "fee_roundtrip": manifest["fee_roundtrip"],
        "accounts": rows, "status": "degraded" if any(r["status"] != "ok" for r in rows) else "ok",
    }
    atomic_write_json(cycle_dir / "summary.json", result)
    atomic_write_json(root / "summary.json", result)
    pending.unlink()
    return result
