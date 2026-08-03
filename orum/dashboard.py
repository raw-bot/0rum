from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean
import urllib.request
from collections.abc import Mapping
from urllib.parse import urlparse

import yaml

from orum.accounting import account_returns, compound_balance
from orum.adapters.price import _binance_symbol
from orum.dsl.migrate import risk_value
from orum.llm.comparison import compare_lanes
from orum.opening_range import opening_range_snapshot
from orum.paths import STATE_DIR
from orum.score import score

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _read_optional_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text() or "{}") or {}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_jsonl_tail(path: Path, *, limit: int, max_bytes: int = 1_048_576) -> list[dict]:
    """Read a bounded valid tail without letting a torn LLM append break the UI."""
    if limit <= 0 or not path.exists():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            chunk = handle.read(max_bytes)
    except OSError:
        return []
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    records: list[dict] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict):
            records.append(value)
        if len(records) >= limit:
            break
    records.reverse()
    return records


def _safe_llm_json(path: Path, *, max_bytes: int = 1_048_576) -> dict:
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            return {}
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _llm_account_state(path: Path, lane: str) -> dict:
    def empty(status: str) -> dict:
        return {
            "lane": lane,
            "status": status,
            "balance_usd": None,
            "equity_usd": None,
            "positions": [],
            "processed_decision_count": 0,
        }

    raw = _safe_llm_json(path)
    if not raw:
        return empty("absent")
    positions = raw.get("positions") if isinstance(raw.get("positions"), Mapping) else {}
    visible_positions: list[dict] = []
    unrealized = 0.0
    for symbol, item in list(positions.items())[:10]:
        if not isinstance(item, Mapping):
            continue
        side = str(item.get("side") or "")
        try:
            qty = float(item.get("qty") or 0)
            entry = float(item.get("entry_px") or 0)
            mark = float(item.get("mark_px") or entry)
        except (TypeError, ValueError, OverflowError):
            return empty("invalid")
        unrealized += (1 if side == "long" else -1) * qty * (mark - entry)
        targets = item.get("take_profits")
        if not isinstance(targets, list):
            targets = []
        visible_positions.append({
            "position_id": item.get("position_id"),
            "decision_id": item.get("decision_id"),
            "symbol": item.get("symbol") or symbol,
            "side": side,
            "qty": qty,
            "entry_px": entry,
            "mark_px": mark,
            "requested_leverage": item.get("requested_leverage"),
            "effective_leverage": item.get("effective_leverage"),
            "liquidation_px": item.get("liquidation_px"),
            "stop_loss": item.get("stop_loss"),
            "take_profits": targets[:5],
            "time_exit_at": item.get("time_exit_at"),
            "thesis": item.get("thesis"),
            "invalidation": item.get("invalidation"),
        })
    try:
        balance = float(raw.get("balance_usd") or 0)
    except (TypeError, ValueError, OverflowError):
        return empty("invalid")
    processed = raw.get("processed_decision_ids")
    if not isinstance(processed, list):
        return empty("invalid")
    return {
        "lane": lane,
        "status": "active",
        "starting_balance_usd": raw.get("starting_balance_usd"),
        "balance_usd": balance,
        "equity_usd": balance + unrealized,
        "positions": visible_positions,
        "processed_decision_count": len(processed),
    }


def _llm_equity_curve(fill_records: list[dict], accounts: Mapping[str, Mapping[str, object]]) -> dict:
    """Build a bounded realised-equity series from the append-only paper-fill ledger.

    The ledger records balance after each fill, not mark-to-market equity, so this
    deliberately exposes realised cash only. That keeps the dashboard truthful
    while a position is open and avoids inventing historical prices.
    """
    lanes = ("llm_reference", "llm_evolving")
    balances: dict[str, float] = {}
    for lane in lanes:
        account = accounts.get(lane, {})
        try:
            starting = float(account.get("starting_balance_usd"))
        except (AttributeError, TypeError, ValueError, OverflowError):
            starting = 0.0
        if math.isfinite(starting) and starting > 0:
            balances[lane] = starting
    if not balances:
        return {"points": [], "events": []}

    rows: list[dict] = []
    for record in fill_records:
        lane = record.get("lane")
        action = record.get("action")
        if lane not in balances or not isinstance(action, str):
            continue
        try:
            balance_after = float(record.get("balance_after_usd"))
            candle_ts = int(record.get("candle_ts"))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(balance_after) or candle_ts < 0:
            continue
        rows.append({
            "lane": lane,
            "action": action,
            "balance_after_usd": balance_after,
            "candle_ts": candle_ts,
            "created_at": record.get("created_at") if isinstance(record.get("created_at"), str) else None,
            "decision_id": record.get("decision_id") if isinstance(record.get("decision_id"), str) else None,
            "side": record.get("side") if isinstance(record.get("side"), str) else None,
            "price": _llm_optional_number(record.get("price")),
            "realized_pnl_usd": _llm_optional_number(record.get("realized_pnl_usd")),
        })
    rows.sort(key=lambda item: (item["candle_ts"], item["created_at"] or "", item["lane"]))
    if not rows:
        return {"points": [], "events": []}

    points = [{
        "index": 0,
        "candle_ts": rows[0]["candle_ts"],
        "equity_usd": sum(balances.values()),
    }]
    events = []
    for index, row in enumerate(rows, start=1):
        balances[row["lane"]] = row["balance_after_usd"]
        points.append({
            "index": index,
            "candle_ts": row["candle_ts"],
            "equity_usd": sum(balances.values()),
        })
        events.append({
            **row,
            "index": index,
            "equity_usd": points[-1]["equity_usd"],
        })
    return {"points": points, "events": events}


def _llm_optional_number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _bounded_list(value: object, *, limit: int) -> list:
    return list(value[:limit]) if isinstance(value, (list, tuple)) else []


