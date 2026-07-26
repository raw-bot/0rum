"""R1 TOM study — analysis per PREREG.md (frozen 2026-07-26, commit 1b7cb46).
No spec deviation. Bootstrap seed fixed for reproducibility."""
import json, pathlib, datetime
import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).parent
DATA = HERE / "data"
RNG = np.random.default_rng(42)
N_BOOT = 10_000
PRIMARY_START, PRIMARY_END = "2009-01-01", "2026-06-30"
SUB1_END, SUB2_START = "2017-12-31", "2018-01-01"

def load_yahoo(fn, use_adj):
    d = json.loads((DATA / fn).read_bytes())["chart"]["result"][0]
    ts = d["timestamp"]
    ind = d["indicators"]
    px = (ind["adjclose"][0]["adjclose"] if use_adj else ind["quote"][0]["close"])
    df = pd.DataFrame({
        "date": [datetime.datetime.fromtimestamp(t, datetime.UTC).date() for t in ts],
        "px": px}).dropna()
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates("date").set_index("date").sort_index()
    df["ret"] = df["px"].pct_change()
    return df.dropna()

def load_fred(fn):
    df = pd.read_csv(DATA / fn, na_values=".")
    df.columns = ["date", "rate"]
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["rate"].dropna()

def tag_tom(df):
    """TOM day returns = returns of {last trading day of month, first 3 of next month}."""
    dates = df.index
    ym = dates.to_period("M")
    is_tom = np.zeros(len(df), bool)
    for m in ym.unique():
        idx = np.where(ym == m)[0]
        is_tom[idx[-1]] = True          # last trading day of month m
        is_tom[idx[:3]] = True          # first 3 trading days of month m
    df = df.copy()
    df["tom"] = is_tom
    return df

def tag_tom_variant(df):
    """Descriptive variant T-4..T+3: last 4 trading days + first 3."""
    dates = df.index
    ym = dates.to_period("M")
    is_v = np.zeros(len(df), bool)
    for m in ym.unique():
        idx = np.where(ym == m)[0]
        is_v[idx[-4:]] = True
        is_v[idx[:3]] = True
    return is_v

def diff_stat(df):
    return df.loc[df.tom, "ret"].mean() - df.loc[~df.tom, "ret"].mean()

def cluster_bootstrap(df, n=N_BOOT):
    months = df.index.to_period("M")
    uniq = months.unique()
    groups = {m: df[months == m] for m in uniq}
    stats = np.empty(n)
    for i in range(n):
        pick = RNG.choice(len(uniq), size=len(uniq), replace=True)
        parts = pd.concat([groups[uniq[j]] for j in pick])
        stats[i] = parts.loc[parts.tom, "ret"].mean() - parts.loc[~parts.tom, "ret"].mean()
    return np.percentile(stats, [2.5, 97.5]), (stats <= 0).mean()

def ann(r, n_per_year=252):
    g = (1 + r).prod() ** (n_per_year / len(r)) - 1
    vol = r.std() * np.sqrt(n_per_year)
    return g, vol

def maxdd(r):
    w = (1 + r).cumprod()
    return (w / w.cummax() - 1).min()

def strategy_metrics(df, cash_daily, label, results):
    mkt, tom = df["ret"], df["tom"]
    cash = cash_daily.reindex(df.index).ffill().fillna(0.0)
    strat = np.where(tom, mkt, cash)
    w = tom.mean()
    scaled = w * mkt + (1 - w) * cash
    sg, sv = ann(pd.Series(strat, index=df.index))
    bg, bv = ann(scaled)
    cg, _ = ann(cash)
    results[label] = {
        "exposure_share": round(float(w), 4),
        "strategy_ann_ret": round(float(sg), 4), "strategy_ann_vol": round(float(sv), 4),
        "strategy_sharpe": round(float((sg - cg) / sv), 3),
        "strategy_maxdd": round(float(maxdd(pd.Series(strat, index=df.index))), 4),
        "scaledBH_ann_ret": round(float(bg), 4), "scaledBH_ann_vol": round(float(bv), 4),
        "scaledBH_sharpe": round(float((bg - cg) / bv), 3),
        "scaledBH_maxdd": round(float(maxdd(scaled)), 4),
        "gross_advantage_ann": round(float(sg - bg), 4),
        "cash_ann": round(float(cg), 4),
    }
    return sg, bg

def random_block_benchmark(df, n=N_BOOT):
    """Per calendar month hold a random contiguous 4-trading-day block; mean held-day return."""
    months = df.index.to_period("M")
    uniq = months.unique()
    rets_by_m = [df.loc[months == m, "ret"].values for m in uniq]
    sims = np.empty(n)
    for i in range(n):
        acc, cnt = 0.0, 0
        for arr in rets_by_m:
            if len(arr) < 4:
                continue
            s = RNG.integers(0, len(arr) - 3)
            acc += arr[s:s+4].sum(); cnt += 4
        sims[i] = acc / cnt
    return sims

# ---------- load ----------
spx = tag_tom(load_yahoo("sp500tr.json", use_adj=False))
exw = tag_tom(load_yahoo("exw1.json", use_adj=True))
isf = tag_tom(load_yahoo("isf.json", use_adj=True))
tpx = tag_tom(load_yahoo("topix1306.json", use_adj=True))
sx5 = tag_tom(load_yahoo("stoxx50e.json", use_adj=False))

dtb3 = load_fred("dtb3.csv") / 100 / 360   # discount-basis daily accrual approx
eur3 = load_fred("eur3m.csv") / 100 / 360

