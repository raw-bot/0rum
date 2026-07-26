"""C1 prospective signal logger v2 — per PHASE4_VERDICT.md protocol.
Frozen params (PREREG_C1, commit 849b06a): z_in=0.25, z_out=-0.75, g30, z730.
Weekly (Monday). Appends to prospective_log.csv, backfills prior week's realized
return, git-commits. Observation only — no capital, no privileged status."""
import json, csv, datetime, hashlib, pathlib, subprocess, time, urllib.request
import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).parent
REPO = HERE.parent.parent
LOG = HERE / "prospective_log.csv"
ERRLOG = HERE / "logger.log"
UA = {"User-Agent": "Mozilla/5.0"}
Z_IN, Z_OUT = 0.25, -0.75
GROWTH_D, ZWIN, ZMIN, LAG = 30, 730, 365, 1
HEADER = ["run_ts", "code_commit", "data_sha256", "data_through", "n_assets", "fb_usd_bn",
          "g30", "z", "btc_price_kraken", "prev_position", "position", "changed", "fwd_ret_1w", "errors"]

def log_err(msg):
    with open(ERRLOG, "a") as f:
        f.write(f"{datetime.datetime.now(datetime.UTC).isoformat()} {msg}\n")

def get(url, tries=3):
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45).read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3)

def main():
    errors = []
    lst = json.loads(get("https://stablecoins.llama.fi/stablecoins?includePrices=false"))["peggedAssets"]
    fb_ids = [a["id"] for a in lst if a.get("pegMechanism") == "fiat-backed" and a.get("pegType") == "peggedUSD"]
    series = {}
    for i in fb_ids:
        try:
            d = json.loads(get(f"https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={i}"))
        except Exception as e:
            errors.append(f"asset{i}"); log_err(f"asset {i}: {e}")
            continue
        if isinstance(d, list) and d:
            s = pd.Series({pd.Timestamp(int(r["date"]), unit="s", tz="UTC").normalize():
                           (r.get("totalCirculating") or {}).get("peggedUSD", 0.0) or 0.0 for r in d})
            series[i] = s[~s.index.duplicated()].sort_index()
        time.sleep(0.1)
    FB = pd.DataFrame(series).fillna(0.0).sum(axis=1)
    data_hash = hashlib.sha256(FB.to_csv().encode()).hexdigest()[:16]
    g = np.log(FB.shift(LAG)) - np.log(FB.shift(LAG + GROWTH_D))
    z = (g - g.rolling(ZWIN, min_periods=ZMIN).mean()) / g.rolling(ZWIN, min_periods=ZMIN).std()
    z_now, g_now = float(z.iloc[-1]), float(g.iloc[-1])

    try:
        tick = json.loads(get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD"))["result"]
        btc_px = float(list(tick.values())[0]["c"][0])
    except Exception as e:
        btc_px = float("nan"); errors.append("kraken_px"); log_err(f"kraken px: {e}")

    try:
        commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = "unknown"

    rows = []
    if LOG.exists():
        rows = list(csv.DictReader(open(LOG)))
    prev_pos = rows[-1]["position"] if rows else "cash"
    # backfill realized 1w return of previous row
    if rows and rows[-1].get("fwd_ret_1w", "") == "" and rows[-1].get("btc_price_kraken", "") not in ("", "nan") and not np.isnan(btc_px):
        rows[-1]["fwd_ret_1w"] = str(round(btc_px / float(rows[-1]["btc_price_kraken"]) - 1, 5))

    pos = prev_pos
    if prev_pos == "cash" and z_now >= Z_IN:
        pos = "long"
    elif prev_pos == "long" and z_now <= Z_OUT:
        pos = "cash"
    rows.append({"run_ts": datetime.datetime.now(datetime.UTC).isoformat(), "code_commit": commit,
                 "data_sha256": data_hash, "data_through": str(FB.index[-1].date()), "n_assets": len(series),
                 "fb_usd_bn": round(FB.iloc[-1] / 1e9, 2), "g30": round(g_now, 5), "z": round(z_now, 3),
                 "btc_price_kraken": btc_px, "prev_position": prev_pos, "position": pos,
                 "changed": pos != prev_pos, "fwd_ret_1w": "", "errors": ";".join(errors)})
    with open(LOG, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in HEADER})
    try:
        subprocess.run(["git", "-C", str(REPO), "add", str(LOG)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(REPO), "commit", "-m",
                        f"chore(c1-logger): weekly z={z_now:.2f} pos={pos}"], check=True, capture_output=True)
    except Exception as e:
        log_err(f"git commit failed: {e}")
    print(f"z={z_now:.3f} position={pos} (prev={prev_pos}) btc={btc_px}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_err(f"RUN FAILED: {e}")
        raise
