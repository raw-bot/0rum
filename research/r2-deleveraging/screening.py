"""R2 dev screening — implements DEV_PLAN.md (frozen, commit 28f0b04) exactly.
Dev data only: Binance 2020-09..2023-12. Kraken data is NEVER read here."""
import json, pathlib, zipfile, io, datetime, itertools, sys
import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).parent
D = HERE / "data"
OUT = {}
DEV_START, DEV_END = "2020-09-01", "2023-12-31"

RET_WINDOWS = [15, 30, 60]
RET_FIXED = [-0.02, -0.03, -0.04]
RET_PCTS = [0.005, 0.01]
VOL_KS = [3, 5, 10]
OI_WINDOWS = [30, 60]           # minutes
OI_QS = [0.01, 0.025, 0.05]
HORIZON = 120                   # minutes, primary (frozen)
DESC_HORIZONS = [30, 240]
COOLDOWN_MIN = 12 * 60
TAKER_BP = 5.0
DELTA_MIN_BP = 10.0
MIN_EPISODES = 25

def log(*a): print(*a, flush=True)

def load_klines(sym):
    rows = []
    for f in sorted((D / "binance_klines" / sym).glob("*.zip")):
        with zipfile.ZipFile(f) as z:
            name = z.namelist()[0]
            raw = z.read(name)
        first = raw.split(b"\n", 1)[0]
        skip = 1 if first.startswith(b"open_time") else 0
        df = pd.read_csv(io.BytesIO(raw), header=None, skiprows=skip, usecols=[0,1,2,3,4,5,9],
                         names=["ts","open","high","low","close","volume","taker_buy"])
        rows.append(df)
    df = pd.concat(rows, ignore_index=True)
    # ms vs us timestamps
    df["ts"] = pd.to_datetime(np.where(df.ts > 1e14, df.ts // 1000, df.ts), unit="ms", utc=True)
    df = df.drop_duplicates("ts").set_index("ts").sort_index().loc[DEV_START:DEV_END]
    return df

def load_metrics(sym):
    rows = []
    for f in sorted((D / "binance_metrics" / sym).glob("*.zip")):
        with zipfile.ZipFile(f) as z:
            raw = z.read(z.namelist()[0])
        df = pd.read_csv(io.BytesIO(raw), usecols=["create_time", "sum_open_interest"])
        rows.append(df)
    df = pd.concat(rows, ignore_index=True)
    df["ts"] = pd.to_datetime(df.create_time, utc=True)
    return df.drop_duplicates("ts").set_index("ts").sort_index()["sum_open_interest"].loc[DEV_START:DEV_END]

def load_funding(sym):
    rows = []
    for f in sorted((D / "binance_funding" / sym).glob("*.zip")):
        with zipfile.ZipFile(f) as z:
            raw = z.read(z.namelist()[0])
        first = raw.split(b"\n", 1)[0]
        skip = 1 if b"calc_time" in first or b"symbol" in first else 0
        df = pd.read_csv(io.BytesIO(raw), header=None, skiprows=skip, names=["calc_time","interval","rate"])
        rows.append(df)
    df = pd.concat(rows, ignore_index=True)
    df["ts"] = pd.to_datetime(np.where(df.calc_time > 1e14, df.calc_time // 1000, df.calc_time), unit="ms", utc=True)
    s = df.drop_duplicates("ts").set_index("ts").sort_index()["rate"].astype(float)
    z = (s - s.rolling(270, min_periods=90).mean()) / s.rolling(270, min_periods=90).std()  # 270 x 8h = 90d
    return z

def day_quantile_thresholds(series_1m, day_index, q, lookback_days=90):
    """For each calendar day, q-quantile of series over trailing lookback_days (excl. current day)."""
    days = pd.Index(sorted(set(day_index)))
    vals_by_day = {d: series_1m[day_index == d].dropna().values for d in days}
    out, buf = {}, []
    for i, d in enumerate(days):
        window = [vals_by_day[dd] for dd in days[max(0, i - lookback_days):i]]
        arr = np.concatenate(window) if window else np.array([])
        out[d] = np.quantile(arr, q) if arr.size else np.nan
    return out

def analyse_symbol(sym):
    log(f"--- loading {sym}")
    k = load_klines(sym)
    oi = load_metrics(sym)
    fz = load_funding(sym)
    log(f"{sym}: klines {len(k)} rows {k.index[0]}..{k.index[-1]}, OI {len(oi)} rows, funding z {len(fz)}")

    close = k["close"]
    day = k.index.floor("D")
    rets = {w: close / close.shift(w) - 1 for w in RET_WINDOWS}
    r1 = close.pct_change()

    vol5 = k["volume"].rolling(5).sum()
    day_med = vol5.groupby(day).median()
    med90 = day_med.rolling(90, min_periods=30).median().shift(1)  # daily-median approx of 90d median
    med_map = med90.reindex(day.unique()).ffill()
    vol_ref = pd.Series(med_map.reindex(day).values, index=k.index)

    # OI on 1m grid, lagged one full 5-min publication window
    oi_lag = oi.shift(1)
    doi = {w: (oi_lag / oi_lag.shift(w // 5) - 1).reindex(k.index, method="ffill") for w in OI_WINDOWS}

    # day-level trailing quantile thresholds
    ret_thr_pct = {(w, q): day_quantile_thresholds(rets[w], day, q) for w in RET_WINDOWS for q in RET_PCTS}
    doi_thr = {(w, q): day_quantile_thresholds(doi[w], day, q) for w in OI_WINDOWS for q in OI_QS}
    log(f"{sym}: thresholds computed")

    # component masks
    ret_masks = {}
    for w in RET_WINDOWS:
        for t in RET_FIXED:
            ret_masks[(w, f"fix{t}")] = (rets[w] < t)
        for q in RET_PCTS:
            thr = pd.Series([ret_thr_pct[(w, q)].get(d, np.nan) for d in day], index=k.index)
            ret_masks[(w, f"p{q}")] = (rets[w] < thr)
    vol_masks = {kk: (vol5 > kk * vol_ref) for kk in VOL_KS}
    oi_masks = {}
    for w in OI_WINDOWS:
        for q in OI_QS:
            thr = pd.Series([doi_thr[(w, q)].get(d, np.nan) for d in day], index=k.index)
            oi_masks[(w, q)] = (doi[w] < thr)

    sigma1 = r1.rolling(5).std()
    half_spread_bp = (1 + 2 * (sigma1 * 1e4)).clip(1, 20)  # bp, vol-dependent, capped

    pos_min = r1 > 0
    n = len(k)
    close_v, high_v = close.values, k["high"].values
    pos_v = pos_min.values
    vol1h = r1.rolling(60).std()
    day_v1h = vol1h.groupby(day).median()
    v1h_med90 = day_v1h.rolling(90, min_periods=30).median().shift(1)
    v1h_ref = pd.Series(v1h_med90.reindex(day).values, index=k.index).values
    vol1h_v = vol1h.values
    hs_v = half_spread_bp.values
    fz_1m = fz.reindex(k.index, method="ffill").values

    idx_ts = k.index

    def extract(trigger_mask):
        trig = np.flatnonzero(trigger_mask.values & ~np.isnan(close_v))
        episodes, last_entry = [], -10**9
        vol_normalized_since = True
        for i in trig:
            if i - last_entry < COOLDOWN_MIN:
                # exception: vol 1h dropped below its 90d median since last entry
                if not (vol1h_v[i] < v1h_ref[i] if not np.isnan(v1h_ref[i]) else False):
                    continue
            j = i + 1
            while j < min(i + 61, n) and not pos_v[j]:
                j += 1
            if j >= min(i + 61, n) or j + HORIZON >= n:
                continue
            entry_px = high_v[j]  # worst price of entry bar (buy)
            cost_bp = 2 * TAKER_BP + 2 * hs_v[j]
            r2h = (close_v[j + HORIZON] / entry_px - 1) * 1e4 - cost_bp
            ep = {"i": int(j), "ts": str(idx_ts[j]), "net2h_bp": float(r2h),
                  "crash": float(close_v[i] / close_v[max(0, i - 60)] - 1),
                  "vol": float(vol1h_v[i]) if not np.isnan(vol1h_v[i]) else 0.0,
                  "volu": float((vol5 / vol_ref).values[i]) if not np.isnan((vol5 / vol_ref).values[i]) else 0.0,
                  "fz": float(fz_1m[i]) if not np.isnan(fz_1m[i]) else 0.0}
            for h in DESC_HORIZONS:
                if j + h < n:
                    ep[f"net{h}_bp"] = float((close_v[j + h] / entry_px - 1) * 1e4 - cost_bp)
            episodes.append(ep)
            last_entry = j
        return episodes

    return {"k": k, "extract": extract, "ret_masks": ret_masks, "vol_masks": vol_masks, "oi_masks": oi_masks}

def cluster_episodes(eps_btc, eps_eth):
    """Merge cross-symbol episodes <2h apart into clusters; return clustered episode list (mean of members)."""
    allep = [dict(e, sym="BTC") for e in eps_btc] + [dict(e, sym="ETH") for e in eps_eth]
    allep.sort(key=lambda e: e["ts"])
    clusters, cur = [], []
    for e in allep:
        t = pd.Timestamp(e["ts"])
        if cur and (t - pd.Timestamp(cur[-1]["ts"])).total_seconds() <= 7200:
            cur.append(e)
        else:
            if cur: clusters.append(cur)
            cur = [e]
    if cur: clusters.append(cur)
    out = []
    for c in clusters:
        m = {kk: float(np.mean([e[kk] for e in c if kk in e])) for kk in
             ["net2h_bp", "crash", "vol", "volu", "fz"] if any(kk in e for e in c)}
        m["ts"] = c[0]["ts"]; m["n_members"] = len(c)
        out.append(m)
    return out

def buckets(eps):
    if not eps: return []
    crash = np.array([e["crash"] for e in eps]); vol = np.array([e["vol"] for e in eps]); vu = np.array([e["volu"] for e in eps])
    def dec(x):
        q = np.quantile(x, np.linspace(0, 1, 6)[1:-1])
        return np.searchsorted(q, x)
    cd, vd, ud = dec(crash), dec(vol), dec(vu)
    fzb = np.select([np.array([e["fz"] for e in eps]) < -1, np.array([e["fz"] for e in eps]) > 1], [0, 2], 1)
    return list(zip(cd, vd, ud, fzb))

def matched_delta(with_oi, without_oi):
    """Delta = mean(with) - mean(matched from without-only pool), exact bucket match."""
    bw = buckets(with_oi); bo = buckets(without_oi)
    pool = {}
    for e, b in zip(without_oi, bo): pool.setdefault(b, []).append(e["net2h_bp"])
    matched, unmatched = [], 0
    for e, b in zip(with_oi, bw):
        if b in pool and pool[b]:
            matched.append((e["net2h_bp"], float(np.mean(pool[b]))))
        else:
            unmatched += 1
    if not matched: return None, unmatched
    d = np.array(matched)
    return float((d[:, 0] - d[:, 1]).mean()), unmatched

def main():
    S = {sym: analyse_symbol(sym) for sym in ["BTCUSDT", "ETHUSDT"]}
    grid_results = []
    ret_keys = list(S["BTCUSDT"]["ret_masks"].keys())
    log(f"grid: {len(ret_keys)} ret x {len(VOL_KS)} vol x {len(OI_WINDOWS)*len(OI_QS)} oi = {len(ret_keys)*len(VOL_KS)*len(OI_WINDOWS)*len(OI_QS)} cells")
    for rk, vk, (ow, oq) in itertools.product(ret_keys, VOL_KS, itertools.product(OI_WINDOWS, OI_QS)):
        eps_w, eps_wo = {}, {}
        for sym in S:
            s = S[sym]
            base = s["ret_masks"][rk] & s["vol_masks"][vk]
            with_m = base & s["oi_masks"][(ow, oq)]
            without_m = base & ~s["oi_masks"][(ow, oq)]
            eps_w[sym] = s["extract"](with_m)
            eps_wo[sym] = s["extract"](without_m)
        cw = cluster_episodes(eps_w["BTCUSDT"], eps_w["ETHUSDT"])
        cwo = cluster_episodes(eps_wo["BTCUSDT"], eps_wo["ETHUSDT"])
        if len(cw) < 5: continue
        netw = np.array([e["net2h_bp"] for e in cw])
        delta, unmatched = matched_delta(cw, cwo)
        # stability: delta sign in 2020-21 vs 2022-23
        def sub(eps, lo, hi): return [e for e in eps if lo <= e["ts"][:4] <= hi]
        d1, _ = matched_delta(sub(cw, "2020", "2021"), sub(cwo, "2020", "2021"))
        d2, _ = matched_delta(sub(cw, "2022", "2023"), sub(cwo, "2022", "2023"))
        top3 = np.sort(netw)[-3:].sum()
        grid_results.append({
            "cell": f"ret={rk} vol={vk}x oi={ow}m@q{oq}",
            "n_clusters": len(cw), "mean_net2h_bp": round(float(netw.mean()), 1),
            "delta_bp": round(delta, 1) if delta is not None else None,
            "unmatched": unmatched,
            "delta_2020_21": round(d1, 1) if d1 is not None else None,
            "delta_2022_23": round(d2, 1) if d2 is not None else None,
            "top3_share_of_pnl": round(float(top3 / netw.sum()), 2) if netw.sum() != 0 else None,
        })
    grid_results.sort(key=lambda g: (g["delta_bp"] is not None, g["delta_bp"] or -1e9), reverse=True)
    OUT["grid_top20"] = grid_results[:20]
    OUT["n_cells_evaluated"] = len(grid_results)

    # Abandonment criteria on best cell (frozen DEV_PLAN.md)
    verdict = "NO_VALID_CELL"
    if grid_results:
        best = grid_results[0]
        OUT["best_cell"] = best
        crit = {
            "1_n_lt_25": best["n_clusters"] < MIN_EPISODES,
            "2_mean_net_le_0": best["mean_net2h_bp"] <= 0,
            "3_delta_lt_10bp": (best["delta_bp"] is None) or (best["delta_bp"] < DELTA_MIN_BP),
            "4_concentrated": (best["top3_share_of_pnl"] or 0) > 0.5 and best["mean_net2h_bp"] > 0,
            "5_delta_sign_unstable": (best["delta_2020_21"] is None or best["delta_2022_23"] is None
                                      or np.sign(best["delta_2020_21"]) != np.sign(best["delta_2022_23"])),
        }
        OUT["abandon_criteria"] = crit
        verdict = "ABANDON" if any(crit.values()) else "PASS_TO_CONFIRMATION"
    OUT["VERDICT_DEV"] = verdict
    OUT["meta"] = {"plan_commit": "28f0b04", "run": datetime.datetime.now(datetime.UTC).isoformat()}
    (HERE / "screening_results.json").write_text(json.dumps(OUT, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    log(json.dumps({kk: OUT[kk] for kk in ["VERDICT_DEV", "best_cell", "abandon_criteria"] if kk in OUT}, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    log(f"cells evaluated: {OUT['n_cells_evaluated']}")

if __name__ == "__main__":
    main()
