"""C1 analysis per PREREG_C1.md (commit 849b06a). Single run, no spec deviation."""
import json, pathlib, datetime
import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).parent
D = HERE / "data"
RNG = np.random.default_rng(42)
OUT = {"meta": {"prereg_commit": "849b06a", "run": datetime.datetime.now(datetime.UTC).isoformat(), "seed": 42}}

DEV = ("2017-11-01", "2022-06-30")
CONF = ("2022-07-01", "2026-07-20")
SUB1_END, SUB2_START = "2024-06-30", "2024-07-01"
COST_PER_EXEC = 17e-4  # 16bp maker + 1bp half-spread

# ---- build FB aggregate and per-asset top contributors ----
lst = json.loads((D / "stablecoins_list.json").read_bytes())["peggedAssets"]
fb_ids = [a["id"] for a in lst if a.get("pegMechanism") == "fiat-backed" and a.get("pegType") == "peggedUSD"]
name_by_id = {a["id"]: a["name"] for a in lst}
series = {}
for i in fb_ids:
    try:
        d = json.loads((D / f"sc_{i}.json").read_bytes())
    except FileNotFoundError:
        continue
    if not isinstance(d, list) or not d:
        continue
    s = pd.Series({pd.Timestamp(int(r["date"]), unit="s", tz="UTC").normalize(): (r.get("totalCirculating") or {}).get("peggedUSD", 0.0) or 0.0 for r in d})
    series[i] = s[~s.index.duplicated()].sort_index()
panel = pd.DataFrame(series).fillna(0.0)
FB = panel.sum(axis=1)
peak_contrib = panel.max().sort_values(ascending=False)
usdt_id = [i for i in series if name_by_id.get(i) == "Tether"][0]
usdc_id = [i for i in series if name_by_id.get(i) == "USD Coin"][0]
OUT["data"] = {"n_assets": len(series), "fb_first": str(FB.index[0].date()), "fb_last": str(FB.index[-1].date()),
               "fb_now_bn": round(FB.iloc[-1] / 1e9, 1),
               "usdt_usdc_share_now": round(float((panel[usdt_id].iloc[-1] + panel[usdc_id].iloc[-1]) / FB.iloc[-1]), 3)}

def load_price(asset):
    d = json.loads((D / f"price_{asset}.json").read_bytes())["data"]
    s = pd.Series({pd.Timestamp(r["time"]).normalize(): float(r["PriceUSD"]) for r in d})
    return s.sort_index()

btc, eth = load_price("btc"), load_price("eth")
dtb3 = pd.read_csv(HERE.parent / "r1-tom" / "data" / "dtb3.csv", na_values=".")
dtb3.columns = ["date", "rate"]
dtb3["date"] = pd.to_datetime(dtb3["date"]).dt.tz_localize("UTC")
cash_daily = (dtb3.set_index("date")["rate"].dropna() / 100 / 360)

def make_signal(supply):
    g = np.log(supply.shift(1)) - np.log(supply.shift(31))
    mu = g.rolling(730, min_periods=365).mean()
    sd = g.rolling(730, min_periods=365).std()
    return (g - mu) / sd

S = make_signal(FB)
S_ex_usdt = make_signal(FB - panel[usdt_id])
S_ex_usdc = make_signal(FB - panel[usdc_id])

def zscore(x, w=730, mp=365):
    return (x - x.rolling(w, min_periods=mp).mean()) / x.rolling(w, min_periods=mp).std()

def run_strategy(price, sig, z_in, z_out, start, end):
    """Weekly (Monday) decisions, daily accrual, hysteresis, costs on switches."""
    idx = price.loc[start:end].index
    sig_a, cash_a = sig.reindex(idx).ffill(), cash_daily.reindex(idx).ffill().fillna(0.0)
    ret = price.pct_change().reindex(idx)
    pos, in_mkt, switches, decisions = [], False, 0, 0
    for t in idx:
        if t.dayofweek == 0:  # Monday decision
            decisions += 1
            z = sig_a.loc[t]
            if not np.isnan(z):
                if not in_mkt and z >= z_in: in_mkt = True; switches += 1
                elif in_mkt and z <= z_out: in_mkt = False; switches += 1
        pos.append(in_mkt)
    pos = pd.Series(pos, index=idx)
    strat = np.where(pos.shift(1).fillna(False), ret, cash_a)  # position effective next day
    sw = pos.astype(int).diff().abs().fillna(0)
    strat = strat - sw.values * COST_PER_EXEC
    s = pd.Series(strat, index=idx).dropna()
    years = len(s) / 365.25
    annret = (1 + s).prod() ** (1 / years) - 1 if years > 0 else np.nan
    return {"ann": float(annret), "exposure": float(pos.mean()), "switches": int(sw.sum()),
            "series": s, "pos": pos}

