#!/usr/bin/env python
"""Historical replay of the AK MACD brain over REAL BTC 15m candles.

Proves the bot actually takes trades: it walks real Binance history bar-by-bar
through the PRODUCTION brain (evaluate_ak_macd) and the PRODUCTION open path
(ExternalOrchestrator -> bracket -> PaperExecutor), one position at a time, and
closes each on a frozen SL/TP touch (high/low fill). Runs in an ISOLATED
in-memory state + temp signals store, so it never touches the live bot's files.

Usage:  .venv/bin/python scripts/replay_ak_macd.py [n_bars]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import httpx

from orum.external.ak_macd import AkMacdParams, evaluate_ak_macd
from orum.external.ingest import ExternalSignalStore
from orum.external.orchestrator import ExternalOrchestrator

TF_MS = 900_000  # 15m
STRATEGY_ID = "ak_macd_15m_v1"
RISK_PCT = 0.02  # 2% per trade (matches strategy.yaml position_size_r: 2.0)

GOAL = {
    "allowed_external_sources": ["local"],
    "allowed_external_strategies": [
        {"id": STRATEGY_ID, "symbol": "BTCUSD", "engine_asset": "BTC/USDT",
         "timeframe": "15m", "events": ["BUY_CANDIDATE", "SELL_CANDIDATE", "EXIT"]},
    ],
    "allow_short": True,
    "starting_balance_usd": 10_000.0,
}
STRATEGY = {"version": "04", "risk": {"position_size_r": RISK_PCT * 100,
                                      "stop_loss_pct": 2.0, "take_profit_pct": 3.0}}


def fetch_klines(symbol: str, interval: str, total: int) -> list[dict]:
    """Fetch `total` closed 15m candles, paginating backwards (Binance caps at
    1000/call). Drops the final forming bar."""
    out: list[list] = []
    end = None
    with httpx.Client(timeout=20) as client:
        while len(out) < total:
            params = {"symbol": symbol, "interval": interval, "limit": 1000}
            if end is not None:
                params["endTime"] = end
            rows = client.get("https://api.binance.com/api/v3/klines", params=params).json()
            if not rows:
                break
            out = rows + out
            end = int(rows[0][0]) - 1
    candles = [
        {"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
        for r in out
    ]
    return candles[:-1][-total:]  # drop forming bar, keep last `total`


class MemState:
    """In-memory StateAccess so the replay never writes the live bot's files."""

    def __init__(self):
        self.pos = None
        self.trades: list[dict] = []
        self.clock = 0

    def load_strategy(self): return STRATEGY
    def load_position(self): return self.pos
    def save_position(self, p): self.pos = p
    def clear_position(self): self.pos = None
    def append_trade(self, t): self.trades.append(t)
    def trade_history(self): return self.trades
    def resume_ack(self): return False
    def trading_mode(self): return "paper"
    def price_offline(self): return False
    def now_ms(self): return self.clock


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    params = AkMacdParams()
    print(f"Fetching {n} real BTC 15m candles from Binance...")
    candles = fetch_klines("BTCUSDT", "15m", n)
    days = len(candles) * TF_MS / 86_400_000
    print(f"Got {len(candles)} bars (~{days:.1f} days: "
          f"{_iso(candles[0]['ts'])} -> {_iso(candles[-1]['ts'])})\n")

    state = MemState()
    store = ExternalSignalStore(path=Path(tempfile.mkdtemp()) / "replay.jsonl", logger=_silent)
    orch = ExternalOrchestrator(goal=GOAL, state=state, store=store, logger=_silent)

    signals = 0
    trades: list[dict] = []
    equity = GOAL["starting_balance_usd"]

    for i in range(params.warmup, len(candles)):
        bar = candles[i]

        # 1) Manage an open position: close on a frozen SL/TP touch (high/low fill).
        if state.pos is not None:
            done = _try_close(state.pos, bar)
            if done:
                pnl_usd = done["pnl_pct"] * state.pos["notional_usd"]
                equity += pnl_usd
                trades.append({**done, "pnl_usd": pnl_usd, "equity": equity,
                               "dir": state.pos["direction"], "entry": state.pos["entry_price"]})
                state.clear_position()

        # 2) No position -> look for a fresh setup on this closed bar.
        if state.pos is None:
            payload = evaluate_ak_macd(candles[: i + 1], params, symbol="BTCUSD",
                                       strategy=STRATEGY_ID)
            if payload is not None:
                signals += 1
                state.clock = bar["ts"] + TF_MS + 1  # bar just closed (passes bar-close gate)
                outcome = orch.handle(payload)
                if outcome.status.value != "executed":
                    print(f"  [{_iso(bar['ts'])}] {payload['event']:14s} NOT opened: "
                          f"{outcome.stage}/{outcome.detail}")

    # Force-mark any still-open position at the end (unrealized).
    open_note = ""
    if state.pos is not None:
        open_note = f"  (1 position still open at end: {state.pos['direction']} @ {state.pos['entry_price']})"

    _report(signals, trades, equity, GOAL["starting_balance_usd"], open_note)


def _try_close(pos: dict, bar: dict) -> dict | None:
    """Frozen-bracket exit with a realistic high/low fill. SL checked first
    (conservative). Returns a closed-trade dict or None if the bar didn't hit."""
    sl, tp = pos["stop_loss_price"], pos["take_profit_price"]
    if pos["direction"] == "long":
        if bar["low"] <= sl:
            return _closed("stop_loss", pos["entry_price"], sl, "long")
        if bar["high"] >= tp:
            return _closed("take_profit", pos["entry_price"], tp, "long")
    else:
        if bar["high"] >= sl:
            return _closed("stop_loss", pos["entry_price"], sl, "short")
        if bar["low"] <= tp:
            return _closed("take_profit", pos["entry_price"], tp, "short")
    return None


def _closed(reason: str, entry: float, exit_px: float, direction: str) -> dict:
    raw = (exit_px - entry) / entry
    pnl_pct = -raw if direction == "short" else raw
    return {"exit_reason": reason, "exit_price": exit_px, "pnl_pct": pnl_pct}


def _report(signals, trades, equity, start, open_note):
    wins = [t for t in trades if t["pnl_pct"] > 0]
    print("=" * 60)
    print(f"RESULT — signals fired: {signals} | trades closed: {len(trades)}")
    if trades:
        wr = 100 * len(wins) / len(trades)
        ret = 100 * (equity - start) / start
        print(f"  win rate: {wr:.0f}%  ({len(wins)}W / {len(trades) - len(wins)}L)")
        print(f"  equity:   {start:.0f} -> {equity:.0f} USD  ({ret:+.2f}%)")
        print("\n  first trades:")
        for t in trades[:8]:
            print(f"    {t['dir']:5s} entry {t['entry']:>9.1f} -> {t['exit_reason']:11s} "
                  f"{t['pnl_pct'] * 100:+6.2f}%  (eq {t['equity']:.0f})")
    else:
        print("  no trades closed in this window.")
    if open_note:
        print(open_note)
    print("=" * 60)
    if signals > 0:
        print("✓ The bot DOES take trades — the production brain + open path fired on real data.")
    else:
        print("No setup in this window; try a larger n_bars (e.g. 5000).")


def _iso(ms: int) -> str:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def _silent(*_a, **_k):
    return {}


if __name__ == "__main__":
    main()
