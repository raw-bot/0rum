# backtests/ — replay harness workspace

Produced and consumed by `scripts/replay_harness/` (CLI:
`scripts/run_replay_harness.py`). Everything here is OFFLINE artifacts — the
live bot never reads this directory, and the harness never writes to `state/`.

```
snapshots/   immutable market-data snapshots + manifest.json (sha256 per file).
             Delete a series file to force a refetch on the next `snapshot` run.
fixtures/    golden exports (e.g. pine_golden_btcusdt_4h.json: the raw long
             signals + indicator values computed BY TradingView, via the
             label export built into the Pine script).
runs/<id>/   one directory per replay run: events.jsonl (candidate →
             decision → order → fill → exit), report.json (per-config
             summaries + stop/ATR attribution), trades_<config>.json,
             parity.json, and for runtime replays the isolated ledger.
```

Quick reference (repo root, project venv):

```bash
PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py snapshot
PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py single  --run-id my_run
PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py runtime --run-id my_rt \
    --start 2024-01-01 --end 2026-07-15 --gate fast   # fast = gate vectorisé (parité de décision testée)
PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py runtime --run-id my_arb \
    --start 2024-01-01 --end 2026-07-15 --gate fast \
    --portfolio-config backtests/configs/duo_ak_utbot.yaml \
    --arbiter topup --merit btc_utbot_m15_h1,btc_ak_macd_4h
    # variante arbitre budget-par-thèse (scripts/replay_harness/arbiter.py) :
    # enchère par cycle par (symbole, direction), top-up du budget restant,
    # refus `thesis_already_funded` ; --arbiter hold = pas de pyramidage.
PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py compare-arbiter \
    --baseline duo_ak_utbot --candidate my_arb        # rapport avant/après (arbiter_report.json)
PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py overlap --run-id matrix \
    --runs iso_a,iso_b,iso_c                          # matrice de chevauchement entre runs isolés
PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py parity  --run-id my_run \
    --golden backtests/fixtures/pine_golden_btcusdt_4h.json
```

Runs de référence (2024-01 → 2026-07, conventions legacy) : `rt_full_fast` (portefeuille
complet), `iso_ak` / `iso_utbot` / `iso_ha` (stratégies isolées, `iso_ha` = ledger shadow
contrefactuel HA), `duo_ak_utbot` (valeur marginale de HA), `overlap_v1` (matrice),
`sizing_v1` (matrice sizing × fill × target).

Charter (enforced by design + tests/test_replay_harness.py):
1. deterministic — reports carry the data manifest sha256 and a config hash;
2. zero writes to the live paper state (0RUM_STATE_DIR is forced per run);
3. baselines REPRODUCE current conventions, imperfections included;
4. no indicator reads a bar that closes after the simulated clock;
5. every policy consumes the same candidate stream; rejections are logged
   decisions, never silent drops;
6. Pine parity is *explained* (every divergence attributed), never byte-exact.