GRID = [(zi, zi - dz) for zi in (0.25, 0.5, 0.75, 1.0) for dz in (0.5, 1.0)]

def dev_select(price, sig):
    best = None
    for zi, zo in GRID:
        r = run_strategy(price, sig, zi, zo, *DEV)
        if best is None or r["ann"] > best[2]["ann"]:
            best = (zi, zo, r)
    return best

# ---- DEV: threshold selection for signal and null ----
mom30 = zscore(np.log(btc) - np.log(btc.shift(30)))
sel_S = dev_select(btc, S)
sel_M = dev_select(btc, mom30)
OUT["dev_selection"] = {"signal": {"z_in": sel_S[0], "z_out": sel_S[1], "dev_ann": round(sel_S[2]["ann"], 4), "exposure": round(sel_S[2]["exposure"], 3)},
                       "null_momentum": {"z_in": sel_M[0], "z_out": sel_M[1], "dev_ann": round(sel_M[2]["ann"], 4), "exposure": round(sel_M[2]["exposure"], 3)}}

# ---- DEV: incremental regression (criterion a) ----
wk = btc.resample("W-MON").last()
fwd = wk.pct_change().shift(-1)
mom90 = zscore(np.log(btc) - np.log(btc.shift(90)))
vol30 = zscore(btc.pct_change().rolling(30).std())
X = pd.DataFrame({"mom30": mom30, "mom90": mom90, "vol30": vol30, "S": S}).resample("W-MON").last()
df = pd.concat([X, fwd.rename("fwd")], axis=1).loc[DEV[0]:DEV[1]].dropna()
Xm = np.column_stack([np.ones(len(df)), df[["mom30", "mom90", "vol30", "S"]].values])
beta, *_ = np.linalg.lstsq(Xm, df["fwd"].values, rcond=None)
resid = df["fwd"].values - Xm @ beta
se = np.sqrt(np.diag(np.linalg.inv(Xm.T @ Xm)) * resid.var(ddof=5))
OUT["dev_regression"] = {"coef_S_weekly": round(float(beta[4]), 5), "t_naive": round(float(beta[4] / se[4]), 2),
                        "n_weeks": len(df), "note": "t naive (overlap-free weekly), decision par signe du coef"}
crit_a = beta[4] <= 0

