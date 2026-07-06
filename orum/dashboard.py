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
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean
import urllib.request
from urllib.parse import urlparse

import yaml

from orum.accounting import account_returns, compound_balance
from orum.adapters.price import _binance_symbol
from orum.dsl.migrate import risk_value
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


# --- trading engine process control -----------------------------------------
# The dashboard is always-on (launchd) and acts as the remote: this Start/Stop
# toggle launches/stops the whole TRADING ENGINE — worker + watcher + AK MACD
# bridge — via scripts/run_engine.sh, in its OWN detached session. Because the
# engine is a separate process group, stop_worker()'s killpg tears down the
# engine without ever touching the dashboard. The worker owns state/worker.pid
# (run.py), so "is the engine running?" stays a single source of truth.
WORKER_PID_PATH = STATE_DIR / "worker.pid"
WORKER_LOG_PATH = STATE_DIR / "worker.log"
ENGINE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_engine.sh"
ENGINE_LOG_PATH = STATE_DIR / "run_engine.out"


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
    """Start the whole trading engine (worker + watcher + bridge) detached."""
    if worker_running():
        return {"ok": True, "running": True, "pid": _worker_pid(), "detail": "engine already running"}
    root = Path(__file__).resolve().parents[1]
    WORKER_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = open(ENGINE_LOG_PATH, "a")  # noqa: SIM115 - kept open for the child's lifetime
    # Live paper execution, and make sure `uv` is on PATH even under launchd
    # (which does not inherit a login shell PATH).
    env = {**os.environ, "AK_MACD_LIVE": "1"}
    env["PATH"] = os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin")
    proc = subprocess.Popen(
        ["/bin/bash", str(ENGINE_SCRIPT)],
        cwd=root,
        stdout=log,
        stderr=log,
        env=env,
        start_new_session=True,  # detached, own group: survives + killable without hitting the dashboard
    )
    # The worker overwrites this with its own pid on boot; both share the engine's
    # process group, so stop_worker()'s killpg tears down the whole engine either way.
    WORKER_PID_PATH.write_text(str(proc.pid))
    return {"ok": True, "running": True, "pid": proc.pid, "detail": "engine started"}


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
    peak = max([equity] + [pt["equity"] for pt in equity_curve]) if equity_curve else equity
    dd = (1 - equity / peak) if peak else 0.0
    last_ts = _parse_ts(recs[-1].get("ts")) if recs else None
    age = (datetime.now(UTC) - last_ts).total_seconds() if last_ts else None
    return {
        "engines": engines,
        "trades": trades[::-1],
        "equity": equity,
        "equity_curve": equity_curve,
        "drawdown": dd,
        "kill_dd": 0.60,
        "policy": "kelly 6%/trade",
        "poll_age_seconds": age,
    }


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
        })
    return markets


def build_snapshot() -> dict:
    goal = _read_yaml(STATE_DIR / "goal.yaml")
    strategy = _read_yaml(STATE_DIR / "strategy.yaml")
    heartbeat = _read_json(STATE_DIR / "heartbeat.json")
    watcher = _read_optional_json(STATE_DIR / "orum_watcher.json")
    open_position = _read_optional_json(STATE_DIR / "open_position.json")
    trades = _read_jsonl(STATE_DIR / "trades.jsonl")
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

    heartbeat_ts = _parse_ts(heartbeat.get("ts"))
    heartbeat_age = (datetime.now(UTC) - heartbeat_ts).total_seconds() if heartbeat_ts else None

    return {
        "asset": goal.get("asset", "BTC/USDT"),
        "mode": "paper",
        "signal_source": str(goal.get("signal_source", "native")),
        "external": _external_feed(events, goal),
        "logs": _logs(events),
        "price_series": _binance_15m_candles(goal.get("asset", "BTC/USDT")) or _price_series(),
        "markets": _markets(goal),
        "trade_markers": _trade_markers(trades),
        "signals": _signal_markers(ext_records, events, open_position.get("external_signal_id")),
        "worker": {
            "heartbeat_age_seconds": heartbeat_age,
            # 3 missed 60s loop intervals = the worker is presumed dead.
            "stale": heartbeat_age is None or heartbeat_age > 180,
            # process-level truth (is the run_loop actually running?), distinct
            # from heartbeat freshness — lets the UI show ON/OFF reliably.
            "running": worker_running(),
            "pid": _worker_pid(),
        },
        "portfolio": _portfolio(trades, goal),
        "research_portfolio": _portfolio_shadow(),
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
            elif path == "/assets/dashboard.css":
                self._send(200, (STATIC_DIR / "dashboard.css").read_bytes(), "text/css; charset=utf-8")
            elif path == "/assets/dashboard.js":
                self._send(200, (STATIC_DIR / "dashboard.js").read_bytes(), "application/javascript")
            elif path == "/assets/fonts/Montserrat-VariableFont_wght.ttf":
                self._send(200, (STATIC_DIR / "fonts/Montserrat-VariableFont_wght.ttf").read_bytes(), "font/ttf")
            elif path == "/api/state":
                self._send_json(200, build_snapshot())
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
        if path not in ("/api/reflect", "/api/worker/start", "/api/worker/stop", "/api/risk/leverage", "/api/risk/reward"):
            self._send_json(404, {"error": "not found"})
            return
        if not origin_allowed(self.headers.get("Origin"), self.headers.get("Host")):
            self._send_json(403, {"ok": False, "error": "cross-origin request rejected"})
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
            try:
                self._send_json(200, start_worker())
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI.
                self._send_json(500, {"ok": False, "error": str(exc)})
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
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"0rum dashboard running at http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
