"""Contrôle de parité quotidien (launchd com.0rum.parity) — SANS TradingView.

Compare le producteur LIVE au code du labo sur la fenêtre où le producteur
a tourné :
  1. Recalcule les entrées LONG confirmées (confirmed_entries, mêmes fonctions
     que backtest_4h_validation) sur les ~900 dernières barres 4h Binance.
  2. Lit les verdicts du producteur (state/ak_macd_local_shadow.jsonl).
  3. DIVERGENCE si un BUY confirmé existe d'un côté et pas de l'autre dans la
     fenêtre commune, ou si le producteur a des trous de barres (> 1 barre 4h
     manquante = panne passée inaperçue).
Résultat -> state/parity_check.json ; divergence -> notification macOS +
append state/parity_alerts.jsonl.

Le miroir TV (pine/ak_macd_4h_mirror.pine) est du code statique vérifié 35/35
le 2026-07-06 : il ne peut dériver que si on l'édite. Le risque réel de dérive
est producteur-vs-labo — c'est ce que ce script surveille.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_4h_validation import fetch_klines, confirmed_entries  # noqa: E402
from orum.external.ak_macd import AkMacdParams, compute_state  # noqa: E402

STATE = ROOT / "state"
JSONL = STATE / "ak_macd_local_shadow.jsonl"
BAR_MS = 4 * 3600 * 1000


def notify(msg: str) -> None:
    subprocess.run(["/usr/bin/osascript", "-e",
                    f'display notification "{msg}" with title "0rum PARITY" sound name "Basso"'],
                   check=False)


def main() -> None:
    now = datetime.now(UTC).isoformat()

    # 1. Labo : entrées confirmées recalculées (barre en formation exclue).
    bars = fetch_klines(symbol="BTCUSDT", interval="4h", n=900)[:-1]
    st = compute_state(bars, AkMacdParams())
    ts = [b["ts"] for b in bars]
    lab_buys = {ts[t] for (t, d) in confirmed_entries(st, AkMacdParams()) if d == "long"}

    # 2. Producteur : verdicts loggés.
    prod_lines = []
    if JSONL.exists():
        for line in JSONL.read_text().splitlines():
            if line.strip():
                try:
                    prod_lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    prod_bars = sorted({int(e["bar_time"]) for e in prod_lines if e.get("bar_time")})
    prod_buys = {int(e["bar_time"]) for e in prod_lines
                 if e.get("action") == "candidate_confirmed" and e.get("event") == "BUY_CANDIDATE"}

    problems: list[str] = []
    if not prod_bars:
        problems.append("producteur: aucun verdict loggé")
        window = (0, 0)
    else:
        window = (prod_bars[0], prod_bars[-1])
        # 3a. BUY d'un côté seulement, dans la fenêtre commune.
        lab_in_win = {b for b in lab_buys if window[0] <= b <= window[1]}
        miss_prod = sorted(lab_in_win - prod_buys)
        miss_lab = sorted(prod_buys - lab_buys)
        for b in miss_prod:
            problems.append(f"BUY labo absent du producteur: {datetime.fromtimestamp(b/1000, UTC):%Y-%m-%d %H:%M}")
        for b in miss_lab:
            problems.append(f"BUY producteur absent du labo: {datetime.fromtimestamp(b/1000, UTC):%Y-%m-%d %H:%M}")
        # 3b. Trous de barres chez le producteur (panne silencieuse passée).
        gaps = [(a, b) for a, b in zip(prod_bars, prod_bars[1:]) if b - a > BAR_MS]
        for a, b in gaps[-5:]:
            problems.append(f"trou producteur: {datetime.fromtimestamp(a/1000, UTC):%m-%d %H:%M} -> "
                            f"{datetime.fromtimestamp(b/1000, UTC):%m-%d %H:%M}")

    result = {
        "ts": now, "status": "ALERT" if problems else "ok",
        "lab_buys_900bars": len(lab_buys), "producer_bars": len(prod_bars),
        "producer_buys": len(prod_buys),
        "window_utc": [datetime.fromtimestamp(w/1000, UTC).isoformat() if w else None for w in window],
        "problems": problems,
    }
    (STATE / "parity_check.json").write_text(json.dumps(result, indent=2))
    if problems:
        with (STATE / "parity_alerts.jsonl").open("a") as f:
            f.write(json.dumps({"ts": now, "problems": problems}) + "\n")
        notify(f"{len(problems)} divergence(s) producteur/labo — voir state/parity_check.json")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
