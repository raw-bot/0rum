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
- External URL HEAD checks: 14 live `200`, OANDA API endpoints return expected protected/method responses (`403` / `405`), and one external certificate failure on HistData.

## Findings

### AUDIT-001 - High - Current-state docs claim Telegram exists, but code and tests are web-only

Status:
- Addressed on 2026-05-20 by realigning active docs to local web dashboard monitoring.
- Updated: `AGENTS.md`, `CLAUDE.md`, `VISION.md`, `LAUNCH_PROMPT.md`.

Evidence:
- `CLAUDE.md:30` lists `src/monitoring/telegram_bot.py` as implemented.
- `test -e src/monitoring/telegram_bot.py` returned `telegram_bot_exists=1`, meaning the file does not exist.
- `AGENTS.md:57`, `AGENTS.md:73`, `AGENTS.md:156`, `AGENTS.md:159`, `AGENTS.md:862-907`, and `AGENTS.md:932-940` still describe Telegram as the monitoring/execution surface.
- `LAUNCH_PROMPT.md:249`, `LAUNCH_PROMPT.md:266-289`, `LAUNCH_PROMPT.md:329`, and `LAUNCH_PROMPT.md:365` also still require Telegram.
- `VISION.md:36` and `VISION.md:75-76` still list Telegram as the monitoring target.
- Tests assert the opposite: `tests/test_monitoring/test_dashboard.py` checks the dashboard must not mention Telegram.

Root cause:
- The project moved to local web dashboard monitoring, but older source-of-truth docs and current-state memory were only partially realigned.

Impact:
- Agents can reintroduce Telegram work by following stale instructions.
- Human planning is ambiguous: current runtime is web-only, but several docs still imply Telegram is required.

Recommended fix:
- Update `CLAUDE.md` current-state section to remove `src/monitoring/telegram_bot.py`.
- Add a top-level monitoring override to `AGENTS.md` or rewrite stale Telegram sections as historical.
- Update `VISION.md` monitoring target to local web dashboard first, with external notifications as future optional scope only.
- Mark `LAUNCH_PROMPT.md` as historical or remove Telegram requirements from active prompts.

### AUDIT-002 - High - Provider truth is inconsistent after Dukascopy research ingestion

Status:
- Addressed on 2026-05-20 by separating runtime plumbing, research/backtest data, legacy IG, and execution broker concerns.
- Updated: `AGENTS.md`, `CLAUDE.md`, `VISION.md`, `LAUNCH_PROMPT.md`.

Evidence:
- `AGENTS.md:12` says the priority target for real `XAUUSD` is IG demo -> live.
- `AGENTS.md:19-24` then says IG is legacy/inactive and Binance/PAXG remains active until a new provider is validated.
- `CLAUDE.md:68-73` says IG is inactive, Binance/PAXG is the active runtime/validation path, and Dukascopy is validated for research/backtest.
- `docs/provider-bakeoff.md:43-48` and `docs/decisions/ADR-003-dukascopy-research-ingestion.md` say Dukascopy is validated for `XAUUSD` research/backtest and does not affect runtime live execution.
- `src/ingestion/market_client.py:1-4` says IG demo/live can be selected additively for real XAUUSD ingestion.

Root cause:
- Runtime provider, validation data, research/backtest source, and execution broker are related but separate concerns. The docs sometimes collapse them into one "active path".

Impact:
- Future agents may validate strategy quality on Binance/PAXG, reactivate IG, or treat Dukascopy as a live runtime provider.
- This directly touches the project's critical invariant: market data provider and execution broker must remain separate.

Recommended fix:
- Define a single provider matrix in `CLAUDE.md`:
  - runtime live feed: Binance/PAXG proxy, plumbing only
  - research/backtest feed: Dukascopy public `.bi5`
  - execution broker: undecided
  - IG: legacy/inactive
- Replace "runtime/validation path" wording with separate "runtime plumbing" and "research/backtest validation" wording.

### AUDIT-003 - Medium - Startup ingestion background task is untracked

Evidence:
- `src/main.py:65-70` defines `_run_startup_ingestion()`.
- `src/main.py:72` calls `asyncio.create_task(_run_startup_ingestion())` without storing the task or attaching a done callback.

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

