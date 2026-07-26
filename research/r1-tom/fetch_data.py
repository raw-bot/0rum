"""R1 TOM study - data fetch. Sources and checksums recorded in DATA_MANIFEST.md."""
import json, hashlib, datetime, time, urllib.request, pathlib

DATA = pathlib.Path(__file__).parent / "data"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
manifest = []
NOW = int(time.time())

def fetch(url, out, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            raw = urllib.request.urlopen(req, timeout=60).read()
            (DATA / out).write_bytes(raw)
            manifest.append((out, url, hashlib.sha256(raw).hexdigest()))
            return raw
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"  retry {out}: {e}")
            time.sleep(3)

def yahoo(symbol, out):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1=0&period2={NOW}&interval=1d&events=div%2Csplit")
    raw = fetch(url, out)
    d = json.loads(raw)["chart"]["result"][0]
    ts = d["timestamp"]
    print(f"{out}: {len(ts)} rows, "
          f"{datetime.datetime.fromtimestamp(ts[0], datetime.UTC).date()} -> "
          f"{datetime.datetime.fromtimestamp(ts[-1], datetime.UTC).date()}")
    assert len(ts) > 2000, f"{out}: suspiciously few rows -> not daily granularity"

yahoo("%5ESP500TR", "sp500tr.json")
yahoo("EXW1.DE", "exw1.json")
yahoo("ISF.L", "isf.json")
yahoo("1306.T", "topix1306.json")
yahoo("%5ESTOXX50E", "stoxx50e.json")
for series, out in [("DTB3", "dtb3.csv"), ("IR3TIB01EZM156N", "eur3m.csv")]:
    raw = fetch(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", out)
    lines = raw.decode().strip().split("\n")
    print(f"{out}: {len(lines)-1} rows, first={lines[1].split(',')[0]}, last={lines[-1].split(',')[0]}")

with open(DATA.parent / "DATA_MANIFEST.md", "w") as f:
    f.write(f"# Data manifest — downloaded {datetime.datetime.now(datetime.UTC).isoformat()}\n\n"
            "| file | sha256 | url |\n|---|---|---|\n")
    for out, url, h in manifest:
        f.write(f"| {out} | {h} | {url} |\n")
print("manifest written")
