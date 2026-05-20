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
- Test suite on the PR branch: `324 passed in 11.81s`.
- `compileall` completed with exit code `0`.
- `src.data.dukascopy` imports without forcing `DATABASE_URL`.
- `scripts/dukascopy_fetch.py --help` exposes `probe`, `range`, `batch-plan`, `batch-fetch`, `qa`, and `import-postgres`.

Link scan:
- Local markdown links: `MISSING_LOCAL_LINKS=0`.
- External URL HEAD checks were run during the original audit; provider-specific stale links were removed during the 2026-05-20 provider cleanup.

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

Root cause:
- The project moved to local web dashboard monitoring, but older source-of-truth docs and current-state memory were only partially realigned.

Impact:
- Agents can reintroduce abandoned notification work by following stale instructions.
- Human planning is ambiguous: current runtime is web-only, but several docs still imply an external channel is required.

Recommended fix:
- Keep the local web dashboard as the active operator surface in current docs.
- Treat external notification surfaces as out of scope unless a new explicit decision restores one.

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

Root cause:
- Runtime provider, validation data, research/backtest source, and execution broker are related but separate concerns. The docs sometimes collapsed them into one active path.

Impact:
- Future agents may validate strategy quality on Binance/PAXG or treat Dukascopy as a live runtime provider.
- This directly touches the project's critical invariant: market data provider and execution broker must remain separate.

Recommended fix:
- Keep a single provider matrix in `CLAUDE.md`:
  - runtime live feed: Binance/PAXG proxy, plumbing only
  - research/backtest feed: Dukascopy public `.bi5`
  - execution broker: undecided
- Use separate "runtime plumbing" and "research/backtest validation" wording everywhere.

### AUDIT-003 - Medium - Startup ingestion background task is untracked

Evidence:
- `src/main.py` defines `_run_startup_ingestion()`.
- `src/main.py` calls `asyncio.create_task(_run_startup_ingestion())` without storing the task or attaching a done callback.

Root cause:
- Startup ingestion was intentionally made non-blocking, but task lifecycle and exception reporting were not formalized.

Impact:
- Exceptions raised before `CandleFetcher.backfill_all()` per-timeframe handling can become "Task exception was never retrieved".
- The health/dashboard surface cannot report that startup ingestion died.
- Shutdown does not cancel or await the task.

Recommended fix:
- Store the task on `app.state.startup_ingestion_task`.
- Add a done callback that logs exceptions with structlog.
- On lifespan shutdown, cancel and await the task if still running.
- Consider exposing startup-ingestion status in `/health`.

### AUDIT-004 - Medium - Out-of-scope runtime path remained selectable

Status:
- Addressed on 2026-05-20 by removing out-of-scope runtime selection, settings, diagnostics, and tests.

Evidence:
- Runtime selection exposed an out-of-scope path in config, env examples, startup branching, and diagnostics.
- Those code paths are now removed; runtime plumbing is Binance/PAXG only.

Root cause:
- An old runtime path was preserved for debug compatibility, but the guardrail was only prose.

Impact:
- A single env change could reactivate an inactive runtime path.
- This risked expensive, slow, or insufficient data behavior that the project explicitly removed from active scope.

Recommended fix:
- Keep out-of-scope runtime code/config removed unless an explicit future decision restores it.

### AUDIT-005 - Medium - Out-of-scope bootstrap data link failed TLS validation

Status:
- Addressed on 2026-05-20 by removing out-of-scope bootstrap-source references from active provider truth.

Evidence:
- External link checking previously found a certificate failure on an out-of-scope bootstrap data source referenced from historical planning docs.

Root cause:
- External site certificate failure or a stale endpoint.

Impact:
- Agents and humans following historical research docs may hit a browser/security failure.
- Since active validation now points at Dukascopy, the old bootstrap path should not guide implementation.

Recommended fix:
- Keep current docs pointed at Dukascopy research/backtest ingestion and avoid old bootstrap-source links in active guidance.

### AUDIT-006 - Low - Health/risk comments still describe abandoned external delivery

Evidence:
- Health and risk comments described signal-mode deliveries with abandoned channel wording while the code tracks local `ApprovedSignalORM.execution_status == "SENT"` state.
- Risk hooks were generic in implementation, but some comments used stale channel-specific wording.

Root cause:
- Code behavior was changed to local/web signal mode, but comments were not updated.

Impact:
- Lower runtime risk than AUDIT-001, but it reinforces stale implementation assumptions inside code-adjacent docs.

Recommended fix:
- Replace channel-specific comments with neutral "alert hook" / "notification adapter" language.
- Keep the hook generic and avoid naming an abandoned surface.

### AUDIT-007 - Low - Dukascopy 404 market-pause files are not cached

Evidence:
- `src/data/dukascopy/bi5.py` returns an empty parsed frame for HTTP 404.
- No cache marker is written for that hour before returning.

Root cause:
- The raw cache only stores successful `.bi5` payloads.

Impact:
- Expected market-pause hours can be re-requested on every retry or resumed batch.
- This is not a correctness bug, but it weakens the progressive/reprenable ergonomics for known empty hours.

Recommended fix:
- Add a small sidecar marker for confirmed 404 empty hours, for example `09h_ticks.bi5.empty`.
- Teach batch cache accounting to count that marker as present-but-empty.

## Non-Issues Verified

- Local markdown links are not broken.
- The PR branch test suite passed at audit time.
- Python files compile.
- Dukascopy package import remains decoupled from database settings.
- The `dukascopy_fetch.py` CLI exposes the expected industrial commands.

## Open Audit Limitations

- No subagent or fresh-context reviewer was used because delegation was not explicitly requested in this audit request.
- External link checking used HEAD requests; some servers may reject HEAD while accepting GET.
- Runtime Docker build and live app startup were not executed during this audit.
