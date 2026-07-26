"""Gate n°1 — EDGAR coverage audit per PREREG_A.md (commit 00e6b17).
Universe from LSE options catalog (no outcome data touched)."""
import json, pathlib, time, datetime, urllib.request, urllib.parse

HERE = pathlib.Path(__file__).parent
OUT = HERE / "gate_edgar_results.json"
KEY = pathlib.Path.home().joinpath(".config/0rum/lse.key").read_text().strip()
SEC_UA = {"User-Agent": "0rum research audit drawbot@gmail.com"}

def get(url, headers, tries=3, timeout=60):
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout).read()
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(2)

def log(*a): print(*a, flush=True)

# 1. Universe: top 300 by option print count, >=8y history
cat = json.loads(get("https://api.londonstrategicedge.com/vault/catalog", {"x-api-key": KEY, "User-Agent": "0rum-audit"}))
opts = [r for r in cat if r.get("dataset") == "options"]
def years(r):
    try: return float(r.get("years") or 0)
    except Exception: return 0
elig = [r for r in opts if years(r) >= 8]
elig.sort(key=lambda r: int(r.get("ticks") or 0), reverse=True)
universe = [r["symbol"] for r in elig[:300]]
log(f"options catalog: {len(opts)} underlyings | >=8y: {len(elig)} | universe: {len(universe)}")
(HERE / "universe_top300.json").write_text(json.dumps(
    [{"symbol": r["symbol"], "ticks": r.get("ticks"), "first": r.get("first_tick"), "years": r.get("years")} for r in elig[:300]], indent=1))

# 2. CIK mapping
tickmap = json.loads(get("https://www.sec.gov/files/company_tickers.json", SEC_UA))
by_ticker = {v["ticker"].upper(): v["cik_str"] for v in tickmap.values()}
mapped = {s: by_ticker[s.upper()] for s in universe if s.upper() in by_ticker}
log(f"CIK mapped: {len(mapped)}/300 (unmapped likely delisted/renamed -> survivorship note)")

# 3. Submissions harvest: 8-K item 2.02 acceptance datetimes
events = []
errors = 0
for n, (sym, cik) in enumerate(mapped.items()):
    try:
        sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", SEC_UA, timeout=30))
        batches = [sub["filings"]["recent"]]
        for extra in sub["filings"].get("files", []):
            batches.append(json.loads(get(f"https://data.sec.gov/submissions/{extra['name']}", SEC_UA, timeout=30)))
        for b in batches:
            forms, items_l, acc = b["form"], b.get("items", []), b["acceptanceDateTime"]
            for i in range(len(forms)):
                if forms[i] == "8-K" and items_l and i < len(items_l) and "2.02" in (items_l[i] or ""):
                    if acc[i] and acc[i][:4] >= "2014":
                        events.append({"symbol": sym, "acceptance": acc[i]})
    except Exception as e:
        errors += 1
    if n % 50 == 0:
        log(f"  {n}/{len(mapped)} companies, {len(events)} events so far")
    time.sleep(0.12)
log(f"harvest done: {len(events)} 8-K 2.02 events, {errors} company errors")

# 4. Coverage stats
from collections import Counter
by_year = Counter(e["acceptance"][:4] for e in events)
def hour_et(iso):  # acceptanceDateTime is ET per SEC
    return int(iso[11:13])
amc = sum(1 for e in events if hour_et(e["acceptance"]) >= 16)
bmo = sum(1 for e in events if hour_et(e["acceptance"]) < 9)
mid = len(events) - amc - bmo
per_co_yr = len(events) / max(len(mapped), 1) / 12.5
dev_years = [str(y) for y in range(2014, 2020)]
dev_events_per_year = sum(by_year[y] for y in dev_years) / 6

res = {
    "meta": {"prereg_commit": "00e6b17", "run": datetime.datetime.now(datetime.UTC).isoformat()},
    "universe": {"catalog_options": len(opts), "ge8y": len(elig), "top300": len(universe)},
    "cik_mapped": len(mapped), "unmapped": sorted(set(universe) - set(mapped)),
    "events_total_2014plus": len(events),
    "events_by_year": dict(sorted(by_year.items())),
    "amc_bmo_mid": {"amc_after16": amc, "bmo_before9": bmo, "intraday": mid},
    "events_per_company_year": round(per_co_yr, 2),
    "dev_events_per_year_avg": round(dev_events_per_year, 0),
    "gate_thresholds": {"coverage_min_frac": 0.60, "dev_events_min_per_year": 300},
    "gate_coverage_frac": round(per_co_yr / 4.0, 2),
    "GATE": "PASS" if (per_co_yr / 4.0 >= 0.60 and dev_events_per_year >= 300) else "FAIL",
}
(HERE / "edgar_events.json").write_text(json.dumps(events))
OUT.write_text(json.dumps(res, indent=2))
log(json.dumps(res, indent=2))
log("GATE DONE")
