"""Dev options harvest v4 — GENTLE MODE after key deactivation incident.
Pre-computed chunk plan from catalog tick counts (no blind probe jobs),
max ~3 exports/hour, immediate abort on 403/inactive, resumable.
Smallest symbols first (maximize completed symbols per GB)."""
import json, math, pathlib, time, datetime, urllib.request

HERE = pathlib.Path(__file__).parent
D = HERE / "data" / "options_dev"
D.mkdir(parents=True, exist_ok=True)
KEY = pathlib.Path.home().joinpath(".config/0rum/lse.key").read_text().strip()
BASE = "https://api.londonstrategicedge.com/vault"
START = datetime.date(2014, 1, 1)
END = datetime.date(2020, 1, 15)
DEV_DAYS = (END - START).days
CHUNK_TARGET = 2_200_000
ROW_CAP = 2_500_000
PACE_SECONDS = 30            # normal pace; quota-driven (shares key with user backfill)
WEEK_CAP, MONTH_CAP = 14e9, 45e9

class KeyDead(Exception): pass

def call(path, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers={
        "x-api-key": KEY, "User-Agent": "0rum-harvest-gentle",
        **({"Content-Type": "application/json"} if data else {})})
    backoff = 60
    while True:
        try:
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            if e.code == 403 or "inactive" in body:
                raise KeyDead(body)
            if e.code == 429:
                log(f"429 (shared key contention) — backoff {backoff}s")
                time.sleep(backoff); backoff = min(backoff * 2, 900); continue
            raise

def log(*a): print(datetime.datetime.now(datetime.UTC).isoformat(), *a, flush=True)

def wait_quota():
    while True:
        u = call("/usage")
        if u["bytes_used_week"] > WEEK_CAP: log("weekly cap margin — sleep 6h"); time.sleep(21600); continue
        if u["bytes_used_month"] > MONTH_CAP: log("monthly cap margin — sleep 12h"); time.sleep(43200); continue
        if u["exports_this_hour"] >= u["exports_cap_hour"]: time.sleep(300); continue
        return

def export_chunk(sym, a, b, allow_split=True):
    fname = D / f"{sym.replace('/','_')}__{a}__{b}.parquet"
    if fname.exists():
        return
    wait_quota()
    job = call("/export", {"dataset": "options", "symbol": sym, "timeframe": "tick",
                           "start": str(a), "end": str(b), "format": "parquet"})
    jid = job["job_id"]
    info = None
    for _ in range(720):
        info = call(f"/export/{jid}")
        st = info.get("status")
        if st == "ready": break
        if st in ("failed", "expired"): log(f"{sym} {a}..{b}: {st}"); return
        time.sleep(10)
    if not info or info.get("status") != "ready":
        return
    rows = int(info.get("rows") or 0)
    if rows >= ROW_CAP and allow_split:
        m = a + (b - a) / 2
        log(f"{sym} {a}..{b}: cap hit -> split (plan was wrong)")
        time.sleep(PACE_SECONDS)
        export_chunk(sym, a, m, allow_split=True)
        time.sleep(PACE_SECONDS)
        export_chunk(sym, m, b, allow_split=True)
        return
    size = int(info.get("bytes") or 0)
    raw = urllib.request.urlopen(urllib.request.Request(
        f"{BASE}/export/{jid}/download", headers={"x-api-key": KEY, "User-Agent": "0rum-harvest-gentle"}), timeout=1800).read()
    if len(raw) != size:
        log(f"{sym} {a}..{b}: incomplete dl — retry next run"); return
    fname.write_bytes(raw)
    with open(HERE / "HARVEST_MANIFEST.csv", "a") as f:
        f.write(f"{datetime.datetime.now(datetime.UTC).isoformat()},{sym},{a},{b},{rows},{size},{info.get('sha256')}\n")
    log(f"{sym} {a}..{b}: OK rows={rows} {size/1e6:.1f}MB")

uni = json.loads((HERE / "universe_top300.json").read_bytes())
from collections import Counter
ev = Counter(e["symbol"] for e in json.loads((HERE / "edgar_events.json").read_bytes()))
plan = []
for r in uni:
    sym = r["symbol"]
    if ev.get(sym, 0) == 0:
        continue
    ticks = int(r.get("ticks") or 0)
    yrs = float(r.get("years") or 12)
    est_dev_rows = ticks * min(6.04 / max(yrs, 1), 1.0)
    n_chunks = max(1, math.ceil(est_dev_rows / CHUNK_TARGET))
    plan.append((n_chunks, sym))
plan.sort()  # smallest first
total_jobs = sum(n for n, _ in plan)
log(f"harvest v4 gentle: {len(plan)} symbols, ~{total_jobs} planned chunks, pace 3/h -> ~{total_jobs/5/24:.1f}-{total_jobs/12/24:.1f} days")

try:
    for n_chunks, sym in plan:
        step = DEV_DAYS / n_chunks
        for k in range(n_chunks):
            a = START + datetime.timedelta(days=round(k * step))
            b = START + datetime.timedelta(days=round((k + 1) * step)) if k < n_chunks - 1 else END
            fname = D / f"{sym.replace('/','_')}__{a}__{b}.parquet"
            if fname.exists():
                continue
            export_chunk(sym, a, b)
            time.sleep(PACE_SECONDS)
except KeyDead as e:
    log(f"KEY DEACTIVATED — ABORT IMMEDIATELY: {e}")
    raise SystemExit(2)
log("HARVEST COMPLETE")
