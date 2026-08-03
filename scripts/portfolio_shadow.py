"""Câblage SHADOW du portefeuille multi-moteurs (2026-07-05) — zéro impact live.

Calcule l'état COURANT de chaque moteur validé et journalise les décisions dans
state/portfolio_shadow.jsonl (append-only). Ne touche ni au worker, ni au
goal.yaml, ni au producteur AK 4h existant (qui a déjà son propre live paper).

Moteurs suivis (les validés de portfolio_sim v2, jpy exclu) :
  donchian_btc / donchian_eth : Donchian 20/10 daily long-only.
  gold_cot                    : fenêtre COT or (comm COT-index <= 20, 156w).
Chaque signal d'entrée porte : score de confiance 0-3 (SMA100 + régime 90j +
impulsion 1.5*ATR), risque suggéré (politique "modulé + videur" : score 0 =
SKIP, 1 = 1%, 2 = 2%, 3 = 4%) et kill-criteria de référence.

Usage:
  uv run python scripts/portfolio_shadow.py --once     # un passage (launchd/cron)
  uv run python scripts/portfolio_shadow.py --loop     # boucle horaire (nohup)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import fetch_cot, fetch_daily, fetch_klines, cot_index

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "state", "portfolio_shadow.jsonl")
STATE = os.path.join(REPO, "state", "portfolio_shadow_positions.json")
# Politique ACTIVE (choix cube 2026-07-05) : "secoué" = quart-Kelly par moteur.
# Les 3 moteurs plafonnent au cap -> 6% de risque par trade, score-0 INCLUS.
# Le score reste loggé pour reconstruire a posteriori la courbe "modulé+videur".
POLICY = "kelly_6pct"
KELLY_RISK = 0.06
RISK_BY_SCORE = {0: 0.0, 1: 0.01, 2: 0.02, 3: 0.04}   # référence (politique prudente)
KILL = {"max_losing_streak": 9, "max_dd_pct": 68, "worst_trade_r": -2.7,
        "portfolio_max_dd_pct": 60}  # sim Kelly : pire creux 47.7% -> coupe à 60


def atr14(h, l, c):
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    a, out = 1 / 14, [tr[0]]
    for x in tr[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def score_now(bars):
    c = [b["close"] for b in bars]; h = [b["high"] for b in bars]; l = [b["low"] for b in bars]
    a = atr14(h, l, c)
    i = len(bars) - 1
    ma100 = sum(c[-100:]) / 100
    f1 = c[i] > ma100
    f2 = c[i] / c[i - 90] - 1 > 0
    tr_i = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    f3 = tr_i > 1.5 * a[i]
    return int(f1) + int(f2) + int(f3), a[i]


def donchian_state(bars):
    """Signal sur la DERNIÈRE bougie clôturée : entry si close > max des 20
    précédentes (exclue), exit si close < min des 10 précédentes."""
    c = [b["close"] for b in bars]
    hi20 = max(c[-22:-2]); lo10 = min(c[-12:-2])
    last = c[-2]  # dernière clôturée (la -1 peut être en cours sur klines)
    return ("entry" if last > hi20 else "exit" if last < lo10 else "hold",
            last, hi20, lo10)


def _ema_series(xs, n):
    a = 2 / (n + 1)
    out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def _rma_series(xs, n):
    a = 1 / n
    out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def _adx14(h, l, c):
    n = len(c)
    tr, pdm, mdm = [h[0] - l[0]], [0.0], [0.0]
    for i in range(1, n):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    tr_r, p_r, m_r = _rma_series(tr, 14), _rma_series(pdm, 14), _rma_series(mdm, 14)
    pdi = [100 * p_r[i] / tr_r[i] if tr_r[i] else 0.0 for i in range(n)]
    mdi = [100 * m_r[i] / tr_r[i] if tr_r[i] else 0.0 for i in range(n)]
    dx = [100 * abs(pdi[i] - mdi[i]) / (pdi[i] + mdi[i]) if (pdi[i] + mdi[i]) else 0.0 for i in range(n)]
    return _rma_series(dx, 14)


EMA_CROSS_ADX_TH = 20.0
EMA_CROSS_CONFIRM = 3   # entry needs EMA9>EMA21 held for 3 consecutive closed hours
EMA_CROSS_SWING = 8     # stop = min(EMA21, lowest low of the last 8h), frozen at entry


def ema_cross_state(bars, position):
    """EMA9/EMA21 crossover on hourly bars, long-only. Validated 2026-07-28
    against ETH/USDT: raw crossovers alone lose money (win rate ~27%,
    PF<1 over a year) -- this needs BOTH a confirmed, trend-strength-gated
    entry AND a frozen stop, or it just whipsaws in chop.

    Entry : EMA9 has closed above EMA21 for EMA_CROSS_CONFIRM consecutive
            hours (not just this one -- kills same-hour flip-flops like the
            13/14/15 cluster from the 09-28/07 study) AND ADX(14) > 20 (kills
            the low-trend-strength false starts).
    Exit  : the frozen stop (set once at entry, never moved) is touched, OR
            EMA9 closes back below EMA21 -- acted on IMMEDIATELY, no
            confirmation delay (waiting on the exit side only bleeds
            profit/lets losses run, confirmed on the 25-27/07 ETH trade).
    No fixed take-profit: letting a confirmed reverse cross be the exit
    signal is what lets a winner ride through chop instead of capping it
    at an arbitrary R-multiple.

    `bars` ascending, last bar may still be forming -> index -2 is the last
    CLOSED candle, matching `donchian_state`'s own convention.
    """
    c = [b["close"] for b in bars]
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    ema9 = _ema_series(c, 9)
    ema21 = _ema_series(c, 21)
    adx = _adx14(h, l, c)
    i = len(bars) - 2
    price = c[i]
    bar_ts = bars[i]["time"]  # the actual candle this decision is about -- NOT the poll
    # wall-clock time, which can be up to ~59 min later and would otherwise
    # mis-place the chart marker on a later candle than the one that triggered it.

    if position and position.get("holding"):
        stop = position["stop_loss_price"]
        raw_down = ema9[i] < ema21[i] and ema9[i - 1] >= ema21[i - 1]
        if l[i] <= stop:
            return {"action": "exit", "price": stop, "reason": "stop", "bar_ts": bar_ts}
        if raw_down:
            return {"action": "exit", "price": price, "reason": "cross", "bar_ts": bar_ts}
        return {"action": "hold", "price": price, "ema9": ema9[i], "ema21": ema21[i], "adx": adx[i], "bar_ts": bar_ts}

    if i - EMA_CROSS_CONFIRM < 0:
        return {"action": "flat", "price": price, "adx": adx[i], "bar_ts": bar_ts}
    held = all(ema9[i - k] > ema21[i - k] for k in range(EMA_CROSS_CONFIRM))
    was_below = ema9[i - EMA_CROSS_CONFIRM] <= ema21[i - EMA_CROSS_CONFIRM]
    if held and was_below and adx[i] > EMA_CROSS_ADX_TH:
        recent_low = min(l[max(0, i - EMA_CROSS_SWING + 1):i + 1])
        stop = min(ema21[i], recent_low)
        if stop < price:
            return {"action": "enter", "price": price, "stop_loss_price": stop, "adx": adx[i], "bar_ts": bar_ts}
    return {"action": "flat", "price": price, "adx": adx[i], "bar_ts": bar_ts}


def load_positions():
    if os.path.exists(STATE):
        with open(STATE) as f:
            st = json.load(f)
        if "equity" not in st:  # migration ancien format {eng: bool}
            st = {"equity": 1.0, "pos": {k: {"holding": bool(v)} for k, v in st.items()}}
        return st
    return {"equity": 1.0, "pos": {}}


def log(rec):
    rec["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(OUT, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"  {rec['engine']:13s} {rec['action']:6s} score={rec.get('score','-')} "
          f"risk={rec.get('risk_pct','-')} px={rec.get('price','-')}", flush=True)


FEE_RT = 0.001


def _enter(st, eng, px, sc, risk, atr_risk, extra, extra_state=None):
    pos = {"holding": True, "entry_px": px * (1 + FEE_RT / 2),
           "risk_pct": risk, "atr_risk": atr_risk}
    if extra_state:
        pos.update(extra_state)
    st["pos"][eng] = pos
    rec = {"engine": eng, "action": "enter", "score": sc, "risk_pct": risk,
           "price": px, "equity": round(st["equity"], 4), "kill": KILL}
    rec.update(extra)
    log(rec)


def _exit(st, eng, px, extra):
    p = st["pos"][eng]
    x = px * (1 - FEE_RT / 2)
    r = (x - p["entry_px"]) / p["atr_risk"]
    pnl = p["risk_pct"] * r
    st["equity"] *= 1 + pnl
    rec = {"engine": eng, "action": "exit", "price": px, "r": round(r, 3),
           "pnl_pct": round(pnl * 100, 3), "equity": round(st["equity"], 4)}
    rec.update(extra)
    log(rec)
    st["pos"][eng] = {"holding": False}


def poll_once():
    st = load_positions()
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M}] portfolio PAPER poll "
          f"(equity x{st['equity']:.4f})", flush=True)

    for eng, sym in [("donchian_btc", "BTCUSDT"), ("donchian_eth", "ETHUSDT")]:
        bars = fetch_klines(sym, "1d", 150, cache=False)
        sig, px, hi, lo = donchian_state(bars)
        sc, atr_last = score_now(bars[:-1])
        holding = st["pos"].get(eng, {}).get("holding", False)
        if sig == "entry" and not holding:
            _enter(st, eng, px, sc, KELLY_RISK, 2 * atr_last,
                   {"hi20": hi, "lo10": lo, "policy": POLICY})
        elif sig == "exit" and holding:
            _exit(st, eng, px, {"lo10": lo})
        elif holding:
            log({"engine": eng, "action": "hold", "price": px, "score": sc,
                 "lo10": lo, "dist_exit_pct": round(100 * (px / lo - 1), 2)})
        else:
            log({"engine": eng, "action": "flat", "price": px, "score": sc,
                 "hi20": hi, "dist_entry_pct": round(100 * (hi / px - 1), 2)})

    for eng, sym in [("ema_cross_btc", "BTCUSDT"), ("ema_cross_eth", "ETHUSDT")]:
        bars = fetch_klines(sym, "1h", 300, cache=False)
        pos = st["pos"].get(eng)
        holding = bool(pos and pos.get("holding"))
        result = ema_cross_state(bars, pos if holding else None)
        sc, _ = score_now(bars[:-1])
        if result["action"] == "enter":
            risk_distance = result["price"] - result["stop_loss_price"]
            _enter(st, eng, result["price"], sc, KELLY_RISK, risk_distance,
                   {"stop_loss_price": round(result["stop_loss_price"], 2),
                    "adx": round(result.get("adx", 0), 1), "policy": POLICY,
                    "bar_ts": result["bar_ts"]},
                   extra_state={"stop_loss_price": result["stop_loss_price"]})
        elif result["action"] == "exit" and holding:
            _exit(st, eng, result["price"], {"reason": result["reason"], "bar_ts": result["bar_ts"]})
        elif holding:
            log({"engine": eng, "action": "hold", "price": result["price"], "score": sc,
                 "ema9": round(result.get("ema9", 0), 2), "ema21": round(result.get("ema21", 0), 2)})
        else:
            log({"engine": eng, "action": "flat", "price": result["price"], "score": sc,
                 "adx": round(result.get("adx", 0), 1)})

    cot = fetch_cot(range(2006, 2027), "GOLD - COMMODITY EXCHANGE")
    idx = cot_index([r["comm_net"] for r in cot], 156)
    gate = idx[-1] <= 20.0
    bars = fetch_klines("PAXGUSDT", "1d", 150, cache=False)
    sc, atr_last = score_now(bars[:-1])
    holding = st["pos"].get("gold_cot", {}).get("holding", False)
    px = bars[-2]["close"]
    if gate and not holding:
        _enter(st, "gold_cot", px, sc, KELLY_RISK, 2 * atr_last,
               {"cot_index": round(idx[-1], 1), "report": cot[-1]["report_date"],
                "usable_from": cot[-1]["usable_from"], "policy": POLICY})
    elif not gate and holding:
        _exit(st, "gold_cot", px, {"cot_index": round(idx[-1], 1)})
    else:
        log({"engine": "gold_cot", "action": "hold" if holding else "flat",
             "price": px, "score": sc, "cot_index": round(idx[-1], 1),
             "gate_at": 20.0, "dist_gate": round(idx[-1] - 20.0, 1)})

    with open(STATE, "w") as f:
        json.dump(st, f)


def main():
    if "--loop" in sys.argv:
        while True:
            try:
                poll_once()
            except Exception as e:
                print(f"  poll error (retry next hour): {e}", flush=True)
            time.sleep(3600)
    else:
        poll_once()


if __name__ == "__main__":
    main()
