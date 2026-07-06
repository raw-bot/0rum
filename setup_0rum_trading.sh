#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/0rum-trading"

mkdir -p "$ROOT/orum/adapters" "$ROOT/state/history" "$ROOT/tests"

cat > "$ROOT/state/goal.yaml" <<'YAML'
asset: "BTC/USDT"
target_return_30d: 0.07
exceptional_return_30d: 0.10
good_return_30d: 0.05
warning_return_30d: -0.03
max_drawdown: 0.05
emergency_drawdown: 0.06
min_sharpe: 1.3
failure_below: -0.04
reflection_every: 10
one_variable_only: true
cooldown_trades_after_change: 10
require_issue_repeated_reflections: 2
sharpe_bands:
  reject_below: 1.0
  weak_min: 1.0
  valid_min: 1.2
  strong_min: 1.5
  excellent_min: 2.0
watch_issues:
  - stop trop serre
  - position size trop elevee
  - mauvais regime de volatilite
  - entrees trop tardives
  - sorties trop rapides
  - fees/slippage qui mangent l'edge
  - drawdown concentre sur un type de setup
YAML

cat > "$ROOT/state/strategy.yaml" <<'YAML'
version: "01"
entry:
  indicator: rsi
  threshold: 30
  direction: long
stop_loss_pct: 2.0
position_size_r: 0.5
YAML

touch "$ROOT/state/trades.jsonl" "$ROOT/state/hypotheses.jsonl"
cat > "$ROOT/state/heartbeat.json" <<'JSON'
{"status":"initializing","closed_trades":0}
JSON

cat > "$ROOT/.env" <<'ENV'
ORUM_TRADING_MODE=paper
ORUM_TRADING_I_ACCEPT_RISK=false

# Optional API keys - leave blank for free public data
EXCHANGE_API_KEY=
EXCHANGE_API_SECRET=
GLASSNODE_API_KEY=
NEWS_API_KEY=
ENV

cat > "$ROOT/Dockerfile" <<'DOCKER'
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
COPY pyproject.toml ./
COPY orum ./orum
COPY state ./state
RUN uv sync
ENV ORUM_TRADING_MODE=paper
CMD ["uv", "run", "python", "-m", "orum.run"]
DOCKER

cat > "$ROOT/orum/__init__.py" <<'PY'
__all__ = ["__version__"]
__version__ = "0.1.0"
PY

cat > "$ROOT/orum/adapters/__init__.py" <<'PY'
class SchemaError(RuntimeError):
    """Raised when an adapter returns an unexpected schema."""


EXPECTED_SCHEMA_VERSION = 1


def require_schema(payload: dict) -> dict:
    if payload.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        raise SchemaError(f"bad schema_version: {payload.get('schema_version')!r}")
    return payload
PY

cat > "$ROOT/orum/adapters/price.py" <<'PY'
from __future__ import annotations

import os
import time

import ccxt.async_support as ccxt

from . import require_schema


async def fetch(asset: str = "BTC/USDT") -> dict:
    exchange = ccxt.binance({
        "apiKey": os.getenv("EXCHANGE_API_KEY") or None,
        "secret": os.getenv("EXCHANGE_API_SECRET") or None,
        "enableRateLimit": True,
    })
    try:
        ticker = await exchange.fetch_ticker(asset)
        ohlcv = await exchange.fetch_ohlcv(asset, timeframe="1m", limit=100)
    finally:
        await exchange.close()
    payload = {
        "schema_version": 1,
        "source": "binance",
        "asset": asset,
        "timestamp": int(time.time()),
        "last": float(ticker["last"]),
        "bid": float(ticker.get("bid") or ticker["last"]),
        "ask": float(ticker.get("ask") or ticker["last"]),
        "ohlcv": ohlcv,
    }
    return require_schema(payload)
PY

cat > "$ROOT/orum/adapters/onchain.py" <<'PY'
from __future__ import annotations

import os
import time

import httpx

from . import require_schema


async def fetch(asset: str = "BTC/USDT") -> dict:
    key = os.getenv("GLASSNODE_API_KEY", "")
    symbol = asset.split("/")[0].lower()
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "bitcoin" if symbol == "btc" else symbol, "vs_currencies": "usd"}
    if key:
        params["glassnode_key_present"] = "true"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    payload = {
        "schema_version": 1,
        "source": "coingecko",
        "asset": asset,
        "timestamp": int(time.time()),
        "network_activity_proxy": data,
        "premium_key_used": bool(key),
    }
    return require_schema(payload)
PY

cat > "$ROOT/orum/adapters/news.py" <<'PY'
from __future__ import annotations

import os
import time

import httpx

