"""Simulation de portefeuille multi-moteurs + sizing par confiance (2026-07-05).

Question de cube : un levier dynamique indexé sur un score de confiance à
l'entrée bat-il le sizing plat ? Réponse en 3 temps :
  1. Chaque moteur produit ses trades datés avec un SCORE de confluence (0-3)
     calculé sur des états mesurables à l'entrée (jamais de feeling) :
       f1 tendance : close vs SMA100 (aligné avec le sens du trade)
       f2 régime   : rendement 90j aligné avec le sens
       f3 impulsion: true range du jour d'entrée > 1.5*ATR14 (displacement)
                     [moteur COT : f3 = extrême profond, index <= 10]
  2. VALIDATION du score : buckets d'espérance (R) sur les trades poolés.
     Si pas monotone -> le levier dynamique est enterré, sizing plat.
  3. Portefeuille commun (équité partagée, fixed-fractional du moment) sous
     3 politiques : plat 2% / modulé 1-2-4% selon score / quart-Kelly par
     moteur (calculé full-sample -> optimiste, marqué comme tel).

Moteurs v1 (le 4h AK live sera ajouté en v2 via export de trades) :
  E1 donchian_btc   : Donchian 20/10 daily BTC, long only (validé bench)
  E2 donchian_jpy   : Donchian 20/10 daily USDJPY, long ET short (FX symétrique)
  E3 gold_cot       : fenêtre COT or (comm<=20, 156w), hold-while-gate, long
R = (sortie-entrée)/risque, risque = 2*ATR14 à l'entrée, frais 0.1% RT inclus
dans les prix d'entrée/sortie.

Usage: uv run python scripts/portfolio_sim.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import fetch_cot, fetch_daily, fetch_klines, cot_index

FEE_RT = 0.001
LOOKBACK, LO = 156, 20.0


def atr14(h, l, c):
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    a, out = 1 / 14, [tr[0]]
    for x in tr[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def sma(xs, n):
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / min(i + 1, n))
    return out


def score_at(i, side, c, ma100, a, h, l, extra=0):
    f1 = (c[i] > ma100[i]) if side > 0 else (c[i] < ma100[i])
    r90 = c[i] / c[max(0, i - 90)] - 1
    f2 = (r90 > 0) if side > 0 else (r90 < 0)
    tr_i = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) if i else 0
    f3 = tr_i > 1.5 * a[i] if not extra else bool(extra - 1)
    return int(f1) + int(f2) + int(f3)


def donchian_trades(bars, name, allow_short):
    o = [b["open"] for b in bars]; h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]; c = [b["close"] for b in bars]
    t = [b["time"] for b in bars]
    a, ma100 = atr14(h, l, c), sma(c, 100)
    trades, pos, side, px, risk, e_i = [], 0, 0, 0.0, 0.0, 0
    sc = 0
    for i in range(102, len(bars)):
        hi = max(c[i - 21:i - 1]); lo10 = min(c[i - 11:i - 1])
        lo20 = min(c[i - 21:i - 1]); hi10 = max(c[i - 11:i - 1])
        if pos == 0:
            if c[i - 1] > hi:
                pos, side = 1, 1
                px, risk, e_i = o[i] * (1 + FEE_RT / 2), 2 * a[i - 1], i
                sc = score_at(i - 1, 1, c, ma100, a, h, l)
            elif allow_short and c[i - 1] < lo20:
                pos, side = 1, -1
                px, risk, e_i = o[i] * (1 - FEE_RT / 2), 2 * a[i - 1], i
                sc = score_at(i - 1, -1, c, ma100, a, h, l)
        elif side > 0 and c[i - 1] < lo10:
            x = o[i] * (1 - FEE_RT / 2)
            trades.append((t[e_i], t[i], (x - px) / risk, name, sc))
            pos = 0
        elif side < 0 and c[i - 1] > hi10:
            x = o[i] * (1 + FEE_RT / 2)
            trades.append((t[e_i], t[i], (px - x) / risk, name, sc))
            pos = 0
    return trades


def gold_cot_trades():
    bars = fetch_daily("GC=F", "2005-01-01")
    cot = fetch_cot(range(2006, 2027), "GOLD - COMMODITY EXCHANGE")
    ic = cot_index([r["comm_net"] for r in cot], LOOKBACK)
    o = [b["open"] for b in bars]; h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]; c = [b["close"] for b in bars]
    t = [b["time"] for b in bars]
    dates = [datetime.fromtimestamp(x, tz=timezone.utc).strftime("%Y-%m-%d") for x in t]
    a, ma100 = atr14(h, l, c), sma(c, 100)
    gate, deep, k, cur, curd = [False] * len(bars), [False] * len(bars), 0, False, False
    for i, d in enumerate(dates):
        while k < len(cot) and cot[k]["usable_from"] <= d:
            if k >= LOOKBACK:
                cur, curd = ic[k] <= LO, ic[k] <= 10.0
            k += 1
        gate[i], deep[i] = cur, curd
    trades, pos, px, risk, e_i, sc = [], 0, 0.0, 0.0, 0, 0
    for i in range(102, len(bars)):
        if pos == 0 and gate[i - 1]:
            pos, px, risk, e_i = 1, o[i] * (1 + FEE_RT / 2), 2 * a[i - 1], i
            sc = score_at(i - 1, 1, c, ma100, a, h, l, extra=1 + int(deep[i - 1]))
        elif pos == 1 and not gate[i - 1]:
            x = o[i] * (1 - FEE_RT / 2)
            trades.append((t[e_i], t[i], (x - px) / risk, "gold_cot", sc))
            pos = 0
    return trades


def engine_stats(trades, name):
    rs = [r for (_, _, r, n, _) in trades if n == name]
    if not rs:
        return
    wins = [r for r in rs if r > 0]
    pf = sum(wins) / max(1e-9, -sum(r for r in rs if r <= 0))
    yrs = (max(t2 for (_, t2, _, n, _) in trades if n == name)
           - min(t1 for (t1, _, _, n, _) in trades if n == name)) / 86400 / 365.25
    print(f"  {name:13s}: {len(rs):3d} tr ({len(rs)/yrs:4.1f}/an) | win {100*len(wins)/len(rs):4.1f}% "
          f"| PF {pf:4.2f} | exp {sum(rs)/len(rs):+.3f}R | net {sum(rs):+7.1f}R")


def kelly_quarter(trades, name, cap=0.06):
    rs = [r for (_, _, r, n, _) in trades if n == name]
    wins = [r for r in rs if r > 0]; losses = [r for r in rs if r <= 0]
    if not wins or not losses:
        return 0.02
    w = len(wins) / len(rs)
    ratio = (sum(wins) / len(wins)) / max(1e-9, -sum(losses) / len(losses))
    f = w - (1 - w) / ratio
    return max(0.005, min(cap, f / 4))


def run_policy(trades, label, risk_fn):
    eq, peak, mdd = 1.0, 1.0, 0.0
    yearly = {}
    for (t1, t2, r, name, sc) in sorted(trades, key=lambda x: x[1]):
        risk = risk_fn(name, sc)
        prev = eq
        eq *= 1 + risk * r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
        y = datetime.fromtimestamp(t2, tz=timezone.utc).year
        yearly[y] = yearly.get(y, 1.0) * (eq / prev)
    span = (max(t2 for (_, t2, *_ ) in trades) - min(t1 for (t1, *_ ) in trades)) / 86400 / 365.25
    cagr = eq ** (1 / span) - 1
    worst_y, worst = min(yearly.items(), key=lambda kv: kv[1])
    print(f"  {label:22s}: x{eq:7.2f} | CAGR {cagr*100:+6.1f}% | maxDD {mdd*100:5.1f}% "
          f"| pire année {worst_y} {100*(worst-1):+.1f}%")


def ak4h_trades():
    """Moteur live : AK MACD 4h long-only runner (import du script de validation).
    Exit ts approximé = entry ts (ordre de composition par entrée). Score calculé
    sur le daily BTC au jour d'entrée (même définition que les autres moteurs)."""
    import backtest_4h_validation as v4
    bars = v4.fetch_klines(n=17000)
    h = [b["high"] for b in bars]; l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]; ts = [b["ts"] // 1000 for b in bars]
    params = v4.AkMacdParams()
    st = v4.compute_state(bars, params)
    atr4 = v4.atr_series(h, l, c, params.atr_len)

    d_bars = fetch_klines("BTCUSDT", "1d", 3200)
    dc = [b["close"] for b in d_bars]; dh = [b["high"] for b in d_bars]
    dl = [b["low"] for b in d_bars]; dt = [b["time"] for b in d_bars]
    da, dma = atr14(dh, dl, dc), sma(dc, 100)
    day_idx = {t // 86400: i for i, t in enumerate(dt)}

    trades = []
    for (i, d) in v4.confirmed_entries(st, params):
        if d != "long":
            continue
        sw = params.swing_look
        try:
            br = v4.compute_bracket(entry_price=c[i], baseline_at_entry=st.baseline[i],
                                    recent_low=min(l[max(0, i - sw + 1): i + 1]),
                                    recent_high=max(h[max(0, i - sw + 1): i + 1]),
                                    direction=d, rr=v4.RR)
        except ValueError:
            continue
        entry = c[i] * (1 + v4.SLIP_PCT)
        rr_ = v4.sim_runner(d, entry, br.stop_loss_price, br.risk_distance, h, l, atr4, i + 1, v4.K)
        if rr_ is None:
            continue
        j = day_idx.get(ts[i] // 86400)
        sc = score_at(j, 1, dc, dma, da, dh, dl) if j and j > 101 else 1
        trades.append((ts[i], ts[i], rr_, "ak4h_btc", sc))
    return trades


def kelly_expanding(trades):
    """Quart-Kelly par moteur SANS lookahead : pour chaque trade, calculé sur les
    trades du même moteur SORTIS avant son entrée (min 15, sinon 2%). Cap 6%."""
    hist: dict[str, list[tuple[int, float]]] = {}
    for (t1, t2, r, n, _) in trades:
        hist.setdefault(n, []).append((t2, r))
    for n in hist:
        hist[n].sort()

    def risk(name, entry_ts):
        rs = [r for (t2, r) in hist.get(name, []) if t2 < entry_ts]
        if len(rs) < 15:
            return 0.02
        wins = [r for r in rs if r > 0]; losses = [r for r in rs if r <= 0]
        if not wins or not losses:
            return 0.02
        w = len(wins) / len(rs)
        ratio = (sum(wins) / len(wins)) / max(1e-9, -sum(losses) / len(losses))
        return max(0.005, min(0.06, (w - (1 - w) / ratio) / 4))
    return risk


def run_policy_v2(trades, label, risk_fn, skip0=False):
    pool = [t for t in trades if not (skip0 and t[4] == 0)]
    eq, peak, mdd = 1.0, 1.0, 0.0
    yearly = {}
    for (t1, t2, r, name, sc) in sorted(pool, key=lambda x: (x[1], x[0])):
        risk = risk_fn(name, sc, t1)
        prev = eq
        eq *= 1 + risk * r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
        y = datetime.fromtimestamp(t2, tz=timezone.utc).year
        yearly[y] = yearly.get(y, 1.0) * (eq / prev)
    span = (max(t2 for (_, t2, *_ ) in pool) - min(t1 for (t1, *_ ) in pool)) / 86400 / 365.25
    cagr = eq ** (1 / span) - 1
    worst_y, worst = min(yearly.items(), key=lambda kv: kv[1])
    print(f"  {label:26s}: x{eq:7.2f} | CAGR {cagr*100:+6.1f}% | maxDD {mdd*100:5.1f}% "
          f"| pire année {worst_y} {100*(worst-1):+.1f}% | {len(pool)} tr")


def main():
    btc = donchian_trades(fetch_klines("BTCUSDT", "1d", 3200), "donchian_btc", False)
    eth = donchian_trades(fetch_klines("ETHUSDT", "1d", 3200), "donchian_eth", False)
    jpy = donchian_trades(fetch_daily("JPY=X", "2000-01-01"), "donchian_jpy", True)
    gold = gold_cot_trades()
    ak4h = ak4h_trades()
    trades = btc + eth + jpy + gold + ak4h

    print("=" * 96)
    print("PORTEFEUILLE MULTI-MOTEURS v2 — trades datés, R vs risque 2*ATR14, frais 0.1% RT")
    print("=" * 96)
    for nm in ("donchian_btc", "donchian_eth", "donchian_jpy", "gold_cot", "ak4h_btc"):
        engine_stats(trades, nm)

    print("\n-- VALIDATION DU SCORE DE CONFIANCE (tous moteurs poolés) --")
    ok = True
    prev_exp = None
    for s in range(4):
        rs = [r for (_, _, r, _, sc) in trades if sc == s]
        if not rs:
            print(f"  score {s}: n=0")
            continue
        wins = [r for r in rs if r > 0]
        pf = sum(wins) / max(1e-9, -sum(r for r in rs if r <= 0))
        exp = sum(rs) / len(rs)
        print(f"  score {s}: n {len(rs):3d} | exp {exp:+.3f}R | PF {pf:4.2f} | win {100*len(wins)/len(rs):4.1f}%")
        if prev_exp is not None and exp < prev_exp - 0.02:
            ok = False
        prev_exp = exp
    print(f"  => monotone : {'OUI — levier dynamique AUTORISÉ' if ok else 'NON — levier dynamique ENTERRÉ (sizing plat)'}")

    # panier final : jpy exclu (PF<1, rejeté par son propre backtest)
    final = [tr for tr in trades if tr[3] != "donchian_jpy"]
    kx = kelly_expanding(final)
    print(f"\n-- POLITIQUES DE SIZING v2 (panier final SANS jpy, équité partagée) --")
    run_policy_v2(final, "plat 2%", lambda n, s, t: 0.02)
    run_policy_v2(final, "modulé 1/2/4%", lambda n, s, t: {0: 0.01, 1: 0.01, 2: 0.02, 3: 0.04}[s])
    run_policy_v2(final, "modulé + videur score-0", lambda n, s, t: {1: 0.01, 2: 0.02, 3: 0.04}[s], skip0=True)
    run_policy_v2(final, "Kelly expanding (honnête)", lambda n, s, t: kx(n, t))
    run_policy_v2(final, "Kelly expanding + videur", lambda n, s, t: kx(n, t), skip0=True)


if __name__ == "__main__":
    main()
