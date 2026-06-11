from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean
from urllib.parse import urlparse

import yaml

from hermes_trading.accounting import account_returns, compound_balance
from hermes_trading.paths import STATE_DIR
from hermes_trading.score import score

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
    stop_pct = float(strategy.get("stop_loss_pct", 2.0) or 2.0) / 100.0
    take_profit_pct = float(strategy.get("take_profit_pct", 3.0) or 3.0) / 100.0
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
    available = shutil.which("hermes") is not None
    watcher_ts = _parse_ts(watcher.get("ts"))
    watcher_age = (datetime.now(UTC) - watcher_ts).total_seconds() if watcher_ts else 999999
    watcher_connected = watcher.get("status") in {"standby", "reflecting", "reflected"} and watcher_age < 7200
    if watcher_connected:
        detail = watcher.get("detail", "Hermes watcher is connected; LLM call is pending until reflection threshold.")
    elif mode == "hermes":
        detail = "Hermes has produced manual reflections, but no watcher loop is connected."
    elif available:
        detail = "Hermes CLI is installed, but this bot is still using deterministic fallback reflection."
    else:
        detail = "Hermes CLI is not active in this sandbox run."
    return {
        "active": watcher_connected,
        "available": available,
        "mode": mode,
        "watcher": watcher,
        "label": "Hermes watcher connected" if watcher_connected else "Hermes not connected",
        "detail": detail,
    }


def _activity(trades: list[dict], hypotheses: list[dict], heartbeat: dict, watcher: dict) -> list[dict]:
    events: list[dict] = []
    if watcher:
        events.append(
            {
                "ts": watcher.get("ts"),
                "kind": "decision",
                "title": "Hermes watcher",
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


def build_snapshot() -> dict:
    goal = _read_yaml(STATE_DIR / "goal.yaml")
    strategy = _read_yaml(STATE_DIR / "strategy.yaml")
    heartbeat = _read_json(STATE_DIR / "heartbeat.json")
    watcher = _read_optional_json(STATE_DIR / "hermes_watcher.json")
    open_position = _read_optional_json(STATE_DIR / "open_position.json")
    trades = _read_jsonl(STATE_DIR / "trades.jsonl")
    hypotheses = _read_jsonl(STATE_DIR / "hypotheses.jsonl")
    history = sorted((STATE_DIR / "history").glob("*.yaml")) if (STATE_DIR / "history").exists() else []

    returns = account_returns(trades, goal)
    wins = [item for item in returns if item > 0]
    losses = [item for item in returns if item < 0]
    drawdown = _max_drawdown(trades, goal)
    reflection_every = int(goal.get("reflection_every", 10))
    remainder = len(trades) % reflection_every if reflection_every else 0
    remaining = 0 if len(trades) >= reflection_every and remainder == 0 else reflection_every - remainder

    return {
        "asset": goal.get("asset", "BTC/USDT"),
        "mode": "paper",
        "portfolio": _portfolio(trades, goal),
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
            "progress": remainder,
            "remaining": remaining,
            "ready": len(trades) >= reflection_every,
        },
    }


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

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler hook.
        path = urlparse(self.path).path
        if path != "/api/reflect":
            self._send_json(404, {"error": "not found"})
            return
        try:
            result = subprocess.run(
                [sys.executable, "-m", "hermes_trading.reflect", "--hermes"],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                timeout=180,
                check=True,
            )
            self._send_json(200, {"ok": True, "result": json.loads(result.stdout)})
        except Exception as exc:  # noqa: BLE001 - reflected to UI as an actionable error.
            self._send_json(500, {"ok": False, "error": str(exc)})


def main() -> None:
    host = "127.0.0.1"
    port = 8787
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Hermes dashboard running at http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