### AUDIT-004 - Medium - IG remains runtime-selectable despite being documented inactive

Evidence:
- `src/config.py` exposes `MarketDataProvider.IG`.
- `.env.example` keeps all IG variables and bounded IG warm-up settings.
- `src/main.py:67-68` runs `fetcher.warm_up_all()` when `MARKET_DATA_PROVIDER=ig`.
- `src/ingestion/market_client.py:47-48` instantiates `IGClient` when selected.
- Docs say IG is legacy/inactive (`CLAUDE.md:68`, `AGENTS.md:19-24`).

Root cause:
- The legacy IG path was preserved for debug compatibility, but the guardrail is only prose.

Impact:
- A single env change can reactivate an inactive runtime path.
- This risks expensive/slow/insufficient data behavior that the project explicitly deprecated.

Recommended fix:
- Add a setting such as `ALLOW_LEGACY_IG=false`.
- Refuse `MARKET_DATA_PROVIDER=ig` unless the explicit override is set.
- Rename docstrings from "can now be selected" to "legacy debug path only".

### AUDIT-005 - Medium - External HistData link fails TLS validation

Evidence:
- External HEAD scan returned:
  `ERR URLError: https://www.histdata.com/download-free-forex-historical-data/ :: [SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired`
- The link appears in `.planning/phases/05-backtesting-validation/05-RESEARCH.md:927`.

Root cause:
- External site certificate failure or a stale endpoint.

Impact:
- Agents and humans following historical research docs may hit a browser/security failure.
- Since HistData is still referenced in `CLAUDE.md:74` as Phase 5 validation backing, this is not purely archival noise.

Recommended fix:
- Re-check the HistData source manually in a browser.
- If the site is still usable, document the certificate caveat and prefer a stable mirror or local archived dataset path.
- If not usable, mark the research source as historical and point current backtest ingestion to Dukascopy.

### AUDIT-006 - Low - Health/risk comments still describe Telegram delivery

Evidence:
- `src/monitoring/health.py:87` says it counts "signal-mode Telegram deliveries" while the code counts `ApprovedSignalORM.execution_status == "SENT"`.
- `src/risk/hooks.py:4-9` and `src/risk/hooks.py:35` describe Telegram hook registration.
- `src/risk/events.py:7-8` and `src/risk/events.py:76-78` describe Telegram sender hooks.
- `src/risk/__init__.py:6` says `register_alert_hook` registers a Telegram sender.

Root cause:
- Code behavior was changed to local/web signal mode, but comments were not updated.

Impact:
- Lower runtime risk than AUDIT-001, but it reinforces stale implementation assumptions inside code-adjacent docs.

Recommended fix:
- Replace Telegram-specific comments with neutral "alert hook" / "notification adapter" language.
- Keep the hook generic and avoid naming an abandoned surface.

### AUDIT-007 - Low - Dukascopy 404 market-pause files are not cached

Evidence:
- `src/data/dukascopy/bi5.py:240-241` returns an empty parsed frame for HTTP 404.
- No cache marker is written for that hour before returning.

Root cause:
- The raw cache only stores successful `.bi5` payloads.

Impact:
- Expected market-pause hours can be re-requested on every retry or resumed batch.
- This is not a correctness bug, but it weakens the "progressive/reprenable" ergonomics for known empty hours.

Recommended fix:
- Add a small sidecar marker for confirmed 404 empty hours, for example `09h_ticks.bi5.empty`.
- Teach batch cache accounting to count that marker as present-but-empty.

## Non-Issues Verified

- Local markdown links are not broken.
- The PR branch test suite passes with current committed files: `324 passed`.
- Python files compile.
- Dukascopy package import remains decoupled from database settings.
- The `dukascopy_fetch.py` CLI exposes the expected industrial commands.

## Open Audit Limitations

- No subagent or fresh-context reviewer was used because delegation was not explicitly requested in this audit request.
- External link checking used HEAD requests; some servers may reject HEAD while accepting GET. OANDA `403` / `405` are treated as protected/method responses, not dead links.
- Runtime Docker build and live app startup were not executed during this audit.