def _llm_operator_error(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return ""
    if "OpenRouter HTTP 429" in text:
        return "Limite temporaire OpenRouter (quota ou cadence)"
    if "response_not_french" in text:
        return "Réponse du modèle refusée : le texte doit être en français"
    return text[:300]


def _llm_timeline_item(record: Mapping[str, object]) -> dict:
    kind = str(record.get("kind") or "unknown")
    decision = record.get("decision") if isinstance(record.get("decision"), Mapping) else {}
    validation = record.get("validation") if isinstance(record.get("validation"), Mapping) else {}
    return {
        "kind": kind,
        "recorded_at": record.get("recorded_at") or record.get("created_at"),
        "status": record.get("status") or (
            "accepted" if validation.get("accepted") is True
            else "rejected" if validation.get("accepted") is False else None
        ),
        "lane": record.get("lane") or decision.get("lane"),
        "model": record.get("model"),
        "snapshot_id": record.get("snapshot_id"),
        "snapshot_hash": record.get("snapshot_hash"),
        "brief_id": record.get("brief_id"),
        "decision_id": record.get("decision_id") or decision.get("decision_id"),
        "fill_id": record.get("fill_id"),
        "fill_ids": _bounded_list(record.get("fill_ids"), limit=20),
        "action": decision.get("action") or record.get("action"),
        "side": decision.get("side") or record.get("side"),
        "order_type": decision.get("order_type"),
        "equity_fraction": decision.get("equity_fraction"),
        "requested_leverage": decision.get("requested_leverage") or record.get("requested_leverage"),
        "paper_effective_leverage": record.get("paper_effective_leverage") or validation.get("paper_effective_leverage"),
        "fr_retail_eligible_leverage": record.get("fr_retail_eligible_leverage") or validation.get("fr_retail_eligible_leverage"),
        "experimental_only": record.get("experimental_only"),
        "confidence": decision.get("confidence"),
        "memo_fr": decision.get("memo_fr"),
        "thesis": decision.get("thesis"),
        "counter_thesis": decision.get("counter_thesis"),
        "risk_rationale": decision.get("risk_rationale"),
        "invalidation": decision.get("invalidation"),
        "stop_loss": decision.get("stop_loss"),
        "take_profits": _bounded_list(decision.get("take_profits"), limit=5),
        "reasons": _bounded_list(
            validation.get("reasons") if validation.get("reasons") is not None else record.get("reasons"),
            limit=20,
        ),
        "error": _llm_operator_error(record.get("error")),
    }


MODEL_ERROR_DECAY_WINDOW = 10
MODEL_ERROR_DECAY_THRESHOLD = 2


def _llm_lab_state(state_dir: Path, *, now: datetime | None = None) -> dict:
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    runtime_record = _read_optional_json(state_dir / "llm_runtime_status.json")
    runtime = {
        "enabled": False,
        "running": False,
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "interval_minutes": 60,
        "last_cycle_started_at": None,
        "last_cycle_completed_at": None,
        "last_result": "not_started",
        "last_error": "",
    }
    if isinstance(runtime_record, Mapping) and runtime_record:
        if isinstance(runtime_record.get("enabled"), bool):
            runtime["enabled"] = runtime_record["enabled"]
        if isinstance(runtime_record.get("running"), bool):
            runtime["running"] = runtime_record["running"]
        model = runtime_record.get("model")
        if isinstance(model, str) and model:
            runtime["model"] = model[:120]
        interval = runtime_record.get("interval_minutes")
        if isinstance(interval, int) and not isinstance(interval, bool) and 1 <= interval <= 1440:
            runtime["interval_minutes"] = interval
        for key in ("last_cycle_started_at", "last_cycle_completed_at"):
            value = runtime_record.get(key)
            if isinstance(value, str) and value:
                runtime[key] = value[:80]
        result = runtime_record.get("last_result")
        if isinstance(result, str) and result:
            runtime["last_result"] = result[:80]
        runtime["last_error"] = _llm_operator_error(runtime_record.get("last_error"))
    brief_records = _read_jsonl_tail(state_dir / "llm_market_briefs.jsonl", limit=10)
    decision_records = _read_jsonl_tail(state_dir / "llm_decisions.jsonl", limit=60)
    fill_records = _read_jsonl_tail(state_dir / "llm_paper_fills.jsonl", limit=160)
    outcome_records = _read_jsonl_tail(state_dir / "llm_outcomes.jsonl", limit=40)
    postmortem_records = _read_jsonl_tail(state_dir / "llm_postmortems.jsonl", limit=20)
    lesson_records = _read_jsonl_tail(state_dir / "llm_lessons.jsonl", limit=100)

    valid_briefs = [
        item for item in brief_records
        if item.get("status") == "valid" and isinstance(item.get("brief"), Mapping)
    ]
    if runtime["last_result"] == "cycle_error":
        latest_brief_error = next(
            (item.get("error") for item in reversed(brief_records) if item.get("error")),
            None,
        )
        if latest_brief_error:
            runtime["last_error"] = _llm_operator_error(latest_brief_error)
    latest_brief = valid_briefs[-1] if valid_briefs else {}
    brief = latest_brief.get("brief") if isinstance(latest_brief.get("brief"), Mapping) else {}
    opinion = {} if not brief else {
        "recorded_at": latest_brief.get("recorded_at"),
        "model": latest_brief.get("model"),
        "snapshot_id": latest_brief.get("snapshot_id"),
        "snapshot_hash": latest_brief.get("snapshot_hash"),
        "brief_id": brief.get("brief_id"),
        "bias": brief.get("bias"),
        "regime": brief.get("regime"),
        "confidence": brief.get("confidence"),
        "memo_fr": brief.get("memo_fr"),
        "interpretation": brief.get("interpretation"),
        "invalidation": brief.get("invalidation"),
        "evidence_freshness": brief.get("evidence_freshness"),
    }

    timeline = [_llm_timeline_item(item) for item in reversed(decision_records)]
    timeline.extend(
        _llm_timeline_item({**item, "kind": "paper_fill"})
        for item in reversed(fill_records)
    )
    timeline.sort(key=lambda item: str(item.get("recorded_at") or ""), reverse=True)
    timeline = timeline[:30]

    outcome_keys = {
        "lane", "decision_id", "exit_candle_ts", "net_return_on_margin",
        "exit_reason", "calibration_squared_error",
    }
    outcomes = []
    for item in outcome_records:
        outcome = item.get("outcome")
        if not isinstance(outcome, Mapping) or not outcome_keys.issubset(outcome):
            continue
        try:
            exit_ts = int(outcome["exit_candle_ts"])
            net_return = float(outcome["net_return_on_margin"])
            account_return = float(outcome.get("account_return", net_return))
        except (TypeError, ValueError, OverflowError):
            continue
        if not all(math.isfinite(value) for value in (net_return, account_return)):
            continue
        normalized = dict(outcome)
        normalized["exit_candle_ts"] = exit_ts
        normalized["net_return_on_margin"] = net_return
        normalized["account_return"] = account_return
        outcomes.append(normalized)
    outcomes = outcomes[-20:][::-1]
    postmortems = [
        dict(item["postmortem"])
        for item in postmortem_records
        if item.get("status") == "valid" and isinstance(item.get("postmortem"), Mapping)
    ][-10:][::-1]
    latest_lessons: dict[str, dict] = {}
    for item in lesson_records:
        lesson = item.get("lesson")
        if isinstance(lesson, Mapping) and isinstance(lesson.get("lesson_id"), str):
            latest_lessons[str(lesson["lesson_id"])] = dict(lesson)
    lessons = sorted(
        latest_lessons.values(),
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )[:20]
    accounts = {
        "llm_reference": _llm_account_state(
            state_dir / "llm_reference_account.json", "llm_reference"
        ),
        "llm_evolving": _llm_account_state(
            state_dir / "llm_evolving_account.json", "llm_evolving"
        ),
    }
    equity_curve = _llm_equity_curve(fill_records, accounts)
    comparison = compare_lanes(outcomes)

    alerts: list[dict] = []
    for lane, account in accounts.items():
        if account["status"] == "invalid":
            alerts.append({
                "kind": "corrupt_state", "level": "error",
                "message": f"État paper illisible ou trop volumineux pour {lane}.",
            })
    if len(valid_briefs) >= 2:
        previous = valid_briefs[-2]
        previous_brief = previous.get("brief") if isinstance(previous.get("brief"), Mapping) else {}
        if previous_brief.get("bias") != brief.get("bias"):
            alerts.append({"kind": "bias_flip", "level": "warning", "message": "Le biais LLM a changé."})
        if previous.get("model") != latest_brief.get("model"):
            alerts.append({"kind": "model_change", "level": "warning", "message": "Le modèle observé a changé."})
    if opinion:
        try:
            recorded = datetime.fromisoformat(str(opinion["recorded_at"]).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            recorded = None
        if recorded is None or now_utc - recorded.astimezone(UTC) > timedelta(hours=2):
            alerts.append({"kind": "stale_evidence", "level": "warning", "message": "L'opinion LLM date de plus de deux heures."})
    recent_cycles = decision_records[-MODEL_ERROR_DECAY_WINDOW:]
    error_cycles = [
        item for item in recent_cycles
        if item.get("status") == "model_error" or item.get("error")
    ]
    if error_cycles:
        latest_error = _llm_operator_error(error_cycles[-1].get("error")) or "Erreur modèle"
        if len(error_cycles) >= MODEL_ERROR_DECAY_THRESHOLD:
            # A single healthy cycle must not hide a recurring instability (decay
            # blindness): this stays up until enough clean cycles roll the errors
            # out of the window, not just until the very next cycle succeeds.
            alerts.append({
                "kind": "model_error_recurring",
                "level": "error",
                "message": f"{len(error_cycles)}/{len(recent_cycles)} derniers cycles en erreur : {latest_error}",
            })
        elif runtime["last_result"] == "cycle_error":
            alerts.append({"kind": "model_error", "level": "error", "message": latest_error})
    rejected = next((item for item in timeline if item.get("status") == "rejected"), None)
    if rejected:
        alerts.append({"kind": "rejection", "level": "warning", "message": "Décision paper rejetée : " + ", ".join(map(str, rejected.get("reasons") or []))})
        if any("contradiction" in str(reason) for reason in rejected.get("reasons") or []):
            alerts.append({"kind": "contradiction", "level": "warning", "message": "Contradiction entre texte et action structurée."})
    leverage_spike = None
    for item in timeline:
        try:
            paper_leverage = float(item["paper_effective_leverage"])
            fr_leverage = float(item["fr_retail_eligible_leverage"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(paper_leverage) and math.isfinite(fr_leverage) and paper_leverage > fr_leverage:
            leverage_spike = item
            break
    if leverage_spike:
        alerts.append({"kind": "leverage_spike", "level": "info", "message": "Levier paper supérieur au repère FR retail ; expérimental uniquement."})
    ref_return = comparison["llm_reference"]["compounded_return"]
    evo_return = comparison["llm_evolving"]["compounded_return"]
    if comparison["coverage_status"] == "common_window" and abs(float(ref_return) - float(evo_return)) >= 0.1:
        alerts.append({"kind": "lane_divergence", "level": "info", "message": "Les lanes référence et évolutive divergent sur la fenêtre commune."})

    if fill_records or any(item.get("kind") in {"paper_validation", "paper_execution", "paper_monitor"} for item in decision_records):
        mode = "paper_autonomous"
    elif decision_records:
        mode = "shadow"
    elif brief_records:
        mode = "observer"
    else:
        mode = "off"
    available = bool(runtime_record or brief_records or decision_records or fill_records or outcome_records or postmortem_records or lesson_records or any(value["status"] != "absent" for value in accounts.values()))
    return {
        "available": available,
        "last_observed_mode": mode,
        "runtime": runtime,
        "opinion": opinion,
        "timeline": timeline,
        "accounts": accounts,
        "equity_curve": equity_curve,
        "outcomes": outcomes,
        "postmortems": postmortems,
        "lessons": lessons,
        "comparison": comparison,
        "alerts": alerts[:12],
    }


def _compound_return(trades: list[dict], goal: dict) -> float:
    if not trades:
        return 0.0
    return math.prod(1.0 + item for item in account_returns(trades, goal)) - 1.0


def _portfolio(trades: list[dict], goal: dict) -> dict:
    starting_balance = float(goal.get("starting_balance_usd", 10000.0))
    balance = compound_balance(trades, goal)
    return {
        "starting_balance_usd": starting_balance,
        "balance_usd": balance,
        "pnl_usd": balance - starting_balance,
        "pnl_pct": (balance / starting_balance - 1.0) if starting_balance else 0.0,
    }


def _paper_state(goal: dict) -> dict:
    """The unified multi-strategy paper ledger (orum/portfolio/paper_engine.py):
    one shared account, per-strategy open positions, real fills, equity curve.
    This is the source of truth for the portfolio now — the old mono-asset
    worker (trades.jsonl) is retired. Empty/absent files render as a flat 10k
    account, not an error."""
    starting = float(goal.get("starting_balance_usd", 10000.0))
    account = _read_optional_json(STATE_DIR / "paper_positions.json")
    equity_recs = _read_jsonl(STATE_DIR / "paper_equity.jsonl")
    fills = _read_jsonl(STATE_DIR / "paper_fills.jsonl")
    balance = float(account.get("balance_usd", starting))
    equity = float(equity_recs[-1]["equity_usd"]) if equity_recs else balance
    tranches: list[dict] = []
    for position_id, raw in (account.get("positions") or {}).items():
        strategy_id = raw.get("strategy_id") or position_id.split("::t", 1)[0]
        risk_distance = float(
            raw.get("risk_distance", raw.get("atr_risk", 0.0)) or 0.0
        )
        qty = float(raw.get("qty", 0.0) or 0.0)
        dynamic_exit = raw.get("dynamic_exit") or {}
        dynamic_policy = dynamic_exit.get("policy") or {}
        tranches.append({
            "strategy_id": strategy_id,
            "position_id": raw.get("position_id") or position_id,
            **{key: raw.get(key) for key in (
                "symbol", "side", "entry_px", "opened_ts", "entry_reason",
                "exit_policy", "monitor_timeframe", "stop_loss_price",
                "take_profit_price", "sl_basis", "reward_risk_ratio",
            )},
            "qty": qty,
            "notional_usd": float(raw.get("notional_usd", 0.0) or 0.0),
            "risk_pct": float(raw.get("risk_pct", 0.0) or 0.0),
            "atr_risk": float(raw.get("atr_risk", 0.0) or 0.0),
            "risk_distance": risk_distance,
            "stop_risk_usd": qty * risk_distance,
            "dynamic_exit_mode": dynamic_policy.get("mode"),
            "dynamic_exit_version": dynamic_policy.get("version"),
            "dynamic_stop_price": dynamic_exit.get("dynamic_stop_price"),
            "dynamic_mfe_r": dynamic_exit.get("mfe_r"),
            "dynamic_exit_armed": bool(dynamic_exit.get("armed", False)),
            "dynamic_active_target_price": dynamic_exit.get(
                "active_target_price"
            ),
            "dynamic_active_target_r": dynamic_exit.get("active_target_r"),
            "dynamic_target_extensions": int(
                dynamic_exit.get("target_extensions", 0) or 0
            ),
            "dynamic_target_strength_score": int(
                dynamic_exit.get("target_strength_score", 0) or 0
            ),
            "dynamic_last_target_extension_ts": dynamic_exit.get(
                "last_target_extension_ts"
            ),
            "dynamic_hypothetical_closed": bool(
                dynamic_exit.get("hypothetical_closed", False)
            ),
            "dynamic_hypothetical_exit_ts": dynamic_exit.get(
                "hypothetical_exit_ts"
            ),
            "dynamic_hypothetical_exit_reason": dynamic_exit.get(
                "hypothetical_exit_reason"
            ),
            "dynamic_hypothetical_exit_price": dynamic_exit.get(
                "hypothetical_exit_price"
            ),
        })

    grouped: dict[str, list[dict]] = {}
    for tranche in tranches:
        grouped.setdefault(tranche["strategy_id"], []).append(tranche)
    positions: list[dict] = []
    for strategy_id, strategy_tranches in grouped.items():
        first = strategy_tranches[0]
        qty = sum(tranche["qty"] for tranche in strategy_tranches)
        entry_value = sum(
            tranche["qty"] * float(tranche.get("entry_px", 0.0) or 0.0)
            for tranche in strategy_tranches
        )
        stop_levels = {tranche.get("stop_loss_price") for tranche in strategy_tranches}
        take_levels = {tranche.get("take_profit_price") for tranche in strategy_tranches}
        opened = [tranche.get("opened_ts") for tranche in strategy_tranches
                  if tranche.get("opened_ts")]
        positions.append({
            **first,
            "strategy_id": strategy_id,
            "qty": qty,
            "entry_px": entry_value / qty if qty else 0.0,
            "notional_usd": sum(
                tranche["notional_usd"] for tranche in strategy_tranches
            ),
            "risk_pct": sum(tranche["risk_pct"] for tranche in strategy_tranches),
            "stop_risk_usd": sum(
                tranche["stop_risk_usd"] for tranche in strategy_tranches
            ),
            "stop_loss_price": next(iter(stop_levels)) if len(stop_levels) == 1 else None,
            "take_profit_price": next(iter(take_levels)) if len(take_levels) == 1 else None,
            "opened_ts": min(opened) if opened else None,
            "tranche_count": len(strategy_tranches),
            "tranches": strategy_tranches,
        })
    return {
        "starting_balance_usd": starting,
        "balance_usd": balance,
        "equity_usd": equity,
        "pnl_usd": equity - starting,
        "pnl_pct": (equity / starting - 1.0) if starting else 0.0,
        "open_positions": positions,
        "open_count": len(tranches),
        "open_strategy_count": len(positions),
        "fills": fills[-30:][::-1],
        "equity_curve": [
            {"index": i, "ts": r.get("ts"), "equity": (float(r.get("equity_usd", starting)) / starting)}
            for i, r in enumerate(equity_recs)
        ],
        "updated_at": account.get("updated_at"),
    }


def _paper_closed_trades() -> list[dict]:
    """Closed paper fills, reshaped to the accounting/trade contract. The
    dashboard's whole stats layer (portfolio, equity curve, win rate, drawdown,
    score, trade count, latest trades, candles) now reads the unified paper
    ledger instead of the retired mono-asset worker's trades.jsonl. Every
    consumer already keys off net_pnl_usd / entry_price / exit_price / ts, so
    no downstream helper changes — only the source does."""
    out: list[dict] = []
    open_ts: dict[str, str] = {}
    for f in _read_jsonl(STATE_DIR / "paper_fills.jsonl"):
        sid = f.get("strategy_id")
        position_id = f.get("position_id", sid)
        if f.get("action") == "open":
            open_ts[position_id] = f.get("ts")
            continue
        if f.get("action") != "close":
            continue
        entry = float(f.get("entry_px", 0.0) or 0.0)
        exit_px = float(f.get("price", 0.0) or 0.0)
        realized = float(f.get("realized_pnl_usd", 0.0) or 0.0)
        notional = float(f.get("qty", 0.0) or 0.0) * entry
        closed_dt = _parse_ts(f.get("ts"))
        out.append({
            "ts": f.get("ts"),
            "opened_at": open_ts.pop(position_id, f.get("ts")),
            "candle_ts": int(closed_dt.timestamp() * 1000) if closed_dt else None,  # exit time in ms
            "strategy_id": sid,
            "position_id": position_id,
            "asset": f.get("symbol"),
            "side": f.get("side", "long"),
            "direction": f.get("side", "long"),
            "entry_price": entry,
            "exit_price": exit_px,
            "net_pnl_usd": realized,
            "pnl_usd": realized,
            "notional_usd": notional,
            "pnl_pct": (realized / notional) if notional else 0.0,
            "r": f.get("r"),
            "exit_reason": f.get("reason", ""),
            "reason": f.get("reason", ""),
        })
    return out


def _paper_fill_markers(symbol: str, strategy_id: str | None = None) -> list[dict]:
    """REAL paper fills for one symbol, as chart markers (ts in ms to sit on the
    candle axis). `real: True` lets the frontend render them distinctly from the
    recomputed backtest overlay — the two must never be confused."""
    out: list[dict] = []
    for f in _read_jsonl(STATE_DIR / "paper_fills.jsonl"):
        if f.get("symbol") != symbol or (
            strategy_id is not None and f.get("strategy_id") != strategy_id
        ):
            continue
        ts = _parse_ts(f.get("ts"))
        if ts is None:
            continue
        ms = int(ts.timestamp() * 1000)
        if f.get("action") == "open":
            direction = "S" if f.get("side") == "short" else "L"
            out.append({"ts": ms, "price": f.get("price"), "kind": "entry", "side": f.get("side", "long"),
                        "label": f"IN {direction}", "strategy_id": f.get("strategy_id"),
                        "position_id": f.get("position_id", f.get("strategy_id")), "real": True})
        elif f.get("action") == "close":
            reason = str(f.get("reason") or "").lower()
            label = "SL" if reason == "stop_loss" else "TP" if reason == "take_profit" else "OUT"
            out.append({"ts": ms, "price": f.get("price"), "kind": "exit", "side": f.get("side", "long"),
                        "label": label, "strategy_id": f.get("strategy_id"),
                        "position_id": f.get("position_id", f.get("strategy_id")), "real": True,
                        "reason": f.get("reason", "")})
    return out


def _legacy_audit() -> dict:
    """Seven-day, non-authoritative view of the retired mono-asset ledger."""
    cutoff = datetime.now(UTC).timestamp() - 7 * 86_400
    recent = []
    for trade in _read_jsonl(STATE_DIR / "trades.jsonl"):
        closed = _parse_ts(trade.get("ts"))
        if closed is not None and closed.timestamp() >= cutoff:
            recent.append(trade)
    return {
        "authoritative": False,
        "source": "legacy_worker_trades.jsonl",
        "period_days": 7,
        "trade_count_7d": len(recent),
        "net_pnl_usd_7d": sum(float(row.get("net_pnl_usd", 0.0) or 0.0) for row in recent),
        "recent": recent[-12:][::-1],
        "process_running": worker_running(),
        "warning": "Historique séparé — exclu du solde, de l'équité et des KPI unifiés.",
    }




def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _open_position(position: dict, heartbeat: dict, strategy: dict) -> dict:
    if not position:
        return {"active": False}

    entry = float(position.get("entry_price", 0.0) or 0.0)
    current = float(heartbeat.get("last_price", entry) or entry)
    notional = float(position.get("notional_usd", 0.0) or 0.0)
    pnl_pct = ((current - entry) / entry) if entry else 0.0
    pnl_usd = pnl_pct * notional
    stop_pct = risk_value(strategy, "stop_loss_pct", 2.0) / 100.0
    take_profit_pct = risk_value(strategy, "take_profit_pct", 3.0) / 100.0
    opened_at = _parse_ts(position.get("opened_at"))
    held_seconds = int((datetime.now(UTC) - opened_at).total_seconds()) if opened_at else 0

    return {
        **position,
        "active": True,
        "current_price": current,
        "unrealized_pnl_pct": pnl_pct,
        "unrealized_pnl_usd": pnl_usd,
        "stop_price": entry * (1.0 - stop_pct),
        "take_profit_price": entry * (1.0 + take_profit_pct),
        "held_seconds": max(0, held_seconds),
    }


def _max_drawdown(trades: list[dict], goal: dict) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for item in account_returns(trades, goal):
        equity *= 1.0 + item
        peak = max(peak, equity)
        worst = min(worst, (equity - peak) / peak)
    return abs(worst)


def _guardrail_status(drawdown: float, goal: dict) -> dict:
    soft = float(goal.get("soft_drawdown", 0.03))
    max_dd = float(goal.get("max_drawdown", 0.05))
    kill = float(goal.get("emergency_stop_drawdown", 0.06))
    if drawdown >= kill:
        return {"status": "kill", "label": "Kill-switch", "detail": "Manual verification required"}
    if drawdown >= max_dd:
        return {"status": "review", "label": "Review mode", "detail": "Strategy should stop"}
    if drawdown >= soft:
        return {"status": "caution", "label": "Soft drawdown", "detail": "Risk reduction zone"}
    return {"status": "normal", "label": "Paper mode", "detail": "Guardrails clear"}


_CHAMPION_REAUDIT_LABELS = {
    "conforming": ("Champion OK", "Ré-audit périodique conforme à son propre dossier"),
    "drift_detected": ("Champion : dérive", "Ré-audit périodique en dessous de son propre dossier"),
    "insufficient_data": ("Champion : preuve insuffisante", "Pas assez de trades depuis le dernier ré-audit"),
}


def _champion_reaudit_status(state_dir: Path) -> dict:
    """ADR-011: reads the standalone `scripts/champion_reaudit.py` output, if
    any. Never triggers or blocks anything -- an operator-facing chip only,
    mirroring `_guardrail_status`."""
    report = _read_optional_json(state_dir / "champion_reaudit_status.json")
    verdict = report.get("verdict")
    if verdict not in _CHAMPION_REAUDIT_LABELS:
        return {"status": "not_yet_audited", "label": "Champion : jamais audité", "detail": "Lancer scripts/champion_reaudit.py", "audited_at": None}
    label, detail = _CHAMPION_REAUDIT_LABELS[verdict]
    return {"status": verdict, "label": label, "detail": detail, "audited_at": report.get("audited_at")}


def _candles_from_trades(trades: list[dict]) -> list[dict]:
    candles: list[dict] = []
    for trade in trades[-24:]:
        open_price = float(trade.get("entry_price", 0.0))
        close_price = float(trade.get("exit_price", open_price))
        if not open_price and not close_price:
            continue
        spread = max(abs(close_price - open_price), max(open_price, close_price) * 0.00018)
        high = max(open_price, close_price) + spread * 0.65
        low = min(open_price, close_price) - spread * 0.65
        candles.append(
            {
                "ts": trade.get("ts"),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                # high/low are fabricated for display; only open/close come
                # from recorded trade prices.
                "synthetic_range": True,
            }
        )
    return candles


def _equity_curve(trades: list[dict], goal: dict) -> list[dict]:
    equity = 1.0
    points: list[dict] = []
    returns = account_returns(trades, goal)
    for index, (trade, item) in enumerate(zip(trades, returns), start=1):
        equity *= 1.0 + item
        points.append({"index": index, "ts": trade.get("ts"), "equity": equity})
    return points[-48:]


def _downsample(items: list, max_points: int) -> list:
    if len(items) <= max_points or max_points <= 0:
        return items
    stride = len(items) / max_points
    sampled = [items[int(i * stride)] for i in range(max_points)]
    if sampled[-1] is not items[-1]:
        sampled[-1] = items[-1]  # always keep the most recent bar
    return sampled


# Chart display source: Binance 15m — the SAME exchange the worker trades on.
# Using Coinbase here caused price discrepancies between the chart and the
# trading data (different exchange = different prints); everything is Binance now.
_BINANCE_15M_URL_TMPL = "https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=300"
_CB_TTL_SECONDS = 60.0
_cb_lock = threading.Lock()
_cb_cache: dict[str, dict] = {}


def _binance_15m_candles(asset: str = "BTC/USDT", max_points: int = 300) -> list[dict]:
    """Binance 15m OHLCV for one asset's price chart — same exchange as the worker
    so the chart and the trading data never diverge.

    Cached per asset for _CB_TTL_SECONDS (the dashboard polls often). Returns []
    on any failure so the caller can fall back to the worker's own feed and the
    chart never goes blank. Display-only: does not touch the trading data path."""
    now = time.time()
    with _cb_lock:
        cached = _cb_cache.get(asset)
        if cached and cached["candles"] and (now - cached["ts"]) < _CB_TTL_SECONDS:
            return cached["candles"]
    url = _BINANCE_15M_URL_TMPL.format(symbol=_binance_symbol(asset))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "0rum-dashboard"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001 - chart display must never break the snapshot.
        return []
    # Binance klines rows: [openTime(ms), open, high, low, close, volume, ...], oldest first.
    candles: list[dict] = []
    for row in sorted(raw, key=lambda r: r[0]):
        try:
            candles.append({
                "ts": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })
        except (TypeError, ValueError, IndexError):
            continue
    candles = candles[-max_points:]
    if candles:
        with _cb_lock:
            _cb_cache[asset] = {"ts": now, "candles": candles}
    return candles


def _price_series(max_points: int = 1440) -> list[dict]:
    """Real BTC OHLCV the worker actually fetched, downsampled for the browser.

    Prefers the live rolling feed the worker maintains (state/live_candles.json)
    so the chart shows current price; falls back to the backtest cache
    (state/candle_history.json) until the worker has written a live window."""
    candles: list[dict] = []
    for name in ("live_candles.json", "candle_history.json"):
        path = STATE_DIR / name
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text() or "{}")
        except (ValueError, OSError):
            continue
        candles = raw.get("candles", []) if isinstance(raw, dict) else (raw or [])
        if candles:
            break
    series = []
    for candle in _downsample(candles, max_points):
        try:
            close = float(candle.get("close", 0.0))
            series.append(
                {
                    "ts": candle.get("ts"),
                    "open": float(candle.get("open", close)),
                    "high": float(candle.get("high", close)),
                    "low": float(candle.get("low", close)),
                    "close": close,
                    "volume": float(candle.get("volume", 0.0) or 0.0),
                }
            )
        except (TypeError, ValueError):
            continue
    return series


def _trade_markers(trades: list[dict]) -> list[dict]:
    """Entry/exit points for the price chart: entry = triangle, exit = square."""
    markers = []
    for trade in trades:
        opened = _parse_ts(trade.get("opened_at"))
        entry_ts = int(opened.timestamp() * 1000) if opened else trade.get("candle_ts")
        net = float(trade.get("net_pnl_usd", 0.0) or 0.0)
        markers.append(
            {
                "entry_ts": entry_ts,
                "entry_price": float(trade.get("entry_price", 0.0) or 0.0),
                "exit_ts": trade.get("candle_ts"),
                "exit_price": float(trade.get("exit_price", 0.0) or 0.0),
                "side": trade.get("direction", "long"),
                "pnl_pct": float(trade.get("pnl_pct", 0.0) or 0.0),
                "net_pnl_usd": net,
                "win": net >= 0,
                "exit_reason": trade.get("exit_reason"),
            }
        )
    return markers


def _decisions(hypotheses: list[dict]) -> list[dict]:
    decisions: list[dict] = []
    for item in hypotheses[-12:][::-1]:
        changed = bool(item.get("changed"))
        decisions.append(
            {
                "ts": item.get("ts"),
                "mode": item.get("mode", "fallback"),
                "decision": "changed" if changed else "hold",
                "variable": item.get("variable"),
                "score": float(item.get("score", 0.0)),
                "reason": item.get("reason", "No reason recorded"),
            }
        )
    return decisions


def _engine_status(hypotheses: list[dict], watcher: dict) -> dict:
    latest = hypotheses[-1] if hypotheses else {}
    mode = latest.get("mode", "none")
    available = shutil.which("0rum") is not None
    watcher_ts = _parse_ts(watcher.get("ts"))
    watcher_age = (datetime.now(UTC) - watcher_ts).total_seconds() if watcher_ts else 999999
    watcher_connected = watcher.get("status") in {"standby", "reflecting", "reflected"} and watcher_age < 7200
    if watcher_connected:
        detail = watcher.get("detail", "0rum watcher is connected; LLM call is pending until reflection threshold.")
    elif mode == "0rum":
        detail = "0rum has produced manual reflections, but no watcher loop is connected."
    elif available:
        detail = "0rum CLI is installed, but this bot is still using deterministic fallback reflection."
    else:
        detail = "0rum CLI is not active in this sandbox run."
    return {
        "active": watcher_connected,
        "available": available,
        "mode": mode,
        "watcher": watcher,
        "label": "0rum watcher connected" if watcher_connected else "0rum not connected",
        "detail": detail,
    }


def _activity(trades: list[dict], hypotheses: list[dict], heartbeat: dict, watcher: dict) -> list[dict]:
    events: list[dict] = []
    if watcher:
        events.append(
            {
                "ts": watcher.get("ts"),
                "kind": "decision",
                "title": "0rum watcher",
                "detail": watcher.get("detail", "No watcher detail recorded"),
                "value": None,
            }
        )
    for trade in trades[-8:]:
        events.append(
            {
                "ts": trade.get("ts"),
                "kind": "trade",
                "title": "Paper trade closed",
                "detail": f"{float(trade.get('entry_price', 0.0)):,.2f} -> {float(trade.get('exit_price', 0.0)):,.2f}",
                "value": float(trade.get("pnl_pct", 0.0)),
            }
        )
    for item in hypotheses[-8:]:
        changed = bool(item.get("changed"))
        events.append(
            {
                "ts": item.get("ts"),
                "kind": "decision",
                "title": "Reflection changed strategy" if changed else "Reflection held strategy",
                "detail": item.get("reason", "No reason recorded"),
                "value": float(item.get("score", 0.0)),
            }
        )
    if heartbeat:
        events.append(
            {
                "ts": heartbeat.get("ts"),
                "kind": "heartbeat",
                "title": "Worker heartbeat",
                "detail": (
                    f"RSI {float(heartbeat.get('rsi', 0.0)):.1f}; "
                    f"action={heartbeat.get('decision_action', 'unknown')}; "
                    f"regime={heartbeat.get('market_regime', {}).get('label', 'unknown')}; "
                    f"entry={heartbeat.get('entry_fired')}; "
                    f"recorded={heartbeat.get('trade_recorded')}; "
                    f"price={heartbeat.get('price_source', 'unknown')}; "
                    f"news={heartbeat.get('news_source', 'unknown')}"
                ),
                "value": None,
            }
        )
    return sorted(events, key=lambda event: event.get("ts") or "", reverse=True)[:12]


_EXTERNAL_KINDS = {
    "external_signal_received": "received",
    "external_signal_executed": "executed",
    "external_signal_rejected": "rejected",
    "external_signal_duplicate": "duplicate",
    "external_signal_malformed": "malformed",
    "external_poll_no_table": "no_table",
}


def _external_feed(events: list[dict], goal: dict) -> dict:
    """Surface what TradingView proposed and what 0rum decided.

    Built from events.jsonl (the orchestrator logs every external lifecycle
    transition there), so the dashboard shows received vs accepted vs refused
    with the refusal reason — the whole point of the external mode being
    auditable."""
    ext = [event for event in events if str(event.get("kind", "")) in _EXTERNAL_KINDS]
    counts = {label: 0 for label in ("received", "executed", "rejected", "duplicate", "malformed", "no_table")}
    for event in ext:
        counts[_EXTERNAL_KINDS[event["kind"]]] += 1
    recent = [
        {
            "ts": event.get("ts"),
            "status": _EXTERNAL_KINDS[event["kind"]],
            "detail": event.get("detail", ""),
            "check": event.get("check"),
            "dedup_hash": event.get("dedup_hash"),
        }
        for event in ext[-24:][::-1]
    ]
    return {
        "mode": str(goal.get("signal_source", "native")),
        "counts": counts,
        "recent": recent,
        "total": len(ext),
    }


_SIGNAL_EVENT_LABEL = {"BUY_CANDIDATE": "BUY", "SELL_CANDIDATE": "SELL", "EXIT": "EXIT"}


def _signal_markers(ext_records: list[dict], events: list[dict], open_external_id: str | None = None) -> list[dict]:
    """Plottable Pine→0rum signals for the Trade Signals chart.

    external_signals.jsonl carries the structured proposal (bar_time, price,
    event); events.jsonl carries 0rum' verdict (executed / rejected + reason).
    They are joined by dedup_hash so the chart can show, at the right bar and
    price: what TradingView proposed, whether 0rum acted, and why it refused.
    Malformed payloads never reach the store (no price/time), so they only live
    in the text feed, not on the chart.

    An executed BUY whose position is still OPEN has no closed trade yet, so it
    would never reach the chart via trade markers. ``open_external_id`` (the open
    position's external_signal_id, i.e. that BUY's dedup_hash) flags it as
    ``opened`` so the chart can draw a live IN before the exit happens."""
    verdict: dict[str, str] = {}
    reason: dict[str, str] = {}
    for event in events:
        digest = event.get("dedup_hash")
        if not digest:
            continue
        if event.get("kind") == "external_signal_executed":
            verdict[digest] = "executed"
        elif event.get("kind") == "external_signal_rejected":
            verdict.setdefault(digest, "rejected")
            reason[digest] = event.get("check") or event.get("detail") or "rejected"

    markers: list[dict] = []
    seen: set[str] = set()
    for record in ext_records:
        digest = record.get("dedup_hash")
        bar_time = record.get("bar_time")
        price = float(record.get("price", 0.0) or 0.0)
        if not bar_time or price <= 0 or (digest and digest in seen):
            continue
        if digest:
            seen.add(digest)
        status = verdict.get(digest) or ("duplicate" if record.get("status") == "duplicate" else "received")
        if status == "executed" and digest and digest == open_external_id:
            status = "opened"  # executed BUY, position still open -> live IN
        event_value = str(record.get("event", ""))
        markers.append(
            {
                "ts": int(bar_time),
                "price": price,
                "event": _SIGNAL_EVENT_LABEL.get(event_value, event_value),
                "status": status,
                "reason": reason.get(digest, ""),
            }
        )
    return markers


def _logs(events: list[dict]) -> list[dict]:
    """Raw recent event log, newest first — precisely what the worker did."""
    return [
        {"ts": event.get("ts"), "kind": event.get("kind"), "detail": event.get("detail", "")}
        for event in events[-60:][::-1]
    ]


def _paper_logs() -> list[dict]:
    """Heartbeat/log lines from the unified paper engine (cycles + fills), newest
    first. This is the live feed now — the old events.jsonl belongs to the retired
    worker and only lingers as history below these entries."""
    out: list[dict] = []
    for r in _read_jsonl(STATE_DIR / "paper_equity.jsonl")[-30:]:
        out.append({
            "ts": r.get("ts"), "kind": "paper_cycle",
            "detail": f"cycle · {r.get('open_positions', 0)} position(s) · equity ${float(r.get('equity_usd', 0) or 0):,.2f}",
        })
    for f in _read_jsonl(STATE_DIR / "paper_fills.jsonl")[-30:]:
        if f.get("action") == "open":
            out.append({"ts": f.get("ts"), "kind": "paper_fill",
                        "detail": f"OPEN {f.get('strategy_id')} {f.get('symbol')} @ {f.get('price')}"})
        elif f.get("action") == "close":
            out.append({"ts": f.get("ts"), "kind": "paper_fill",
                        "detail": f"CLOSE {f.get('strategy_id')} {f.get('symbol')} @ {f.get('price')} "
                                  f"pnl ${float(f.get('realized_pnl_usd', 0) or 0):,.2f}"})
    _floor = datetime.min.replace(tzinfo=UTC)
    out.sort(key=lambda e: _parse_ts(e.get("ts")) or _floor, reverse=True)
    return out[:40]


def _paper_worker() -> dict:
    """Health of the 15-minute launchd paper engine, keyed off its equity journal.

    Three missed cycles make the worker stale.  This replaces the retired
    mono-asset worker's heartbeat/pid liveness.
    """
    recs = _read_jsonl(STATE_DIR / "paper_equity.jsonl")
    last = _parse_ts(recs[-1].get("ts")) if recs else None
    age = (datetime.now(UTC) - last).total_seconds() if last else None
    stale = age is None or age > 2700
    return {"heartbeat_age_seconds": age, "stale": stale, "running": not stale,
            "pid": None, "mode": "paper_15m"}


# --- retired engine process control -----------------------------------------
# The dashboard may still stop the retired engine as a safety measure, but it
# must never be able to start it again. The unified com.0rum.paper service is
# the only authoritative runtime.
WORKER_PID_PATH = STATE_DIR / "worker.pid"


def _worker_pid() -> int | None:
    try:
        return int(WORKER_PID_PATH.read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def worker_running() -> bool:
    pid = _worker_pid()
    if pid is None:
        return False
    if _pid_alive(pid):
        return True
    WORKER_PID_PATH.unlink(missing_ok=True)  # clean up a stale pid file
    return False


def start_worker() -> dict:
    """Refuse to revive the retired continuous engine."""
    return {
        "ok": False,
        "running": worker_running(),
        "detail": "legacy engine retired; com.0rum.paper is authoritative",
    }


def stop_worker() -> dict:
    pid = _worker_pid()
    if pid is None or not _pid_alive(pid):
        WORKER_PID_PATH.unlink(missing_ok=True)
        return {"ok": True, "running": False, "detail": "engine was not running"}
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)  # kill the whole engine session
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    WORKER_PID_PATH.unlink(missing_ok=True)
    return {"ok": True, "running": False, "pid": pid, "detail": "engine stopped"}


def _portfolio_shadow() -> dict:
    """Panneau des moteurs recherche (portfolio paper Kelly, scripts/portfolio_shadow.py).
    Lit les fichiers append-only du paper — aucun couplage avec le worker."""
    recs = _read_jsonl(STATE_DIR / "portfolio_shadow.jsonl")
    state = _read_optional_json(STATE_DIR / "portfolio_shadow_positions.json")
    engines: dict[str, dict] = {}
    for r in recs:  # dernier enregistrement par moteur = état courant
        name = r.get("engine")
        if name:
            engines[name] = r
    trades = [r for r in recs if r.get("action") == "exit"][-20:]
    equity_curve = [{"ts": r.get("ts"), "equity": r.get("equity")}
                    for r in recs if r.get("action") == "exit" and r.get("equity")]
    equity = float(state.get("equity", 1.0)) if state else 1.0

    # Mark-to-market: realised equity is only stamped on closed trades, so a strategy
    # HOLDING an open position looks frozen (x1.0000) even as price moves. Attach the
    # live unrealised P&L of each open position so the panel evolves every poll — the
    # whole point of watching a paper strategy is seeing the open trade breathe.
    positions = (state or {}).get("pos", {}) or {}
    equity_mtm = equity
    for name, e in engines.items():
        pos = positions.get(name)
        cur = e.get("price")
        if pos and pos.get("holding") and pos.get("entry_px") and cur:
            entry = float(pos["entry_px"])
            unreal_pct = (cur / entry - 1.0)  # Donchian engines are long-only breakouts
            atr_risk = float(pos.get("atr_risk") or 0.0)
            risk_pct = float(pos.get("risk_pct") or 0.0)
            # equity impact = risk fraction × (move expressed in units of the ATR risk stop)
            unreal_equity = (risk_pct * ((cur - entry) / atr_risk)) if atr_risk else 0.0
            e["position"] = {
                "open": True, "entry_px": entry, "current_px": cur,
                "unrealized_pct": unreal_pct, "unrealized_equity": unreal_equity,
            }
            equity_mtm += unreal_equity
        else:
            e["position"] = {"open": False}

    peak = max([equity] + [pt["equity"] for pt in equity_curve]) if equity_curve else equity
    dd = (1 - equity / peak) if peak else 0.0
    last_ts = _parse_ts(recs[-1].get("ts")) if recs else None
    age = (datetime.now(UTC) - last_ts).total_seconds() if last_ts else None
    return {
        "engines": engines,
        "trades": trades[::-1],
        "equity": equity,
        "equity_mtm": equity_mtm,
        "open_positions": sum(1 for e in engines.values() if e.get("position", {}).get("open")),
        "equity_curve": equity_curve,
        "drawdown": dd,
        "kill_dd": 0.60,
        "policy": "kelly 6%/trade",
        "poll_age_seconds": age,
    }


def _llm_fade_shadow() -> dict:
    """Panel for scripts/llm_fade_shadow.py -- the OPPOSITE side of every closed
    LLM trading-lab decision, zero capital. Read-only consumer of its own
    append-only journal; never touches the LLM lab or the native portfolio."""
    recs = _read_jsonl(STATE_DIR / "llm_fade_shadow.jsonl")
    state = _read_optional_json(STATE_DIR / "llm_fade_shadow_state.json")
    equity_by_lane = (state or {}).get("equity", {})
    lanes: dict[str, dict] = {}
    for r in recs:
        lane = r.get("lane")
        if not lane:
            continue
        bucket = lanes.setdefault(lane, {"trades": [], "equity_curve": []})
        bucket["trades"].append(r)
        bucket["equity_curve"].append({"ts": r.get("ts"), "equity": r.get("equity"), "price": r.get("exit_price")})

    result: dict[str, dict] = {}
    for lane, bucket in lanes.items():
        trades = bucket["trades"]
        wins = [t for t in trades if float(t.get("opposite_return") or 0) > 0]
        result[lane] = {
            "equity": equity_by_lane.get(lane, 1.0),
            "trade_count": len(trades),
            "win_rate": (len(wins) / len(trades)) if trades else 0.0,
            "equity_curve": bucket["equity_curve"],
            "trades": trades[-20:][::-1],
        }
    last_ts = _parse_ts(recs[-1].get("ts")) if recs else None
    age = (datetime.now(UTC) - last_ts).total_seconds() if last_ts else None
    return {
        "lanes": result,
        "poll_age_seconds": age,
        "note": "Fade shadow (scripts/llm_fade_shadow.py) — inverse des décisions du labo LLM, observation seule, aucun capital réel.",
    }


def _shadow_engine_markers(recs: list[dict], engine: str) -> list[dict]:
    """Entry/exit markers for one scripts/portfolio_shadow.py engine, chart-ready.
    Distinct from `_paper_fill_markers`: this is SIMULATED shadow data (no real
    capital) -- keep it on its own chart (`_shadow_terminal`), never merged into
    a real-money terminal's `real_markers` where it could be mistaken for an
    executed position."""
    out: list[dict] = []
    for r in recs:
        if r.get("engine") != engine or r.get("action") not in ("enter", "exit"):
            continue
        price = r.get("price")
        if price is None:
            continue
        bar_ts = r.get("bar_ts")
        if bar_ts is not None:
            ts_ms = int(bar_ts) * 1000  # exact candle this decision was about
        else:
            # Older log lines (before bar_ts was recorded) only have the poll's
            # wall-clock time, which can be up to ~59 min after the candle it
            # decided on -- reconstruct the candle by flooring to the hour and
            # stepping back one, matching ema_cross_state's `i = len(bars)-2`.
            poll_ts = _parse_ts(r.get("ts"))
            if poll_ts is None:
                continue
            hour_floor = poll_ts.replace(minute=0, second=0, microsecond=0)
            ts_ms = int((hour_floor - timedelta(hours=1)).timestamp() * 1000)
        entry = r["action"] == "enter"
        out.append({
            "ts": ts_ms,
            "price": float(price),
            "kind": "entry" if entry else "exit",
            "label": "SHADOW IN" if entry else f"SHADOW {str(r.get('reason', 'OUT')).upper()}",
        })
    return out


def _shadow_terminal() -> dict:
    """Price + EMA9/EMA21 chart data for the ema_cross_btc / ema_cross_eth shadow
    engines (scripts/portfolio_shadow.py) -- a separate payload from
    `_market_signals` on purpose, since that one carries REAL paper fills."""
    recs = _read_jsonl(STATE_DIR / "portfolio_shadow.jsonl")
    out: dict[str, dict] = {}
    for engine, asset in [("ema_cross_btc", "BTC/USDT"), ("ema_cross_eth", "ETH/USDT")]:
        out[engine] = {
            "asset": asset,
            "candles": _binance_klines(asset, "1h", 500),
            "shadow_markers": _shadow_engine_markers(recs, engine),
            "note": "Shadow (scripts/portfolio_shadow.py) — observation seule, aucun capital réel.",
        }
    return out


def _markets(goal: dict) -> list[dict]:
    """Display-only market watch panel: the runtime asset first, then every asset
    listed in goal.watch_assets. Feeds the top-bar market chips; never touches
    the trading path (worker keeps reading goal.asset only)."""
    runtime = goal.get("asset", "BTC/USDT")
    watch = goal.get("watch_assets") or []
    assets = [runtime] + [a for a in watch if a and a != runtime]
    markets: list[dict] = []
    for asset in assets:
        series = _binance_15m_candles(asset)
        if not series and asset == runtime:
            series = _price_series()
        closes = [c["close"] for c in series if c.get("close")]
        last = closes[-1] if closes else 0.0
        first = closes[0] if closes else 0.0
        markets.append({
            "asset": asset,
            "is_runtime": asset == runtime,
            "last_close": last,
            # change over the fetched 15m window (~3 days), display-only
            "change_pct": ((last / first) - 1.0) if first else 0.0,
            "sparkline": [c["close"] for c in _downsample(series, 160)],
            "candles": _downsample(series, 110),
        })
    return markets


# --- Signal overlays: each asset shows the engine that actually tracks it -------
_SIGNAL_TTL = 120.0
_sig_lock = threading.Lock()
_sig_cache: dict[str, dict] = {}


def _binance_klines(asset: str, interval: str, limit: int = 300) -> list[dict]:
    """Generic Binance OHLCV (single REST call), cached per (asset, interval, limit).
    Display-only — never touches the trading path."""
    key = f"{asset}:{interval}:{limit}"
    now = time.time()
    with _cb_lock:
        c = _cb_cache.get(key)
        if c and c["candles"] and (now - c["ts"]) < _CB_TTL_SECONDS:
            return c["candles"]
    url = (f"https://api.binance.com/api/v3/klines?symbol={_binance_symbol(asset)}"
           f"&interval={interval}&limit={limit}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "0rum-dashboard"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001 - chart display must never break the snapshot.
        return []
    out: list[dict] = []
    for row in sorted(raw, key=lambda r: r[0]):
        try:
            out.append({"ts": int(row[0]), "open": float(row[1]), "high": float(row[2]),
                        "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])})
        except (TypeError, ValueError, IndexError):
            continue
    if out:
        with _cb_lock:
            _cb_cache[key] = {"ts": now, "candles": out}
    return out


def _ak_macd_markers(candles: list[dict]) -> list[dict]:
    """AK MACD entry markers via the REAL brain, so the chart matches the bot.
    The native paper AK sleeve is bidirectional; exits remain bracket/runner-
    managed and are represented by audited fill markers instead."""
    try:
        from orum.external.ak_macd import (  # local import: keep dashboard import-safe
            AkMacdParams, compute_state, _flip_up, _flip_down,
            _strictly_increasing, _strictly_decreasing,
            _other_long_conditions, _other_short_conditions, _regime_at)
    except Exception:  # noqa: BLE001
        return []
    p = AkMacdParams(allow_short=True, regime_filter=True)
    if len(candles) < p.warmup:
        return []
    st = compute_state(candles, p)
    n = len(st.macd); W = p.candidate_window_bars; cb = p.confirmation_bars
    cand = None
    out: list[dict] = []
    for t in range(2, n):
        fu = _flip_up(st.macd, t); fd = _flip_down(st.macd, t)
        reg = _regime_at(st.closes, t)[0] if p.regime_filter else None
        if fu:
            cand = (t, "long")
        elif fd and p.allow_short:
            cand = (t, "short")
        elif cand is not None:
            c0, side = cand
            if t > c0 + W:
                cand = None
            elif side == "long":
                if not (st.macd[t] > st.macd[t - 1]):
                    cand = None
                elif (_strictly_increasing(st.macd, t, cb) and _other_long_conditions(st, t, p)
                      and reg != "unfavorable"):
                    out.append({"ts": candles[t]["ts"], "price": st.closes[t], "kind": "entry", "side": "long", "label": "InL"})
                    cand = None
            else:
                if not (st.macd[t] < st.macd[t - 1]):
                    cand = None
                elif (_strictly_decreasing(st.macd, t, cb) and _other_short_conditions(st, t, p)
                      and reg != "favorable"):
                    out.append({"ts": candles[t]["ts"], "price": st.closes[t], "kind": "entry", "side": "short", "label": "InS"})
                    cand = None
    return out


def _utbot_mtf_markers(m15_candles: list[dict], h1_candles: list[dict]) -> list[dict]:
    """Display the same closed-candle long-only UT Bot contract as the paper engine."""
    from orum.strategies.utbot_mtf import ema_last, utbot_signal_series

    now_ms = int(time.time() * 1000)
    m15_candles = [row for row in m15_candles if int(row["ts"]) + 900_000 <= now_ms]
    if not m15_candles or not h1_candles:
        return []
    buys, _, _ = utbot_signal_series(m15_candles, key_value=6.0, atr_period=10)
    _, sells, _ = utbot_signal_series(m15_candles, key_value=7.0, atr_period=20)
    markers: list[dict] = []
    holding = False
    for index, candle in enumerate(m15_candles):
        decision_ts = int(candle["ts"]) + 900_000
        closed_h1 = [row for row in h1_candles if int(row["ts"]) + 3_600_000 <= decision_ts]
        h1_closes = [float(row["close"]) for row in closed_h1]
        if not h1_closes:
            continue
        trend = ema_last(h1_closes, 200)
        if not holding and buys[index] and trend is not None and h1_closes[-1] > trend:
            markers.append({"ts": candle["ts"], "price": candle["close"], "kind": "entry",
                            "side": "long", "label": "InL", "strategy_id": "btc_utbot_m15_h1"})
            holding = True
        elif holding and sells[index]:
            markers.append({"ts": candle["ts"], "price": candle["close"], "kind": "exit",
                            "side": "long", "label": "OUT", "strategy_id": "btc_utbot_m15_h1"})
            holding = False
    return markers


def _donchian_markers(candles: list[dict], entry_n: int = 20, exit_n: int = 10) -> list[dict]:
    """Operational long-only Donchian entry and signal exit overlay."""
    c = [x["close"] for x in candles]
    out: list[dict] = []
    hold_l = False
    for i in range(entry_n + 2, len(c)):
        hi_e = max(c[i - entry_n - 1:i - 1]); lo_x = min(c[i - exit_n - 1:i - 1])
        if not hold_l and c[i] > hi_e:
            out.append({"ts": candles[i]["ts"], "price": c[i], "kind": "entry", "side": "long", "label": "InL"}); hold_l = True
        elif hold_l and c[i] < lo_x:
            out.append({"ts": candles[i]["ts"], "price": c[i], "kind": "exit", "side": "long", "label": "OUT"}); hold_l = False
    return out


def _gold_cot_markers(candles: list[dict]) -> list[dict]:
    """gold_cot (long-only): go long when the COT commercial index <= 20 (gate opens),
    exit when it climbs back > 20. Joins weekly COT (by release/usable_from date) to the
    daily PAXG bars. Same engine as scripts/portfolio_shadow.gold_cot."""
    try:
        import os as _os
        import datetime as _dt
        _scripts = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "scripts")
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        from data_layer import fetch_cot, cot_index
        rows = fetch_cot(range(2020, 2027), "GOLD - COMMODITY EXCHANGE")
        idxs = cot_index([r["comm_net"] for r in rows], 156)
    except Exception:  # noqa: BLE001 - COT unavailable -> no gold markers, chart still renders.
        return []
    gates: list[tuple[int, float]] = []
    for r, v in zip(rows, idxs):
        stamp = r.get("usable_from") or r.get("report_date")
        try:
            d = _dt.datetime.fromisoformat(str(stamp)[:10]).replace(tzinfo=_dt.timezone.utc)
            gates.append((int(d.timestamp() * 1000), float(v)))
        except Exception:  # noqa: BLE001
            continue
    gates.sort()
    if not gates:
        return []

    def idx_at(ts: int):
        applicable = None
        for ms, v in gates:
            if ms <= ts:
                applicable = v
            else:
                break
        return applicable

    out: list[dict] = []
    hold = False
    for c in candles:
        v = idx_at(c["ts"])
        if v is None:
            continue
        gate_open = v <= 20.0
        if gate_open and not hold:
            out.append({"ts": c["ts"], "price": c["close"], "kind": "entry", "side": "long", "label": "InL"}); hold = True
        elif not gate_open and hold:
            out.append({"ts": c["ts"], "price": c["close"], "kind": "exit", "side": "long", "label": "OUT"}); hold = False
    return out


def _pair_trades(markers: list[dict]) -> list[dict]:
    """Pair each entry with the next exit of the SAME side -> a trade segment
    (entry -> exit) the chart can draw a line for. Green if it closed TP, red if SL."""
    trades: list[dict] = []
    open_by_side: dict[str, dict] = {}
    for m in markers:
        side = m.get("side")
        if m.get("kind") == "entry":
            open_by_side[side] = m
        elif m.get("kind") == "exit" and side in open_by_side:
            e = open_by_side.pop(side)
            trades.append({"entry_ts": e["ts"], "entry_price": e["price"],
                           "exit_ts": m["ts"], "exit_price": m["price"],
                           "side": side, "result": m.get("label")})
    return trades


def _market_signals(goal: dict) -> dict:
    """Return display candles and native-engine overlays for each tracked asset.

    Most cards observe comparable 1h candles. The M15/H1 UT Bot sleeve displays
    its native M15 bars and separately fetches H1 context for the EMA filter.
    This function remains display-only and is not imported by the paper engine.
    """
    now = time.time()
    with _sig_lock:
        c = _sig_cache.get("all")
        if c and (now - c["ts"]) < _SIGNAL_TTL:
            return c["data"]
    runtime = goal.get("asset", "BTC/USDT")
    out: dict[str, dict] = {}

    def _shown(display_candles, markers, n=160):
        display_candles = display_candles[-n:]
        if not display_candles:
            return [], []
        first_ts = display_candles[0]["ts"]
        visible = []
        for marker in markers:
            if marker["ts"] < first_ts:
                continue
            model_marker = dict(marker)
            label = str(model_marker.get("label") or "")
            if not label.upper().startswith("MODEL"):
                model_marker["label"] = f"MODEL {label}".strip()
            visible.append(model_marker)
        return display_candles, visible

    def _signal_payload(
        asset, engine, native_timeframe, marker_fn, note, *, strategy_id, n=480,
        display_timeframe="1h", marker_context_timeframe=None,
    ):
        display = _binance_klines(asset, display_timeframe, 500)
        native = (
            display if native_timeframe == display_timeframe
            else _binance_klines(asset, native_timeframe, 300)
        )
        marker_context = (
            _binance_klines(asset, marker_context_timeframe, 500)
            if marker_context_timeframe else display
        )
        candles, markers = _shown(display, marker_fn(native, marker_context), n=n)
        real_markers = _paper_fill_markers(asset, strategy_id=strategy_id)
        return {
            "asset": asset,
            "engine": engine,
            "strategy_id": strategy_id,
            "timeframe": native_timeframe,
            "display_timeframe": display_timeframe,
            "is_runtime": runtime == asset,
            "candles": candles,
            "markers": markers,
            "real_markers": real_markers,
            "trades": _pair_trades(markers),
            "scenario_mode": "display_only",
            "scenario_note": "Éventail de stress visuel, non probabiliste, jamais utilisé par le moteur.",
            "note": note,
        }

    def _error_payload(
        asset, engine, native_timeframe, exc, *, strategy_id, display_timeframe="1h"
    ):
        return {
            "asset": asset,
            "engine": engine,
            "strategy_id": strategy_id,
            "timeframe": native_timeframe,
            "display_timeframe": display_timeframe,
            "is_runtime": runtime == asset,
            "candles": [],
            "markers": [],
            "real_markers": [],
            "trades": [],
            "scenario_mode": "display_only",
            "scenario_note": "Éventail indisponible sans bougies ; aucune incidence moteur.",
            "note": f"erreur: {exc}",
        }

    try:
        out["BTC/USDT"] = _signal_payload(
            "BTC/USDT", "AK MACD", "4h", lambda native, _display: _ak_macd_markers(native),
            "signal AK MACD calculé en 4 h · fills paper réels IN/TP/SL · sorties bracket ATR",
            strategy_id="btc_ak_macd_4h",
        )
    except Exception as e:  # noqa: BLE001
        out["BTC/USDT"] = _error_payload(
            "BTC/USDT", "AK MACD", "4h", e, strategy_id="btc_ak_macd_4h"
        )

    try:
        out["BTC/USDT::btc_utbot_m15_h1"] = _signal_payload(
            "BTC/USDT", "UT BOT", "15m", _utbot_mtf_markers,
            "signal UT Bot M15 · permission H1 EMA200 · paper long-only",
            strategy_id="btc_utbot_m15_h1",
            display_timeframe="15m", marker_context_timeframe="1h",
        )
    except Exception as e:  # noqa: BLE001
        out["BTC/USDT::btc_utbot_m15_h1"] = _error_payload(
            "BTC/USDT", "UT BOT", "15m", e, strategy_id="btc_utbot_m15_h1",
            display_timeframe="15m",
        )

    try:
        out["ETH/USDT"] = _signal_payload(
            "ETH/USDT", "Donchian 20/10", "1d", lambda native, _display: _donchian_markers(native),
            "signal Donchian calculé en 1 j · cassure 20 j / sortie 10 j · fills paper réels IN/TP/SL",
            strategy_id="eth_donchian",
        )
    except Exception as e:  # noqa: BLE001
        out["ETH/USDT"] = _error_payload(
            "ETH/USDT", "Donchian 20/10", "1d", e, strategy_id="eth_donchian"
        )

    try:
        gate = _read_optional_json(STATE_DIR / "cot_gate.json")  # real source (was retired portfolio_shadow.jsonl)
        idx = gate.get("cot_index")
        gate_open = bool(gate.get("gate_on"))
        out["PAXG/USDT"] = _signal_payload(
            "PAXG/USDT", "Or · fenêtre COT", "1d", lambda native, _display: _gold_cot_markers(native),
            (f"signal COT calculé en 1 j · index ≤20 · actuel "
             f"{idx if idx is not None else '?'} → {'OUVERT' if gate_open else 'FERMÉ'} · fills paper réels IN/TP/SL"),
            strategy_id="gold_cot",
        )
    except Exception as e:  # noqa: BLE001
        out["PAXG/USDT"] = _error_payload(
            "PAXG/USDT", "Or · fenêtre COT", "1d", e, strategy_id="gold_cot"
        )

    with _sig_lock:
        _sig_cache["all"] = {"ts": now, "data": out}
    return out


def build_snapshot() -> dict:
    goal = _read_yaml(STATE_DIR / "goal.yaml")
    strategy = _read_yaml(STATE_DIR / "strategy.yaml")
    heartbeat = _read_json(STATE_DIR / "heartbeat.json")
    watcher = _read_optional_json(STATE_DIR / "orum_watcher.json")
    open_position = _read_optional_json(STATE_DIR / "open_position.json")
    trades = _paper_closed_trades()  # source of truth: unified paper ledger (was trades.jsonl)
    hypotheses = _read_jsonl(STATE_DIR / "hypotheses.jsonl")
    events = _read_jsonl(STATE_DIR / "events.jsonl")
    ext_records = _read_jsonl(STATE_DIR / "external_signals.jsonl")
    history = sorted((STATE_DIR / "history").glob("*.yaml")) if (STATE_DIR / "history").exists() else []

    returns = account_returns(trades, goal)
    wins = [item for item in returns if item > 0]
    losses = [item for item in returns if item < 0]
    drawdown = _max_drawdown(trades, goal)
    reflection_every = int(goal.get("reflection_every", 10))
    # Same gating as orum_watch.maybe_reflect_once: trades closed since the
    # last recorded hypothesis, not the total count modulo the cadence.
    latest_reflection_ts = hypotheses[-1].get("ts") if hypotheses else None
    pending_trades = (
        [trade for trade in trades if str(trade.get("ts", "")) > str(latest_reflection_ts)]
        if latest_reflection_ts
        else trades
    )
    progress = min(len(pending_trades), reflection_every) if reflection_every else 0
    remaining = max(0, reflection_every - len(pending_trades)) if reflection_every else 0

    paper = _paper_state(goal)
    return {
        "asset": goal.get("asset", "BTC/USDT"),
        "mode": "paper",
        "paper": paper,
        "llm_lab": _llm_lab_state(STATE_DIR),
        "legacy_audit": _legacy_audit(),
        "signal_source": str(goal.get("signal_source", "native")),
        "external": _external_feed(events, goal),
        "logs": _paper_logs() + _logs(events),  # live paper heartbeat on top, old worker events as history
        "price_series": _binance_15m_candles(goal.get("asset", "BTC/USDT")) or _price_series(),
        "markets": _markets(goal),
        "market_signals": _market_signals(goal),
        "trade_markers": _trade_markers(trades),
        "signals": _signal_markers(ext_records, events, open_position.get("external_signal_id")),
        "worker": _paper_worker(),
        "portfolio_config": _portfolio_config(),
        "portfolio": _portfolio(trades, goal),
        "research_portfolio": _portfolio_shadow(),
        "shadow_terminal": _shadow_terminal(),
        "llm_fade_shadow": _llm_fade_shadow(),
        "open_position": _open_position(open_position, heartbeat, strategy),
        "last_price": float(heartbeat.get("last_price", 0.0) or (trades[-1].get("exit_price", 0.0) if trades else 0.0)),
        "candles": _candles_from_trades(trades),
        "equity_curve": _equity_curve(trades, goal),
        "trade_count": len(trades),
        "pnl_compound": _compound_return(trades, goal),
        "avg_trade": mean(returns) if returns else 0.0,
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "best_trade": max(returns) if returns else 0.0,
        "worst_trade": min(returns) if returns else 0.0,
        "drawdown": drawdown,
        "score": score(trades, goal),
        "goal": goal,
        "strategy": strategy,
        "heartbeat": heartbeat,
        "latest_trades": trades[-16:][::-1],
        "latest_hypothesis": hypotheses[-1] if hypotheses else {},
        "engine": _engine_status(hypotheses, watcher),
        "activity": _activity(trades, hypotheses, heartbeat, watcher),
        "decisions": _decisions(hypotheses),
        "hypotheses": hypotheses[-8:][::-1],
        "history_versions": [path.name for path in history],
        "guardrail": _guardrail_status(drawdown, goal),
        "champion_reaudit": _champion_reaudit_status(STATE_DIR),
        "reflection": {
            "every": reflection_every,
            "progress": progress,
            "remaining": remaining,
            "ready": len(pending_trades) >= reflection_every,
        },
    }


REFLECT_MIN_INTERVAL_SECONDS = 60.0
_reflect_throttle_lock = threading.Lock()
_last_manual_reflect = 0.0


LEVERAGE_MIN = 1.0
LEVERAGE_MAX = 5.0
LEVERAGE_STEP = 0.5
_strategy_write_lock = threading.Lock()


def set_max_leverage(value) -> dict:
    """Persist ``risk.max_leverage`` into strategy.yaml via a targeted line edit
    (preserves comments/ordering) with an atomic replace. ``load_strategy`` re-reads
    the file on every signal, so the change takes effect without a worker restart.

    The value is clamped to [LEVERAGE_MIN, LEVERAGE_MAX] and snapped to 0.5 steps;
    capping notional only ever shrinks position size, never the SL/TP price levels.
    """
    try:
        lev = float(value)
    except (TypeError, ValueError):
        raise ValueError("max_leverage must be a number")
    if math.isnan(lev) or math.isinf(lev):
        raise ValueError("max_leverage must be finite")
    lev = round(lev / LEVERAGE_STEP) * LEVERAGE_STEP
    lev = max(LEVERAGE_MIN, min(LEVERAGE_MAX, lev))

    path = STATE_DIR / "strategy.yaml"
    with _strategy_write_lock:
        text = path.read_text()
        pattern = re.compile(r"^(\s*)max_leverage:.*$", re.MULTILINE)
        if not pattern.search(text):
            raise ValueError("max_leverage key not found in strategy.yaml")
        replacement = (
            r"\g<1>max_leverage: %.1f"
            r"           # notional cap (× equity) — set via dashboard slider" % lev
        )
        new_text = pattern.sub(replacement, text, count=1)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(new_text)
        os.replace(tmp, path)  # atomic: the worker never reads a half-written file
    return {"ok": True, "max_leverage": lev}


RR_MIN = 0.5
RR_MAX = 5.0
RR_STEP = 0.5


def set_reward_risk(value) -> dict:
    """Persist ``risk.reward_risk_ratio`` (the take-profit R multiple) into
    strategy.yaml via a targeted line edit + atomic replace, mirroring
    ``set_max_leverage``. ``load_strategy`` re-reads on every signal, so the new
    TP applies without a worker restart. Clamped to [RR_MIN, RR_MAX], 0.5 steps."""
    try:
        rr = float(value)
    except (TypeError, ValueError):
        raise ValueError("reward_risk_ratio must be a number")
    if math.isnan(rr) or math.isinf(rr):
        raise ValueError("reward_risk_ratio must be finite")
    rr = round(rr / RR_STEP) * RR_STEP
    rr = max(RR_MIN, min(RR_MAX, rr))

    path = STATE_DIR / "strategy.yaml"
    with _strategy_write_lock:
        text = path.read_text()
        pattern = re.compile(r"^(\s*)reward_risk_ratio:.*$", re.MULTILINE)
        if not pattern.search(text):
            raise ValueError("reward_risk_ratio key not found in strategy.yaml")
        replacement = (
            r"\g<1>reward_risk_ratio: %.1f"
            r"      # TP = entry +/- rr*risk - set via dashboard slider" % rr
        )
        new_text = pattern.sub(replacement, text, count=1)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(new_text)
        os.replace(tmp, path)  # atomic: the worker never reads a half-written file
    return {"ok": True, "reward_risk_ratio": rr}


# --- live portfolio risk (state/portfolio.yaml, read by the paper engine at
# the start of every cycle — run_paper_portfolio.load_config) -----------------
PORTFOLIO_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "portfolio.yaml"
RISK_PCT_STEP = 0.0025


def _portfolio_config() -> dict:
    """The per-strategy risk config the paper engine ACTUALLY loads: the
    state/portfolio.yaml runtime override when present, else the committed
    config/portfolio.yaml fallback — same precedence as the engine."""
    runtime = STATE_DIR / "portfolio.yaml"
    source = runtime if runtime.exists() else PORTFOLIO_DEFAULT_PATH
    doc = _read_yaml(source)
    goal = _read_yaml(STATE_DIR / "goal.yaml")
    return {
        "source": "state/portfolio.yaml" if source == runtime else "config/portfolio.yaml",
        "max_leverage": doc.get("max_leverage"),
        "risk_bounds": {
            "min": float(goal.get("risk_per_trade_min", 0.005)),
            "max": float(goal.get("risk_per_trade_max", 0.02)),
        },
        "strategies": [
            {
                key: s.get(key)
                for key in ("id", "engine", "symbol", "timeframe", "risk_pct",
                            "entry_enabled", "exit_policy", "monitor_timeframe",
                            "reward_risk_ratio", "dynamic_exit")
            }
            for s in (doc.get("strategies") or [])
        ],
    }


def _edit_portfolio_strategy_field(strategy_id: str, field: str, formatted: str) -> None:
    """Targeted line edit of one strategy's field inside state/portfolio.yaml,
    preserving comments and ordering (same approach as set_max_leverage). The
    runtime override is materialized from config/portfolio.yaml on first edit;
    the engine picks the new value up at its next cycle."""
    runtime = STATE_DIR / "portfolio.yaml"
    with _strategy_write_lock:
        if not runtime.exists():
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_text(PORTFOLIO_DEFAULT_PATH.read_text())
        lines = runtime.read_text().splitlines(keepends=True)
        start = end = None
        for index, line in enumerate(lines):
            if re.match(rf"^\s*-\s+id:\s*{re.escape(strategy_id)}\s*(#.*)?$", line):
                start = index
            elif start is not None and re.match(r"^\s*-\s+id:", line):
                end = index
                break
        if start is None:
            raise ValueError(f"unknown strategy_id: {strategy_id!r}")
        end = end if end is not None else len(lines)
        for index in range(start + 1, end):
            match = re.match(rf"^(\s*){re.escape(field)}:", lines[index])
            if match:
                lines[index] = f"{match.group(1)}{field}: {formatted}   # set via dashboard slider\n"
                tmp = runtime.with_name(runtime.name + ".tmp")
                tmp.write_text("".join(lines))
                os.replace(tmp, runtime)  # atomic: the engine never reads a half-written file
                return
        raise ValueError(f"strategy {strategy_id!r} has no {field!r} key")


def set_portfolio_leverage(value) -> dict:
    """Persist the portfolio-wide max_leverage notional cap (× equity) into the
    runtime portfolio override — enforced by PaperBroker.open, which only ever
    SHRINKS position size. Clamped to [LEVERAGE_MIN, LEVERAGE_MAX], 0.5 steps."""
    try:
        lev = float(value)
    except (TypeError, ValueError):
        raise ValueError("max_leverage must be a number")
    if math.isnan(lev) or math.isinf(lev):
        raise ValueError("max_leverage must be finite")
    lev = round(lev / LEVERAGE_STEP) * LEVERAGE_STEP
    lev = max(LEVERAGE_MIN, min(LEVERAGE_MAX, lev))
    runtime = STATE_DIR / "portfolio.yaml"
    with _strategy_write_lock:
        if not runtime.exists():
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_text(PORTFOLIO_DEFAULT_PATH.read_text())
        lines = runtime.read_text().splitlines(keepends=True)
        new_line = f"max_leverage: {lev:.1f}   # notional cap (× equity) — set via dashboard slider\n"
        for index, line in enumerate(lines):
            if re.match(r"^max_leverage:", line):
                lines[index] = new_line
                break
        else:
            for index, line in enumerate(lines):
                if re.match(r"^strategies:", line):
                    lines[index:index] = [new_line, "\n"]
                    break
            else:
                raise ValueError("strategies key not found in portfolio.yaml")
        tmp = runtime.with_name(runtime.name + ".tmp")
        tmp.write_text("".join(lines))
        os.replace(tmp, runtime)  # atomic: the engine never reads a half-written file
    return {"ok": True, "max_leverage": lev,
            "note": "written to state/portfolio.yaml; effective at the engine's next cycle"}


def set_strategy_risk(strategy_id, value) -> dict:
    """Persist a strategy's risk_pct (fraction, e.g. 0.02 = 2%) into the
    runtime portfolio override. Clamped to goal.yaml's risk_per_trade band and
    snapped to 0.25% steps."""
    goal = _read_yaml(STATE_DIR / "goal.yaml")
    lo = float(goal.get("risk_per_trade_min", 0.005))
    hi = float(goal.get("risk_per_trade_max", 0.02))
    try:
        risk = float(value)
    except (TypeError, ValueError):
        raise ValueError("risk_pct must be a number")
    if math.isnan(risk) or math.isinf(risk):
        raise ValueError("risk_pct must be finite")
    risk = round(round(risk / RISK_PCT_STEP) * RISK_PCT_STEP, 4)
    risk = max(lo, min(hi, risk))
    _edit_portfolio_strategy_field(str(strategy_id or ""), "risk_pct", f"{risk:.4f}".rstrip("0").rstrip("."))
    return {"ok": True, "strategy_id": strategy_id, "risk_pct": risk,
            "note": "written to state/portfolio.yaml; effective at the engine's next cycle"}


def set_strategy_reward(strategy_id, value) -> dict:
    """Persist a strategy's reward_risk_ratio (bracket TP multiple) into the
    runtime portfolio override. Clamped to [RR_MIN, RR_MAX], 0.5 steps. Only
    strategies that already carry a reward_risk_ratio key (bracket exits)
    accept this."""
    try:
        rr = float(value)
    except (TypeError, ValueError):
        raise ValueError("reward_risk_ratio must be a number")
    if math.isnan(rr) or math.isinf(rr):
        raise ValueError("reward_risk_ratio must be finite")
    rr = round(rr / RR_STEP) * RR_STEP
    rr = max(RR_MIN, min(RR_MAX, rr))
    _edit_portfolio_strategy_field(str(strategy_id or ""), "reward_risk_ratio", f"{rr:.1f}")
    return {"ok": True, "strategy_id": strategy_id, "reward_risk_ratio": rr,
            "note": "written to state/portfolio.yaml; effective at the engine's next cycle"}


def origin_allowed(origin: str | None, host: str | None) -> bool:
    """Reject cross-origin browser POSTs (any web page can fire requests at
    127.0.0.1). Requests without an Origin header (curl, scripts) pass."""
    if not origin:
        return True
    return urlparse(origin).netloc == (host or "")


def reflect_allowed(now: float, last_ts: float, min_interval: float = REFLECT_MIN_INTERVAL_SECONDS) -> bool:
    return (now - last_ts) >= min_interval


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature.
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, indent=2, sort_keys=True).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler hook.
        try:
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, (STATIC_DIR / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/bot":
                self._send(200, (STATIC_DIR / "bot.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/opening-range":
                self._send(200, (STATIC_DIR / "opening_range.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/assets/dashboard.css":
                self._send(200, (STATIC_DIR / "dashboard.css").read_bytes(), "text/css; charset=utf-8")
            elif path == "/assets/opening-range.css":
                self._send(200, (STATIC_DIR / "opening_range.css").read_bytes(), "text/css; charset=utf-8")
            elif path == "/assets/dashboard.js":
                self._send(200, (STATIC_DIR / "dashboard.js").read_bytes(), "application/javascript")
            elif path == "/assets/bot.js":
                self._send(200, (STATIC_DIR / "bot.js").read_bytes(), "application/javascript")
            elif path == "/assets/opening-range.js":
                self._send(200, (STATIC_DIR / "opening_range.js").read_bytes(), "application/javascript")
            elif path == "/assets/fonts/Montserrat-VariableFont_wght.ttf":
                self._send(200, (STATIC_DIR / "fonts/Montserrat-VariableFont_wght.ttf").read_bytes(), "font/ttf")
            elif path == "/api/state":
                self._send_json(200, build_snapshot())
            elif path == "/api/opening-range":
                self._send_json(200, opening_range_snapshot())
            else:
                self._send_json(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001 - a partial state read must not kill the connection silently.
            try:
                self._send_json(500, {"error": f"snapshot failed: {exc}"})
            except OSError:
                pass

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler hook.
        global _last_manual_reflect
        path = urlparse(self.path).path
        if path not in ("/api/reflect", "/api/worker/start", "/api/worker/stop", "/api/risk/leverage", "/api/risk/reward", "/api/portfolio/risk"):
            self._send_json(404, {"error": "not found"})
            return
        if not origin_allowed(self.headers.get("Origin"), self.headers.get("Host")):
            self._send_json(403, {"ok": False, "error": "cross-origin request rejected"})
            return
        if path == "/api/portfolio/risk":
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
                sid = payload.get("strategy_id")
                if "max_leverage" in payload:
                    self._send_json(200, set_portfolio_leverage(payload.get("max_leverage")))
                elif "risk_pct" in payload:
                    self._send_json(200, set_strategy_risk(sid, payload.get("risk_pct")))
                elif "reward_risk_ratio" in payload:
                    self._send_json(200, set_strategy_reward(sid, payload.get("reward_risk_ratio")))
                else:
                    self._send_json(400, {"ok": False, "error": "risk_pct, reward_risk_ratio or max_leverage required"})
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI.
                self._send_json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/api/risk/leverage":
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
                self._send_json(200, set_max_leverage(payload.get("max_leverage")))
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI.
                self._send_json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/api/risk/reward":
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
                self._send_json(200, set_reward_risk(payload.get("reward_risk_ratio")))
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI.
                self._send_json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/api/worker/start":
            self._send_json(410, start_worker())
            return
        if path == "/api/worker/stop":
            try:
                self._send_json(200, stop_worker())
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI.
                self._send_json(500, {"ok": False, "error": str(exc)})
            return
        with _reflect_throttle_lock:
            now = time.monotonic()
            if not reflect_allowed(now, _last_manual_reflect):
                self._send_json(429, {"ok": False, "error": "reflection was triggered recently; retry later"})
                return
            _last_manual_reflect = now
        try:
            result = subprocess.run(
                [sys.executable, "-m", "orum.reflect", "--0rum"],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                timeout=180,
                check=True,
            )
            self._send_json(200, {"ok": True, "result": json.loads(result.stdout)})
        except Exception as exc:  # noqa: BLE001 - reflected to UI as an actionable error.
            self._send_json(500, {"ok": False, "error": str(exc)})


def _bind_address() -> tuple[str, int]:
    host = os.getenv("ORUM_DASHBOARD_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("ORUM_DASHBOARD_PORT", "8787"))
    except ValueError:
        port = 8787
    return host, port


def main() -> None:
    host, port = _bind_address()
    try:
        server = ThreadingHTTPServer((host, port), DashboardHandler)
    except OSError as exc:
        print(
            f"cannot bind dashboard to {host}:{port}: {exc}. Another instance is "
            "likely already running — check `launchctl list | grep 0rum.dashboard` "
            "before starting one manually.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    print(f"0rum dashboard running at http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
