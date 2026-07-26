"""Dev-phase options harvest v2 — adaptive time-splitting around the discovered
2.5M-row export cap. Resumable via chunk files; quota-aware; runs for days."""
import json, pathlib, time, datetime, urllib.request

HERE = pathlib.Path(__file__).parent
D = HERE / "data" / "options_dev"
D.mkdir(parents=True, exist_ok=True)
KEY = pathlib.Path.home().joinpath(".config/0rum/lse.key").read_text().strip()
BASE = "https://api.londonstrategicedge.com/vault"
START, END = "2014-01-01", "2020-01-15"
ROW_CAP = 2_500_000
WEEK_CAP, MONTH_CAP = 15.5e9, 48e9

def call(path, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers={
        "x-api-key": KEY, "User-Agent": "0rum-harvest",
        **({"Content-Type": "application/json"} if data else {})})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def log(*a): print(datetime.datetime.now(datetime.UTC).isoformat(), *a, flush=True)

def wait_quota():
    while True:
        try:
            u = call("/usage")
        except Exception as e:
            log("usage failed, wait 5min:", e); time.sleep(300); continue
        if u["bytes_used_week"] > WEEK_CAP: log("weekly cap — sleep 6h"); time.sleep(21600); continue
        if u["bytes_used_month"] > MONTH_CAP: log("monthly cap — sleep 12h"); time.sleep(43200); continue
        if u["exports_this_hour"] >= u["exports_cap_hour"]: time.sleep(600); continue
        return

def export_range(sym, a, b):
    """Export [a,b); returns ('ok', rows) after saving, 'split' if cap hit, 'skip' on failure."""
    fname = D / f"{sym.replace('/','_')}__{a}__{b}.parquet"
    if fname.exists():
        return "done"
    wait_quota()
    try:
        job = call("/export", {"dataset": "options", "symbol": sym, "timeframe": "tick",
                               "start": a, "end": b, "format": "parquet"})
        jid = job["job_id"]
        for _ in range(600):
            info = call(f"/export/{jid}")
            st = info.get("status")
            if st == "ready": break
            if st in ("failed", "expired"): log(f"{sym} {a}..{b}: {st}"); return "skip"
            time.sleep(5)
        else:
            return "skip"
        rows = int(info.get("rows") or 0)
        if rows >= ROW_CAP:
            return "split"
        size = int(info.get("bytes") or 0)
        raw = urllib.request.urlopen(urllib.request.Request(
            f"{BASE}/export/{jid}/download", headers={"x-api-key": KEY, "User-Agent": "0rum-harvest"}), timeout=1800).read()
        if len(raw) != size:
            log(f"{sym} {a}..{b}: incomplete dl"); return "skip"
        fname.write_bytes(raw)
        with open(HERE / "HARVEST_MANIFEST.csv", "a") as f:
            f.write(f"{datetime.datetime.now(datetime.UTC).isoformat()},{sym},{a},{b},{rows},{size},{info.get('sha256')}\n")
        log(f"{sym} {a}..{b}: OK rows={rows} {size/1e6:.1f}MB")
        return "ok"
    except Exception as e:
        log(f"{sym} {a}..{b}: ERROR {e}"); time.sleep(60); return "skip"

def midpoint(a, b):
    da = datetime.date.fromisoformat(a); db = datetime.date.fromisoformat(b)
    return (da + (db - da) / 2).isoformat()

def harvest_symbol(sym):
    stack = [(START, END)]
    while stack:
        a, b = stack.pop()
        r = export_range(sym, a, b)
        if r == "split":
            m = midpoint(a, b)
            if m in (a, b):
                log(f"{sym}: cannot split further {a}..{b} — skip"); continue
            stack.append((m, b)); stack.append((a, m))

universe = [r["symbol"] for r in json.loads((HERE / "universe_top300.json").read_bytes())]
etfs = {"ARKK","EEM","GDX","HYG","IWM","SMH","SOXL","SOXS","SQQQ","TLT","TQQQ","XLE","XLF"}
symbols = [s for s in universe if s not in etfs]
# purge truncated v1 files (rows==cap exactly): they lack coverage
for f in D.glob("*.parquet"):
    if "__" not in f.stem:
        f.unlink()
        log(f"purged v1 file {f.name}")
log(f"harvest v2: {len(symbols)} symbols")
for sym in symbols:
    harvest_symbol(sym)
log("HARVEST COMPLETE")