from . import require_schema


async def fetch(asset: str = "BTC/USDT") -> dict:
    key = os.getenv("NEWS_API_KEY", "")
    query = asset.split("/")[0]
    if key:
        url = "https://newsapi.org/v2/everything"
        params = {"q": query, "sortBy": "publishedAt", "pageSize": 5, "apiKey": key}
    else:
        url = "https://api.coingecko.com/api/v3/search/trending"
        params = {}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    payload = {
        "schema_version": 1,
        "source": "newsapi" if key else "coingecko-trending",
        "asset": asset,
        "timestamp": int(time.time()),
        "items": data.get("articles", data.get("coins", []))[:5],
        "premium_key_used": bool(key),
    }
    return require_schema(payload)
PY

cat > "$ROOT/orum/adapters/macro.py" <<'PY'
from __future__ import annotations

import time

import httpx

from . import require_schema


async def fetch(asset: str = "BTC/USDT") -> dict:
    url = "https://api.coindesk.com/v1/bpi/currentprice/USD.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except Exception:
        data = {"fallback": "macro endpoint unavailable"}
    payload = {
        "schema_version": 1,
        "source": "coindesk",
        "asset": asset,
        "timestamp": int(time.time()),
        "risk_context": data,
    }
    return require_schema(payload)
PY

cat > "$ROOT/orum/score.py" <<'PY'
from __future__ import annotations

import math
from typing import Iterable

import numpy as np


def _returns(trades: Iterable[dict]) -> list[float]:
    values: list[float] = []
    for trade in trades:
        pnl_pct = trade.get("pnl_pct")
        if pnl_pct is not None:
            values.append(float(pnl_pct))
    return values


def realised_return(trades: Iterable[dict]) -> float:
    total = 1.0
    for value in _returns(trades):
        total *= 1.0 + value
    return total - 1.0


