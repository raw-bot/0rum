"""Dev-phase options harvest per PREREG_A (00e6b17): per-symbol option-print
exports for 2014-01-01..2020-01-15 (dev + margin). Quota-aware (5 exports/h,
weekly/monthly byte caps), resumable, runs for days. Confirmation period is
NOT harvested until dev passes."""
import json, pathlib, time, datetime, urllib.request

HERE = pathlib.Path(__file__).parent
D = HERE / "data" / "options_dev"
D.mkdir(parents=True, exist_ok=True)
KEY = pathlib.Path.home().joinpath(".config/0rum/lse.key").read_text().strip()
BASE = "https://api.londonstrategicedge.com/vault"
START, END = "2014-01-01", "2020-01-15"
WEEK_CAP = 15.5e9   # stay under 16GB
MONTH_CAP = 48e9    # stay under 50GB

def call(path, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers={
        "x-api-key": KEY, "User-Agent": "0rum-harvest",
        **({"Content-Type": "application/json"} if data else {})})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def log(*a):
    print(datetime.datetime.now(datetime.UTC).isoformat(), *a, flush=True)

universe = [r["symbol"] for r in json.loads((HERE / "universe_top300.json").read_bytes())]
etfs = {"ARKK","EEM","GDX","HYG","IWM","SMH","SOXL","SOXS","SQQQ","TLT","TQQQ","XLE","XLF"}
symbols = [s for s in universe if s not in etfs]
log(f"harvest universe: {len(symbols)} symbols, {START}..{END}")

done = {f.stem for f in D.glob("*.parquet")}
pending = [s for s in symbols if s.replace("/", "_") not in done]
log(f"already done: {len(done)}, pending: {len(pending)}")

for sym in pending:
    # quota check
    while True:
        try:
            u = call("/usage")
        except Exception as e:
            log("usage check failed, wait 5min:", e); time.sleep(300); continue
        if u["bytes_used_week"] > WEEK_CAP:
            log(f"weekly cap reached ({u['bytes_used_week']/1e9:.1f}GB) — sleeping 6h"); time.sleep(21600); continue
        if u["bytes_used_month"] > MONTH_CAP:
            log(f"monthly cap reached — sleeping 12h"); time.sleep(43200); continue
        if u["exports_this_hour"] >= u["exports_cap_hour"]:
            time.sleep(600); continue
        break
    try:
        job = call("/export", {"dataset": "options", "symbol": sym, "timeframe": "tick",
                               "start": START, "end": END, "format": "parquet"})
        jid = job["job_id"]
        for _ in range(600):
            info = call(f"/export/{jid}")
            if info.get("status") == "ready":
                break
            if info.get("status") in ("failed", "expired"):
                log(f"{sym}: export {info.get('status')} — skip"); info = None; break
            time.sleep(5)
        if not info or info.get("status") != "ready":
            continue
        size = int(info.get("bytes") or 0)
        req = urllib.request.Request(f"{BASE}/export/{jid}/download",
                                     headers={"x-api-key": KEY, "User-Agent": "0rum-harvest"})
        raw = urllib.request.urlopen(req, timeout=1800).read()
        if len(raw) != size:
            log(f"{sym}: incomplete download {len(raw)}/{size} — will retry next run"); continue
        (D / f"{sym.replace('/', '_')}.parquet").write_bytes(raw)
        with open(HERE / "HARVEST_MANIFEST.csv", "a") as f:
            f.write(f"{datetime.datetime.now(datetime.UTC).isoformat()},{sym},{info.get('rows')},{size},{info.get('sha256')}\n")
        log(f"{sym}: OK rows={info.get('rows')} size={size/1e6:.1f}MB")
    except Exception as e:
        log(f"{sym}: ERROR {e} — continue")
        time.sleep(60)
log("HARVEST COMPLETE")
