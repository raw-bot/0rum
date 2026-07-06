"""Baseline backtest of the CURRENT AK MACD strategy with the new sizing guards.

Goal: an honest, no-tuning baseline of the strategy exactly as it trades today
(long + short, frozen-at-entry SL/TP bracket, bracket-only exits) BUT routed
through the engine's real ``bracket_sizing`` so the leverage cap and the
fee-adjusted reward/risk refusal are exercised. Sweeps max_leverage 1x/2x/3x.

Untouched (as required): no trailing, no partial TP, no time-stop, no
score-based sizing. SL/TP price levels are computed by the same compute_bracket
the live engine uses — the edge is not modified.

Assumptions (documented so the numbers are reproducible):
  * Non-compounding: risk and notional caps reference a FIXED equity
    (START_EQUITY) every trade, so the 1x/2x/3x comparison is clean (no
    path-dependency confound). Final equity is reported as a simple sum of P&L.
  * Same fees+slippage as scripts/backtest_ak_macd_long.py.
  * The RR-refusal gate uses the live fee_rate only (no slippage) — it mirrors
    exactly what the live orchestrator passes to bracket_sizing; execution P&L
    then applies fees AND slippage (so realized R is slightly below the gate).
  * SL assumed to fill first when SL and TP are both inside a bar (conservative).

Usage: uv run python scripts/baseline_ak_macd.py [n_bars]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

from orum.external.bracket import bracket_sizing, compute_bracket

# ---- indicator params (ak_macd_15m.pine defaults — DO NOT tune) ----
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
BASE_LEN, ATR_LEN, ATR_MULT = 30, 14, 0.2
VOL_MA_LEN = 9
TREND_LOOK, PULL_LOOK, SWING_LOOK = 50, 10, 10
RR = 1.5

# ---- account / sizing (matches strategy.yaml + engine defaults) ----
START_EQUITY = 10_000.0
RISK_PCT = 0.02          # position_size_r: 2.0
FEE_PCT = 0.0004         # taker per side (~0.08% round trip) — also the gate fee
SLIP_PCT = 0.0003        # adverse slippage per side (execution only)
MIN_REWARD_RISK = 1.0    # strategy default; RR-refusal floor (leverage-independent)
LEVERAGE_SWEEP = (1.0, 2.0, 3.0)


def fetch_klines(symbol="BTCUSDT", interval="15m", n=50000):
    out: list[dict] = []
    end = int(time.time() * 1000)
    while len(out) < n:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&limit=1000&endTime={end}")
        req = urllib.request.Request(url, headers={"User-Agent": "0rum-backtest"})
        with urllib.request.urlopen(req, timeout=30) as r:
            batch = json.loads(r.read().decode())
        if not batch:
            break
        rows = [{"time": k[0] // 1000, "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in batch]
        out = rows + out
        end = batch[0][0] - 1
        time.sleep(0.25)
        if len(batch) < 1000:
            break
    return out[-n:]


def ema(xs, n):
    a = 2 / (n + 1); out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def rma(xs, n):
    a = 1 / n; out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def sma(xs, n):
    out = []
    for i in range(len(xs)):
        win = xs[max(0, i - n + 1): i + 1]
        out.append(sum(win) / len(win))
    return out


def true_range(h, l, c):
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return tr


def barssince(flags, i):
    for k in range(i, -1, -1):
        if flags[k]:
            return i - k
    return 10**9


def generate_signals(bars):
    """Replicate ak_macd_15m.pine setups → list of (i, direction, bracket)."""
    h = [b["high"] for b in bars]; l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]; v = [b["volume"] for b in bars]
    n = len(c)
    macd = [a - b for a, b in zip(ema(c, MACD_FAST), ema(c, MACD_SLOW))]
    base = ema(c, BASE_LEN)
    atr = rma(true_range(h, l, c), ATR_LEN)
    vma = sma(v, VOL_MA_LEN)
    isBlue = [c[i] > base[i] + atr[i] * ATR_MULT for i in range(n)]
    isRed = [c[i] < base[i] - atr[i] * ATR_MULT for i in range(n)]
    isGray = [not isBlue[i] and not isRed[i] for i in range(n)]
    grayOrRed = [isGray[i] or isRed[i] for i in range(n)]
    grayOrBlue = [isGray[i] or isBlue[i] for i in range(n)]
    lowN = [min(l[max(0, i - SWING_LOOK + 1): i + 1]) for i in range(n)]
    highN = [max(h[max(0, i - SWING_LOOK + 1): i + 1]) for i in range(n)]

    signals = []
    degenerate = 0
    i = 2
    while i < n - 1:
        flipUp = macd[i] > macd[i - 1] and macd[i - 1] <= macd[i - 2]
        flipDown = macd[i] < macd[i - 1] and macd[i - 1] >= macd[i - 2]
        volOk = v[i] > vma[i]
        buy = (barssince(isBlue, i) < TREND_LOOK and barssince(grayOrRed, i) < PULL_LOOK
               and c[i] > base[i] and flipUp and macd[i] > 0 and volOk)
        sell = (barssince(isRed, i) < TREND_LOOK and barssince(grayOrBlue, i) < PULL_LOOK
                and c[i] < base[i] and flipDown and macd[i] < 0 and volOk)
        if not (buy or sell):
            i += 1; continue
        direction = "long" if buy else "short"
        try:
            br = compute_bracket(entry_price=c[i], baseline_at_entry=base[i],
                                 recent_low=lowN[i], recent_high=highN[i],
                                 direction=direction, rr=RR)
        except ValueError:
            degenerate += 1; i += 1; continue
        signals.append((i, direction, br))
        # Advance past this setup's resolution is handled in the sim; here we step
        # one bar so overlapping setups are still detected (sim de-overlaps).
        i += 1
    return signals, bars, degenerate


def simulate(signals, bars, max_leverage):
    """Run the accepted-trade simulation for one leverage cap."""
    h = [b["high"] for b in bars]; l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]; t = [b["time"] for b in bars]
    n = len(c)
    target_risk = START_EQUITY * RISK_PCT

    accepted, rejected, reject_reasons = [], 0, {}
    busy_until = -1  # one position at a time (mirrors the live single-position book)
    same_bar = 0

    for (i, direction, br) in signals:
        if i <= busy_until:
            continue
        sizing = bracket_sizing(
            account_equity=START_EQUITY, risk_pct=RISK_PCT,
            risk_distance=br.risk_distance, entry_price=br.entry_price,
            reward_risk_ratio=br.reward_risk_ratio,
            max_leverage=max_leverage, fee_rate=FEE_PCT, min_reward_risk=MIN_REWARD_RISK,
        )
        if not sizing["accepted"]:
            rejected += 1
            key = "real reward/risk < min" if "reward/risk" in (sizing["reject_reason"] or "") else sizing["reject_reason"]
            reject_reasons[key] = reject_reasons.get(key, 0) + 1
            continue

        qty = sizing["qty_base"]
        notional = sizing["notional_usd"]
        sl, tp = br.stop_loss_price, br.take_profit_price
        entry = br.entry_price * (1 + SLIP_PCT) if direction == "long" else br.entry_price * (1 - SLIP_PCT)

        outcome, exit_i, exit_fill = None, None, None
        for j in range(i + 1, n):
            hit_sl = (l[j] <= sl) if direction == "long" else (h[j] >= sl)
            hit_tp = (h[j] >= tp) if direction == "long" else (l[j] <= tp)
            if hit_sl and hit_tp:
                same_bar += 1
            if hit_sl:
                exit_fill = sl * (1 - SLIP_PCT) if direction == "long" else sl * (1 + SLIP_PCT)
                outcome, exit_i = "loss", j; break
            if hit_tp:
                exit_fill = tp
                outcome, exit_i = "win", j; break
        if outcome is None:
            break  # ran out of bars with position open

        gross = (exit_fill - entry) * qty if direction == "long" else (entry - exit_fill) * qty
        fees = FEE_PCT * (entry + exit_fill) * qty
        pnl = gross - fees
        accepted.append({
            "dir": direction, "t": t[i], "outcome": outcome, "pnl": pnl,
            "r": pnl / target_risk, "notional": notional, "leverage": notional / START_EQUITY,
            "capped": sizing["capped"],
        })
        busy_until = exit_i

    return {
        "accepted": accepted, "rejected": rejected, "reject_reasons": reject_reasons,
        "same_bar": same_bar, "target_risk": target_risk,
    }


def metrics(res):
    g = res["accepted"]
    if not g:
        return None
    wins = [x for x in g if x["outcome"] == "win"]
    losses = [x for x in g if x["outcome"] == "loss"]
    gross_win = sum(x["pnl"] for x in wins)
    gross_loss = -sum(x["pnl"] for x in losses)
    net = sum(x["pnl"] for x in g)
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    # equity curve (non-compounding sum) + max drawdown
    eq = START_EQUITY; peak = eq; max_dd = 0.0
    for x in g:
        eq += x["pnl"]; peak = max(peak, eq); max_dd = max(max_dd, peak - eq)
    # max losing streak
    streak = cur = 0
    for x in g:
        cur = cur + 1 if x["outcome"] == "loss" else 0
        streak = max(streak, cur)
    notionals = [x["notional"] for x in g]
    leverages = [x["leverage"] for x in g]
    worst = min(g, key=lambda x: x["pnl"])
    return {
        "n": len(g), "wins": len(wins), "losses": len(losses),
        "winrate": len(wins) / len(g),
        "pf": pf,
        "exp_r_mult": sum(x["r"] for x in g) / len(g),
        "exp_usd": net / len(g),
        "net": net, "final_eq": START_EQUITY + net,
        "max_dd": max_dd, "max_dd_pct": max_dd / START_EQUITY,
        "worst_usd": worst["pnl"], "worst_r": worst["r"], "worst_pct_eq": worst["pnl"] / START_EQUITY,
        "max_losing_streak": streak,
        "notional_avg": sum(notionals) / len(notionals), "notional_max": max(notionals),
        "lev_avg": sum(leverages) / len(leverages), "lev_max": max(leverages),
        "capped_count": sum(1 for x in g if x["capped"]),
    }


def main():
    n_target = int(sys.argv[1]) if len(sys.argv) > 1 else 50000
    print(f"Fetching ~{n_target} BTCUSDT 15m bars from Binance ...", flush=True)
    bars = fetch_klines(n=n_target)
    t = [b["time"] for b in bars]
    span = (datetime.fromtimestamp(t[0], timezone.utc), datetime.fromtimestamp(t[-1], timezone.utc))
    print(f"Got {len(bars)} bars: {span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d}", flush=True)

    signals, bars, degenerate = generate_signals(bars)
    print(f"Raw setups: {len(signals)} valid brackets  (+{degenerate} degenerate/no-bracket)\n", flush=True)

    print("=" * 92)
    print("CONFIGURATION")
    print("=" * 92)
    print(f"  Strategy        : AK MACD 15m (long+short), frozen SL/TP bracket, bracket-only exits")
    print(f"  Indicator params: MACD {MACD_FAST}/{MACD_SLOW}/{MACD_SIG}, baseline EMA{BASE_LEN}, "
          f"ATR{ATR_LEN}×{ATR_MULT}, volMA{VOL_MA_LEN}, RR {RR}")
    print(f"  Account         : start ${START_EQUITY:,.0f}, risk {RISK_PCT:.0%}/trade (non-compounding ref)")
    print(f"  Costs           : fee {FEE_PCT:.2%}/side, slippage {SLIP_PCT:.2%}/side")
    print(f"  Sizing gate     : min_reward_risk {MIN_REWARD_RISK} (fee-adjusted), single position at a time")
    print(f"  Data window     : {span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d}  ({len(bars)} bars)\n")

    results = {lev: simulate(signals, bars, lev) for lev in LEVERAGE_SWEEP}
    mets = {lev: metrics(res) for lev, res in results.items()}

    # reject reasons are leverage-independent (RR gate) — print once from 1x.
    base = results[LEVERAGE_SWEEP[0]]
    n_signals_seen = len(base["accepted"]) + base["rejected"]
    print("=" * 92)
    print("ACCEPT / REJECT BY SIZING  (after one-position-at-a-time de-overlap)")
    print("=" * 92)
    print(f"  Signals evaluated : {n_signals_seen}")
    print(f"  Accepted (traded) : {len(base['accepted'])}")
    print(f"  Rejected by sizing: {base['rejected']}")
    if base["reject_reasons"]:
        for reason, cnt in sorted(base["reject_reasons"].items(), key=lambda kv: -kv[1]):
            print(f"      - {reason}: {cnt}")
    else:
        print("      (none)")
    print(f"  Note: RR-refusal is leverage-independent → accepted/rejected set is identical at 1x/2x/3x.")
    print(f"  SL+TP same-bar (ambiguous, SL assumed): {base['same_bar']}\n")

    print("=" * 92)
    print("LEVERAGE COMPARISON  (same trade set; only position size / exposure differ)")
    print("=" * 92)
    hdr = (f"  {'metric':<24}" + "".join(f"{f'{lev:.0f}x':>14}" for lev in LEVERAGE_SWEEP))
    print(hdr); print("  " + "-" * (24 + 14 * len(LEVERAGE_SWEEP)))

    def row(label, fn, fmt):
        cells = "".join(f"{fmt(mets[lev]):>14}" for lev in LEVERAGE_SWEEP)
        print(f"  {label:<24}{cells}")

    if all(mets.values()):
        row("trades",            None, lambda m: f"{m['n']}")
        row("winrate",           None, lambda m: f"{m['winrate']:.1%}")
        row("profit factor",     None, lambda m: f"{m['pf']:.2f}")
        row("expectancy (R)",    None, lambda m: f"{m['exp_r_mult']:+.3f}")
        row("expectancy ($)",    None, lambda m: f"${m['exp_usd']:+.2f}")
        row("net P&L ($)",       None, lambda m: f"${m['net']:+,.0f}")
        row("final equity ($)",  None, lambda m: f"${m['final_eq']:,.0f}")
        row("max drawdown ($)",  None, lambda m: f"${m['max_dd']:,.0f}")
        row("max drawdown (%)",  None, lambda m: f"{m['max_dd_pct']:.1%}")
        row("worst trade ($)",   None, lambda m: f"${m['worst_usd']:+,.0f}")
        row("worst trade (%eq)", None, lambda m: f"{m['worst_pct_eq']:.1%}")
        row("max losing streak", None, lambda m: f"{m['max_losing_streak']}")
        row("notional avg ($)",  None, lambda m: f"${m['notional_avg']:,.0f}")
        row("notional max ($)",  None, lambda m: f"${m['notional_max']:,.0f}")
        row("leverage avg",      None, lambda m: f"{m['lev_avg']:.2f}x")
        row("leverage max",      None, lambda m: f"{m['lev_max']:.2f}x")
        row("trades capped",     None, lambda m: f"{m['capped_count']}")
    else:
        print("  (no accepted trades — widen the data window)")
    print("=" * 92)


if __name__ == "__main__":
    main()
