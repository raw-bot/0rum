"""C1 data snapshot per PREREG_C1.md (commit 849b06a)."""
import json, hashlib, time, datetime, pathlib, urllib.request

HERE = pathlib.Path(__file__).parent
D = HERE / "data"
UA = {"User-Agent": "Mozilla/5.0"}
manifest = []

def get(url, out, tries=3):
    for i in range(tries):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45).read()
            (D / out).write_bytes(raw)
            manifest.append((out, hashlib.sha256(raw).hexdigest(), url))
            return raw
        except Exception as e:
            if i == tries - 1:
                print(f"FAIL {url}: {e}", flush=True); return None
            time.sleep(2)

# 1) stablecoin list (classification snapshot)
raw = get("https://stablecoins.llama.fi/stablecoins?includePrices=false", "stablecoins_list.json")
assets = json.loads(raw)["peggedAssets"]
fb = [a for a in assets if a.get("pegMechanism") == "fiat-backed" and a.get("pegType") == "peggedUSD"]
print(f"fiat-backed peggedUSD assets: {len(fb)}", flush=True)

# 2) per-asset history
ok = 0
for a in fb:
    if get(f"https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={a['id']}", f"sc_{a['id']}.json"):
        ok += 1
    time.sleep(0.15)
print(f"histories: {ok}/{len(fb)}", flush=True)

# 3) CM prices (btc, eth)
for asset in ["btc", "eth"]:
    get(f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets={asset}&metrics=PriceUSD&frequency=1d&start_time=2015-01-01&page_size=10000", f"price_{asset}.json")

with open(HERE / "DATA_MANIFEST_C1.md", "w") as f:
    f.write(f"# C1 data snapshot — {datetime.datetime.now(datetime.UTC).isoformat()}\n\n| file | sha256 |\n|---|---|\n")
    for out, h, url in manifest:
        f.write(f"| {out} | {h} |\n")
print("MANIFEST WRITTEN", flush=True)