def max_drawdown(trades: Iterable[dict]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in _returns(trades):
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return worst


def sharpe(trades: Iterable[dict]) -> float:
    values = _returns(trades)
    if len(values) < 2:
        return 0.0
    std = float(np.std(values, ddof=1))
    if std == 0.0:
        return 0.0
    return float(np.mean(values) / std * math.sqrt(max(len(values), 1)))


def _component(actual: float, target: float) -> float:
    if target == 0:
        return 0.0
    return max(-1.0, min(1.0, actual / target))


def score(trades: Iterable[dict], goal: dict) -> float:
    materialized = list(trades)
    if not materialized:
        return 0.0
    target = float(goal["target_return_30d"])
    max_dd = float(goal["max_drawdown"])
    min_sharpe = float(goal["min_sharpe"])
    ret_score = _component(realised_return(materialized), target)
    dd = max_drawdown(materialized)
    dd_score = 1.0 - min(2.0, dd / max_dd)
    sharpe_score = _component(sharpe(materialized), min_sharpe)
    composite = 0.45 * ret_score + 0.35 * dd_score + 0.20 * sharpe_score
    floor = float(goal.get("failure_below", -1.0))
    return max(-1.0, min(1.0, composite if composite >= floor else -1.0))
PY

cat > "$ROOT/orum/loop.py" <<'PY'
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import aiofiles
import yaml
from rich.console import Console

from .adapters import SchemaError
from .adapters import macro, news, onchain, price
from .score import max_drawdown

ROOT = Path(os.getenv("ORUM_TRADING_ROOT", Path.cwd()))
STATE = ROOT / "state"
console = Console()


async def _retry(name: str, func: Callable[..., Awaitable[dict]], asset: str) -> dict:
    delay = 1.0
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return await func(asset)
        except SchemaError:
            raise
        except Exception as exc:
            last_error = exc
            console.print(f"{name} adapter attempt {attempt}/3 failed: {exc}")
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError(f"{name} adapter failed after 3 attempts") from last_error


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _rsi(ohlcv: list) -> float:
    closes = [float(row[4]) for row in ohlcv[-15:]]
    if len(closes) < 15:
        return 50.0
    deltas = [b - a for a, b in zip(closes, closes[1:])]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [abs(min(delta, 0.0)) for delta in deltas]
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _open_trade(strategy: dict, market: dict) -> dict | None:
    price_payload = market["price"]
    rsi = _rsi(price_payload["ohlcv"])
    entry = strategy.get("entry", {})
    if entry.get("indicator") != "rsi":
        return None
    threshold = float(entry.get("threshold", 30))
    direction = entry.get("direction", "long")
    if direction == "long" and rsi <= threshold:
        return {
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "asset": price_payload["asset"],
            "direction": direction,
            "entry_price": price_payload["last"],
            "entry_rsi": rsi,
            "strategy_version": strategy.get("version", "01"),
        }
    return None


def _close_synthetic(trade: dict, strategy: dict) -> dict:
    stop = float(strategy.get("stop_loss_pct", 2.0)) / 100.0
    drift = random.uniform(-stop * 0.8, stop * 1.4)
    trade = dict(trade)
    trade.update({
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "exit_price": trade["entry_price"] * (1 + drift),
        "pnl_pct": drift,
        "fees_pct": 0.001,
        "status": "closed",
    })
    return trade


async def _append_jsonl(path: Path, payload: dict) -> None:
    async with aiofiles.open(path, "a", encoding="utf-8") as handle:
        await handle.write(json.dumps(payload, sort_keys=True) + "\n")


async def _write_json(path: Path, payload: dict) -> None:
    async with aiofiles.open(path, "w", encoding="utf-8") as handle:
        await handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _load_trades() -> list[dict]:
    path = STATE / "trades.jsonl"
    if not path.exists():
        return []
    trades = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            trades.append(json.loads(line))
    return trades


async def run(asset: str) -> None:
    console.print(f"Booting 0rum-trading worker for {asset} in paper mode")
    failures = 0
    while True:
        try:
            goal = _load_yaml(STATE / "goal.yaml")
            strategy = _load_yaml(STATE / "strategy.yaml")
            market = {
                "price": await _retry("price", price.fetch, asset),
                "onchain": await _retry("onchain", onchain.fetch, asset),
                "news": await _retry("news", news.fetch, asset),
                "macro": await _retry("macro", macro.fetch, asset),
            }
            trade = _open_trade(strategy, market)
            if trade:
                closed = _close_synthetic(trade, strategy)
                await _append_jsonl(STATE / "trades.jsonl", closed)
                console.print(f"closed paper trade {closed['asset']} pnl={closed['pnl_pct']:.4f}")
            trades = _load_trades()
            dd = max_drawdown(trades)
            status = "review" if dd >= float(goal.get("max_drawdown", 1.0)) else "running"
            if dd >= float(goal.get("emergency_drawdown", 1.0)):
                status = "emergency_stop"
            await _write_json(STATE / "heartbeat.json", {
                "asset": asset,
                "status": status,
                "closed_trades": len(trades),
                "drawdown": dd,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            if status == "emergency_stop":
                raise RuntimeError("emergency drawdown reached; no autonomous restart")
            failures = 0
            await asyncio.sleep(60)
        except SchemaError:
            raise
        except Exception as exc:
            failures += 1
            console.print(f"loop failure {failures}/5: {exc}")
            if failures >= 5:
                raise RuntimeError("circuit breaker opened after 5 consecutive failures") from exc
            await asyncio.sleep(min(60, 2 ** failures))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", default=None)
    args = parser.parse_args(argv)
    goal = _load_yaml(STATE / "goal.yaml")
    asyncio.run(run(args.asset or goal.get("asset", "BTC/USDT")))
PY

cat > "$ROOT/orum/run.py" <<'PY'
from __future__ import annotations

from .loop import main


if __name__ == "__main__":
    main()
PY

cat > "$ROOT/orum/reflect.py" <<'PY'
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .score import max_drawdown, realised_return, score

ROOT = Path.cwd()
STATE = ROOT / "state"


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _write_yaml(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)


def _load_trades(limit: int | None = None) -> list[dict]:
    path = STATE / "trades.jsonl"
    trades = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                trades.append(json.loads(line))
    return trades[-limit:] if limit else trades


def _next_version(current: str) -> str:
    return f"{int(current) + 1:02d}"


def _archive(strategy: dict) -> Path:
    version = strategy.get("version", "01")
    target = STATE / "history" / f"v{version}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(STATE / "strategy.yaml", target)
    return target


def _append_hypothesis(payload: dict) -> None:
    with (STATE / "hypotheses.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _fallback_change(strategy: dict, trades: list[dict], goal: dict) -> tuple[dict, dict]:
    updated = json.loads(json.dumps(strategy))
    realised = realised_return(trades)
    drawdown = max_drawdown(trades)
    if drawdown > float(goal["max_drawdown"]):
        variable = "stop_loss_pct"
        before = float(updated.get("stop_loss_pct", 2.0))
        after = max(0.2, before - 0.2)
        updated["stop_loss_pct"] = round(after, 4)
        reason = "drawdown exceeded max, tightening stop loss by 0.2"
    else:
        variable = "entry.threshold"
        before = float(updated["entry"].get("threshold", 30))
        after = before + 2 if realised < float(goal["target_return_30d"]) else max(2, before - 2)
        updated["entry"]["threshold"] = round(after, 4)
        reason = "realised return below target, loosening entry threshold by 2"
    return updated, {
        "mode": "fallback",
        "variable": variable,
        "before": before,
        "after": after,
        "reason": reason,
    }


def _0rum_change(strategy: dict, trades: list[dict], goal: dict) -> tuple[dict, dict]:
    prompt = f"""Read this trading data and propose exactly one YAML variable change.
Goal:
{yaml.safe_dump(goal)}
Strategy:
{yaml.safe_dump(strategy)}
Latest trades:
{json.dumps(trades[-25:], indent=2)}
Return JSON only: {{"variable":"entry.threshold","after":32,"reason":"..."}}.
"""
    result = subprocess.run(["0rum", "--print", prompt], text=True, capture_output=True, check=True)
    hypothesis = json.loads(result.stdout.strip())
    updated = json.loads(json.dumps(strategy))
    variable = hypothesis["variable"]
    before = None
    if variable == "entry.threshold":
        before = updated["entry"]["threshold"]
        updated["entry"]["threshold"] = hypothesis["after"]
    elif variable == "stop_loss_pct":
        before = updated["stop_loss_pct"]
        updated["stop_loss_pct"] = hypothesis["after"]
    elif variable == "position_size_r":
        before = updated["position_size_r"]
        updated["position_size_r"] = hypothesis["after"]
    else:
        raise ValueError(f"unsupported variable from 0rum: {variable}")
    hypothesis.update({"mode": "0rum", "before": before})
    return updated, hypothesis


def reflect(mode: str) -> dict:
    goal = _load_yaml(STATE / "goal.yaml")
    strategy = _load_yaml(STATE / "strategy.yaml")
    trades = _load_trades(25 if mode == "0rum" else None)
    if not trades:
        trades = [{"pnl_pct": -0.001, "status": "synthetic_seed"}]
    _archive(strategy)
    updated, hypothesis = _fallback_change(strategy, trades, goal) if mode == "fallback" else _0rum_change(strategy, trades, goal)
    updated["version"] = _next_version(str(strategy.get("version", "01")))
    hypothesis.update({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "from_version": strategy.get("version", "01"),
        "to_version": updated["version"],
        "score": score(trades, goal),
        "one_variable_only": True,
    })
    _write_yaml(STATE / "strategy.yaml", updated)
    _append_hypothesis(hypothesis)
    return hypothesis


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fallback", action="store_true")
    group.add_argument("--0rum", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(reflect("fallback" if args.fallback else "0rum"), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
PY

cat > "$ROOT/tests/test_score_reflect.py" <<'PY'
from pathlib import Path
import os

import yaml

from orum.reflect import reflect
from orum.score import max_drawdown, score


def test_score_penalizes_drawdown_against_goal():
    trades = [{"pnl_pct": 0.02}, {"pnl_pct": -0.08}]
    goal = {"target_return_30d": 0.07, "max_drawdown": 0.05, "min_sharpe": 1.3, "failure_below": -0.04}
    assert max_drawdown(trades) > goal["max_drawdown"]
    assert score(trades, goal) < 0


def test_fallback_reflection_changes_exactly_one_variable(tmp_path, monkeypatch):
    state = tmp_path / "state"
    (state / "history").mkdir(parents=True)
    (state / "goal.yaml").write_text(
        "asset: BTC/USDT\ntarget_return_30d: 0.07\nmax_drawdown: 0.05\nmin_sharpe: 1.3\nfailure_below: -0.04\n",
        encoding="utf-8",
    )
    (state / "strategy.yaml").write_text(
        'version: "01"\nentry:\n  indicator: rsi\n  threshold: 30\n  direction: long\nstop_loss_pct: 2.0\nposition_size_r: 0.5\n',
        encoding="utf-8",
    )
    (state / "trades.jsonl").write_text('{"pnl_pct": -0.01}\n', encoding="utf-8")
    (state / "hypotheses.jsonl").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = reflect("fallback")
    updated = yaml.safe_load((state / "strategy.yaml").read_text(encoding="utf-8"))

    assert updated["version"] == "02"
    assert result["variable"] == "entry.threshold"
    assert updated["entry"]["threshold"] == 32
    assert updated["stop_loss_pct"] == 2.0
    assert updated["position_size_r"] == 0.5
    assert Path(state / "history" / "v01.yaml").exists()
PY

if [ ! -f "$ROOT/pyproject.toml" ]; then
  (cd "$ROOT" && uv init --no-readme)
fi
(cd "$ROOT" && uv add ccxt yfinance pyyaml httpx aiofiles numpy pandas rich pytest)
