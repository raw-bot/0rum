"""Incremental Kraken holdout collector — preserves 5m OI + 1m candles for
PF_XBTUSD/PF_ETHUSD against the undocumented charts API disappearing.
Resumes from last collected chunk (re-fetches it to complete partial data).
Appends sha256 manifest (committed); raw data stays local."""
import csv, datetime, hashlib, pathlib, re, subprocess, time, urllib.request

HERE = pathlib.Path(__file__).parent
REPO = HERE.parent.parent
D = HERE / "data" / "kraken"
MANIFEST = HERE / "KRAKEN_COLLECT_MANIFEST.csv"
ERRLOG = HERE / "collector.log"
UA = {"User-Agent": "Mozilla/5.0"}
SYMS = ["PF_XBTUSD", "PF_ETHUSD"]

def log_err(msg):
    with open(ERRLOG, "a") as f:
        f.write(f"{datetime.datetime.now(datetime.UTC).isoformat()} {msg}\n")

def get(url, tries=3):
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3)

def main():
    rows = []
    today = datetime.datetime.now(datetime.UTC).date()
    for sym in SYMS:
        out = D / sym
        out.mkdir(parents=True, exist_ok=True)
        dates = sorted(datetime.date.fromisoformat(m.group(1))
                       for f in out.glob("oi-*.json")
                       if (m := re.match(r"oi-(\d{4}-\d{2}-\d{2})", f.name)))
        d = dates[-1] if dates else datetime.date(2024, 1, 1)  # re-fetch last chunk
        while d < today:
            d2 = min(d + datetime.timedelta(days=2), today)
            t0 = int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.UTC).timestamp())
            t1 = int(datetime.datetime(d2.year, d2.month, d2.day, tzinfo=datetime.UTC).timestamp())
            for kind, url in [
                ("oi", f"https://futures.kraken.com/api/charts/v1/analytics/{sym}/open-interest?since={t0}&to={t1}&interval=300"),
                ("px", f"https://futures.kraken.com/api/charts/v1/trade/{sym}/1m?from={t0}&to={t1}"),
            ]:
                try:
                    raw = get(url)
                except Exception as e:
                    log_err(f"{sym} {kind} {d}: {e}")
                    continue
                f = out / f"{kind}-{d}.json"
                f.write_bytes(raw)
                rows.append([datetime.datetime.now(datetime.UTC).isoformat(), sym, f.name,
                             hashlib.sha256(raw).hexdigest()])
                time.sleep(0.12)
            d = d2
    if rows:
        new = not MANIFEST.exists()
        with open(MANIFEST, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["collected_ts", "symbol", "file", "sha256"])
            w.writerows(rows)
        try:
            subprocess.run(["git", "-C", str(REPO), "add", str(MANIFEST)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(REPO), "commit", "-m",
                            f"chore(kraken-collector): +{len(rows)} files through {today}"],
                           check=True, capture_output=True)
        except Exception as e:
            log_err(f"git commit failed: {e}")
    print(f"collected {len(rows)} files through {today}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_err(f"RUN FAILED: {e}")
        raise
