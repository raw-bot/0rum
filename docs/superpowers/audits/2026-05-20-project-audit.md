# 0rum Project Audit - 2026-05-20

## Scope

Branch audited: `codex/dukascopy-research-ingestion`

Audit focus:
- bugs and runtime risks
- project/spec inconsistencies
- local and external documentation links
- PR-readiness signals after cleaning unrelated worktree changes

Superpowers used:
- `superpowers:systematic-debugging` for evidence-first issue classification
- `doubt-driven-development` as a degraded self-adversarial pass because no subagent delegation was requested
- `superpowers:verification-before-completion` for command-backed claims

## Verification Commands

```bash
git status --short --branch
./.venv/bin/python -m pytest -q
./.venv/bin/python -m compileall -q src scripts tests
env -u DATABASE_URL ./.venv/bin/python -c "import src.data.dukascopy as d; print(d.__all__)"
./.venv/bin/python scripts/dukascopy_fetch.py --help
```

Observed results:
- Worktree started clean on the PR branch before this report file was added.
- Test suite on the PR branch passed at audit time.
- `compileall` completed with exit code `0`.
- `src.data.dukascopy` imports without forcing `DATABASE_URL`.
- `scripts/dukascopy_fetch.py --help` exposes `probe`, `range`, `batch-plan`, `batch-fetch`, `qa`, and `import-postgres`.

Link scan:
- Local markdown links: `MISSING_LOCAL_LINKS=0`.
- Provider-specific stale links were removed during the 2026-05-20 provider cleanup.

## Findings

### AUDIT-001 - High - Current-state docs claimed an abandoned notification surface existed, but code and tests are web-only

Status:
- Addressed on 2026-05-20 by realigning active docs to local web dashboard monitoring.
- Updated: `AGENTS.md`, `CLAUDE.md`, `VISION.md`, `LAUNCH_PROMPT.md`.

Evidence:
- Earlier current-state docs listed a non-existent external notification module as implemented.
- A file-existence check for that legacy module path showed the file does not exist.
- Earlier project instructions still described the abandoned notification channel as the monitoring/execution surface.
- Earlier launch prompts also still required the abandoned notification channel.
- Earlier vision docs still listed the abandoned notification channel as the monitoring target.
- Tests and runtime behavior point to the local web dashboard as the active surface.

Result:
- Active docs now keep the local web dashboard as the operator surface.
- External notification surfaces remain out of scope unless a new explicit decision restores one.

### AUDIT-002 - High - Provider truth was inconsistent after Dukascopy research ingestion

Status:
- Addressed on 2026-05-20 by making the active provider matrix explicit and removing out-of-scope runtime paths.
- Updated: `AGENTS.md`, `CLAUDE.md`, `VISION.md`, `LAUNCH_PROMPT.md`, `docs/provider-bakeoff.md`, and runtime ingestion code.

Evidence:
- Current provider truth is now singular:
  - runtime market-data plumbing: Binance/PAXG proxy only
  - research/backtest XAUUSD data: Dukascopy public `.bi5`
  - execution broker: not selected in this branch
- Runtime provider selection code now exposes only the active runtime plumbing path.

Result:
- Binance/PAXG is documented as plumbing only.
- Dukascopy is documented as research/backtest only.
- Execution broker selection remains separate.

### AUDIT-003 - Medium - Startup ingestion background task was untracked

Status:
- Addressed on 2026-05-20 by storing the startup task on `app.state`, logging task failure, and cancelling/awaiting it during shutdown.

Evidence:
- `src/main.py` starts startup ingestion as a background task.
- The task is now assigned to `app.state.startup_ingestion_task`.
- Shutdown now cancels and awaits the task if it is still running.

Result:
- A startup ingestion failure is no longer silent task drift.
- App shutdown cleans up the background task instead of leaving it detached.

### AUDIT-007 - Low - Dukascopy 404 market-pause files were not cached

Status:
- Addressed on 2026-05-20 by adding `.empty` sidecar markers for confirmed 404 hours.

Evidence:
- `src/data/dukascopy/bi5.py` now writes `09h_ticks.bi5.empty` style markers for HTTP 404 responses.
- `DukascopyTickDownloader.fetch_hour()` reuses existing empty markers without refetching.
- `src/data/dukascopy/batch.py` counts those markers as cached hours, not missing raw files.

Result:
- Expected market-pause hours do not get re-requested on resumed batches.
- Batch planning now distinguishes missing hours from known empty hours.

## Non-Issues Verified

- Local markdown links are not broken.
- Python files compile.
- Dukascopy package import remains decoupled from database settings.
- The `dukascopy_fetch.py` CLI exposes the expected industrial commands.

## Open Audit Limitations

- No subagent or fresh-context reviewer was used because delegation was not explicitly requested in this audit request.
- Runtime Docker build and live app startup were not executed during this audit.
