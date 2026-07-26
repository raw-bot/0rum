"""C1 prospective signal logger — frozen params from PREREG_C1/results (commit 4973e65).
Runs weekly (Monday). Appends decision to prospective_log.csv and git-commits it.
No capital involved: pure prospective validation record."""
import json, csv, datetime, pathlib, subprocess, time, urllib.request
import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).parent
LOG = HERE / "prospective_log.csv"
ERRLOG = HERE / "logger.log"
UA = {"User-Agent": "Mozilla/5.0"}
Z_IN, Z_OUT = 0.25, -0.75          # frozen (dev selection, results_c1.json)
GROWTH_D, ZWIN, ZMIN, LAG = 30, 730, 365, 1

def log_err(msg):
    with open(ERRLOG, "a") as f:
        f.write(f"{datetime.datetime.now(datetime.UTC).isoformat()} {msg}\n")

def get(url, tries=3):
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45).read()
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(3)

def main():
    lst = json.loads(get("https://stablecoins.llama.fi/stablecoins?includePrices=false"))["peggedAssets"]
    fb_ids = [a["id"] for a in lst if a.get("pegMechanism") == "fiat-backed" and a.get("pegType") == "peggedUSD"]
    series = {}
    for i in fb_ids:
        try:
            d = json.loads(get(f"https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={i}"))
        except Exception as e:
            log_err(f"asset {i} fetch failed: {e}")
            continue
        if isinstance(d, list) and d:
            s = pd.Series({pd.Timestamp(int(r["date"]), unit="s", tz="UTC").normalize():
                           (r.get("totalCirculating") or {}).get("peggedUSD", 0.0) or 0.0 for r in d})
            series[i] = s[~s.index.duplicated()].sort_index()
        time.sleep(0.1)
    FB = pd.DataFrame(series).fillna(0.0).sum(axis=1)
    g = np.log(FB.shift(LAG)) - np.log(FB.shift(LAG + GROWTH_D))
    z = (g - g.rolling(ZWIN, min_periods=ZMIN).mean()) / g.rolling(ZWIN, min_periods=ZMIN).std()
    z_now, g_now = float(z.iloc[-1]), float(g.iloc[-1])

    prev_pos = "cash"
    if LOG.exists():
        rows = list(csv.DictReader(open(LOG)))
        if rows:
            prev_pos = rows[-1]["position"]
    pos = prev_pos
    if prev_pos == "cash" and z_now >= Z_IN:
        pos = "long"
    elif prev_pos == "long" and z_now <= Z_OUT:
        pos = "cash"

    new = not LOG.exists()
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["run_ts", "data_through", "n_assets", "fb_usd_bn", "g30", "z", "prev_position", "position", "changed"])
        w.writerow([datetime.datetime.now(datetime.UTC).isoformat(), str(FB.index[-1].date()), len(series),
                    round(FB.iloc[-1] / 1e9, 2), round(g_now, 5), round(z_now, 3), prev_pos, pos, pos != prev_pos])
    try:
        subprocess.run(["git", "-C", str(HERE.parent.parent), "add", str(LOG)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(HERE.parent.parent), "commit", "-m",
                        f"chore(c1-logger): weekly signal z={z_now:.2f} pos={pos}"], check=True, capture_output=True)
    except Exception as e:
        log_err(f"git commit failed: {e}")
    print(f"z={z_now:.3f} position={pos} (prev={prev_pos})")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_err(f"RUN FAILED: {e}")
        raise
