"""Deterministic market-data snapshots for the replay harness.

Fetches paginated Binance spot klines ONCE into backtests/snapshots/ (never
state/data_cache/, which belongs to the live bot) and records a sha256 per
series in a manifest. Every replay then runs from the snapshot files only, so
a run is reproducible bit-for-bit and the manifest hash identifies exactly
which data produced which report.

Candle shape is the canonical orum one (scripts/run_paper_portfolio.py
`binance_provider`): {"ts": epoch_ms_of_bar_OPEN, "open", "high", "low",
"close", "volume"}. NOTE the Binance timestamp is the bar OPEN — the bar is
only knowable at ts + interval (timeline.py owns that arithmetic).
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "backtests" / "snapshots"
MANIFEST_PATH = SNAPSHOT_DIR / "manifest.json"

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000,
}

_UA = {"User-Agent": "0rum-replay-harness"}


def _series_path(symbol: str, interval: str) -> Path:
    return SNAPSHOT_DIR / f"{symbol.replace('/', '')}_{interval}.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_series(symbol: str, interval: str, start_ms: int, end_ms: int | None = None) -> list[dict]:
    """Paginated Binance spot klines in [start_ms, end_ms), oldest first."""
    binance_symbol = symbol.replace("/", "")
    end = end_ms or int(time.time() * 1000)
    out: list[dict] = []
    while True:
        url = (f"https://api.binance.com/api/v3/klines?symbol={binance_symbol}"
               f"&interval={interval}&limit=1000&endTime={end}")
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            batch = json.loads(r.read().decode())
        if not batch:
            break
        rows = [{"ts": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                for k in batch if int(k[0]) >= start_ms]
        out = rows + out
        if int(batch[0][0]) <= start_ms or len(batch) < 1000:
            break
        end = int(batch[0][0]) - 1
        time.sleep(0.25)
    return out


def build_snapshot(series: list[tuple[str, str]], start_ms: int, end_ms: int | None = None) -> dict:
    """Fetch every (symbol, interval) pair and (re)write the manifest.
    Existing series files are refetched only if absent — delete a file to force."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    for symbol, interval in series:
        path = _series_path(symbol, interval)
        if not path.exists():
            rows = fetch_series(symbol, interval, start_ms, end_ms)
            path.write_text(json.dumps(rows))
            print(f"  snapshot {symbol} {interval}: {len(rows)} bars -> {path.name}", flush=True)
        manifest["series"][path.name] = {
            "symbol": symbol, "interval": interval,
            "sha256": _sha256(path),
            "bars": len(json.loads(path.read_text())),
        }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST_PATH.read_text())
    except (OSError, ValueError):
        return {"series": {}}


def load_series(symbol: str, interval: str) -> list[dict]:
    path = _series_path(symbol, interval)
    rows = json.loads(path.read_text())
    expected = load_manifest()["series"].get(path.name, {}).get("sha256")
    actual = _sha256(path)
    if expected and expected != actual:
        raise ValueError(f"snapshot {path.name} does not match its manifest sha256 "
                         f"(expected {expected[:12]}, got {actual[:12]})")
    return rows


def manifest_fingerprint(series_names: list[str]) -> str:
    """Stable fingerprint of the data a run consumed (goes into the report)."""
    manifest = load_manifest()["series"]
    payload = json.dumps({name: manifest.get(name, {}).get("sha256") for name in sorted(series_names)},
                         sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