P = lambda df: df.loc[PRIMARY_START:PRIMARY_END]
results = {"meta": {"prereg_commit": "1b7cb46", "n_boot": N_BOOT, "seed": 42,
                    "run": datetime.datetime.now(datetime.UTC).isoformat()}}

# 1. Primary test
p = P(spx)
d = diff_stat(p)
(ci_lo, ci_hi), p_le0 = cluster_bootstrap(p)
results["primary"] = {
    "instrument": "^SP500TR", "n_days": len(p), "n_months": len(p.index.to_period('M').unique()),
    "mean_tom_bp": round(p.loc[p.tom, 'ret'].mean() * 1e4, 2),
    "mean_nontom_bp": round(p.loc[~p.tom, 'ret'].mean() * 1e4, 2),
    "diff_bp": round(d * 1e4, 2), "ci95_bp": [round(ci_lo * 1e4, 2), round(ci_hi * 1e4, 2)],
    "boot_frac_diff_le_0": round(float(p_le0), 4),
}

# 2. Sub-periods (sign only)
for name, seg in [("sub_2009_2017", spx.loc[PRIMARY_START:SUB1_END]),
                  ("sub_2018_2026", spx.loc[SUB2_START:PRIMARY_END])]:
    results[name] = {"diff_bp": round(diff_stat(seg) * 1e4, 2), "sign_positive": bool(diff_stat(seg) > 0)}

# 3. Concentration: drop 4 largest-|window-contribution| months
tomdays = p[p.tom]
contrib = tomdays.groupby(tomdays.index.to_period("M"))["ret"].sum()
worst = contrib.abs().nlargest(4).index
keep = ~p.index.to_period("M").isin(worst)
d_lo = diff_stat(p[keep])
results["concentration"] = {"dropped_months": [str(m) for m in worst],
                            "diff_bp_after_drop": round(d_lo * 1e4, 2), "sign_positive": bool(d_lo > 0)}

# 4. Strategy vs scaled B&H (SP500TR/USD cash; EXW1/EUR cash)
sg_spx, bg_spx = strategy_metrics(p, dtb3, "strategy_SP500TR", results)
sg_exw, bg_exw = strategy_metrics(P(exw), eur3, "strategy_EXW1", results)

# 5. Random-block benchmark (SP500TR)
sims = random_block_benchmark(p)
tom_mean = p.loc[p.tom, "ret"].mean()
results["random_block"] = {"tom_mean_bp": round(tom_mean * 1e4, 2),
                           "sim_median_bp": round(float(np.median(sims)) * 1e4, 2),
                           "percentile_of_tom": round(float((sims < tom_mean).mean()) * 100, 1)}

# 6. Costs (per prereg: 24 exec/yr, max(1.25e,0.05%) + half-spread 1.5bp SP / 2.5bp EXW)
def cost_drag(account, half_spread_bp):
    per_exec = max(1.25, 0.0005 * account) + half_spread_bp / 1e4 * account
    return 24 * per_exec / account
for acct in (5_000, 20_000):
    drag_spx = cost_drag(acct, 1.5); drag_exw = cost_drag(acct, 2.5)
    results[f"net_at_{acct}"] = {
        "cost_drag_SP_ann": round(drag_spx, 4),
        "net_advantage_SP500TR_ann": round(float(sg_spx - bg_spx - drag_spx), 4),
        "cost_drag_EXW_ann": round(drag_exw, 4),
        "net_advantage_EXW1_ann": round(float(sg_exw - bg_exw - drag_exw), 4),
    }

# 7. Controls (sign only)
for label, df in [("control_ISF", isf), ("control_TOPIX1306", tpx)]:
    seg = P(df)
    results[label] = {"diff_bp": round(diff_stat(seg) * 1e4, 2), "sign_positive": bool(diff_stat(seg) > 0)}

# 8. Descriptive (cannot alter verdict)
pre = spx.loc["1988-02-01":"2008-12-31"]
v = p.copy(); v["tom"] = tag_tom_variant(spx)[spx.index.get_indexer(p.index)]
lastday = p[p.tom & (p.index.to_period("M") != p.index.to_period("M").shift(1))]  # unused; simpler below
tom_rets = p[p.tom]
first3 = tom_rets[tom_rets.groupby(tom_rets.index.to_period("M")).cumcount() > 0]
results["descriptive"] = {
    "sp500tr_1988_2008_diff_bp": round(diff_stat(pre) * 1e4, 2),
    "variant_Tm4_Tp3_diff_bp": round(diff_stat(v) * 1e4, 2),
    "stoxx50e_price_diff_bp": round(diff_stat(P(sx5)) * 1e4, 2),
    "tom_share_of_total_ret_descriptive": round(float(p.loc[p.tom, 'ret'].sum() / p['ret'].sum()), 3),
}

# Verdict per prereg
crit = {
    "i_diff_le_0": d <= 0,
    "ii_subperiod_sign_broken": not (results["sub_2009_2017"]["sign_positive"] and results["sub_2018_2026"]["sign_positive"]),
    "iii_net_adv_lt_1pct_at_20k": results["net_at_20000"]["net_advantage_SP500TR_ann"] < 0.01,
    "iv_sign_lost_after_drop4": not results["concentration"]["sign_positive"],
}
results["invalidation_criteria_triggered"] = {k: bool(v) for k, v in crit.items()}
results["VERDICT"] = "REJECT" if any(crit.values()) else "GO"

(HERE / "results.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