results = {}
if not crit_a:
    # ---- CONFIRMATION ----
    stratS = run_strategy(btc, S, sel_S[0], sel_S[1], *CONF)
    stratM = run_strategy(btc, mom30, sel_M[0], sel_M[1], *CONF)
    adv = stratS["ann"] - stratM["ann"]
    results["confirmation"] = {"signal_ann": round(stratS["ann"], 4), "null_ann": round(stratM["ann"], 4),
                               "advantage_vs_null_ann": round(adv, 4),
                               "signal_exposure": round(stratS["exposure"], 3), "null_exposure": round(stratM["exposure"], 3),
                               "signal_switches": stratS["switches"], "null_switches": stratM["switches"]}
    # scaled B&H
    idx = btc.loc[CONF[0]:CONF[1]].index
    ret = btc.pct_change().reindex(idx); cash = cash_daily.reindex(idx).ffill().fillna(0)
    w = stratS["exposure"]
    bh = (w * ret + (1 - w) * cash).dropna()
    results["scaledBH_ann"] = round(float((1 + bh).prod() ** (365.25 / len(bh)) - 1), 4)
    # sub-periods (advantage vs null within each)
    subs = {}
    for nm, lo, hi in [("sub1", CONF[0], SUB1_END), ("sub2", SUB2_START, CONF[1])]:
        a = stratS["series"].loc[lo:hi]; b = stratM["series"].loc[lo:hi]
        ann_a = (1 + a).prod() ** (365.25 / len(a)) - 1
        ann_b = (1 + b).prod() ** (365.25 / len(b)) - 1
        subs[nm] = round(float(ann_a - ann_b), 4)
    results["subperiod_adv"] = subs
    # placebo
    posc = stratS["pos"]
    blocks = (posc.astype(int).diff() == 1).sum() + (1 if posc.iloc[0] else 0)
    days_in = int(posc.sum()); n = len(posc)
    sims = np.empty(2000)
    retv = ret.fillna(0).values; cashv = cash.values
    for k in range(2000):
        p = np.zeros(n, bool)
        lens = np.diff(np.sort(RNG.choice(np.arange(1, days_in), size=max(blocks - 1, 0), replace=False))) if blocks > 1 else []
        lens = list(lens) + [days_in - sum(lens)] if blocks > 1 else [days_in]
        starts = np.sort(RNG.choice(n - days_in, size=blocks, replace=False)) if blocks else []
        pos0 = 0
        for L, st in zip(lens, starts):
            a0 = min(n - 1, st + pos0); p[a0:a0 + L] = True
        sr = np.where(np.roll(p, 1), retv, cashv)
        sw2 = np.abs(np.diff(p.astype(int))).sum() + (1 if p[0] else 0)
        sims[k] = (1 + pd.Series(sr)).prod() ** (365.25 / n) - 1 - sw2 * COST_PER_EXEC / (n / 365.25)
    results["placebo_percentile"] = round(float((sims < stratS["ann"]).mean()) * 100, 1)
    # leave-out 3 biggest |S| episodes in confirmation
    Sc = S.loc[CONF[0]:CONF[1]].dropna()
    peaks, used = [], pd.Series(False, index=Sc.index)
    for t in Sc.abs().sort_values(ascending=False).index:
        if not used.loc[max(t - pd.Timedelta(days=15), Sc.index[0]):min(t + pd.Timedelta(days=15), Sc.index[-1])].any():
            peaks.append(t); used.loc[t - pd.Timedelta(days=15):t + pd.Timedelta(days=15)] = True
        if len(peaks) == 3: break
    mask = pd.Series(True, index=stratS["series"].index)
    for t in peaks:
        mask.loc[t - pd.Timedelta(days=15):t + pd.Timedelta(days=15)] = False
    a = stratS["series"][mask]; b = stratM["series"].reindex(a.index).fillna(0)
    lo_adv = (1 + a).prod() ** (365.25 / len(a)) - 1 - ((1 + b).prod() ** (365.25 / len(b)) - 1)
    results["leaveout"] = {"peaks": [str(t.date()) for t in peaks], "advantage_after": round(float(lo_adv), 4)}
    # ex-USDT / ex-USDC
    for nm, sig in [("ex_usdt", S_ex_usdt), ("ex_usdc", S_ex_usdc)]:
        r = run_strategy(btc, sig, sel_S[0], sel_S[1], *CONF)
        results[nm] = {"ann": round(r["ann"], 4), "adv_vs_null": round(r["ann"] - stratM["ann"], 4)}
    # ETH sign control
    momE = zscore(np.log(eth) - np.log(eth.shift(30)))
    selME = dev_select(eth, momE)
    rE = run_strategy(eth, S, sel_S[0], sel_S[1], *CONF)
    rEM = run_strategy(eth, momE, selME[0], selME[1], *CONF)
    results["eth_control_adv"] = round(rE["ann"] - rEM["ann"], 4)
    # switch audit: jumps >10%/day in USDT/USDC supply near switches
    switch_dates = stratS["pos"].astype(int).diff().abs()
    switch_dates = switch_dates[switch_dates > 0].index
    jumps = []
    for sd in switch_dates:
        for i, nm in [(usdt_id, "USDT"), (usdc_id, "USDC")]:
            w30 = panel[i].loc[sd - pd.Timedelta(days=35):sd]
            j = w30.pct_change().abs()
            if (j > 0.10).any():
                jumps.append({"switch": str(sd.date()), "asset": nm, "max_jump": round(float(j.max()), 3)})
    results["switch_audit"] = {"n_switches": len(switch_dates), "anomalies": jumps}

# ---- verdict ----
crit = {"a_coef_S_not_positive": bool(crit_a)}
if not crit_a:
    crit["b_adv_lt_1pct"] = results["confirmation"]["advantage_vs_null_ann"] < 0.01
    crit["c_subperiod_fail"] = (min(results["subperiod_adv"].values()) < 0)
    crit["d_leaveout_fail"] = results["leaveout"]["advantage_after"] <= 0
    crit["e_concentration_fail"] = (results["ex_usdt"]["adv_vs_null"] <= 0) or (results["ex_usdc"]["adv_vs_null"] <= 0)
    crit["h_audit_fail"] = len({j["switch"] for j in results["switch_audit"]["anomalies"]}) > 0.25 * max(results["switch_audit"]["n_switches"], 1)
    crit["i_placebo_and_small"] = (results["placebo_percentile"] < 60) and (results["confirmation"]["advantage_vs_null_ann"] < 0.01)
OUT["results"] = results
OUT["criteria_triggered"] = crit
OUT["VERDICT"] = "REJECT" if any(crit.values()) else "GO"
(HERE / "results_c1.json").write_text(json.dumps(OUT, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
print(json.dumps({k: OUT[k] for k in ["data", "dev_selection", "dev_regression", "results", "criteria_triggered", "VERDICT"]}, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
