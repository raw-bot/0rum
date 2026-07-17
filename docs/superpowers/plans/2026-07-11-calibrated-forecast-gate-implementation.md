# Calibrated forecast gate — implementation plan

## Scope

Paper portfolio only. New entries only. One shared account. No worker, broker or
dashboard process restart without explicit confirmation.

## Tasks

1. [x] Add failing unit tests for walk-forward chronology, activation thresholds,
   locked fallback, veto, resize and audit persistence.
2. [x] Implement a pure calibrated forecast module with deterministic quantiles and
   metrics.
3. [x] Integrate it into `PaperEngine.run_cycle` after signal generation and before
   `PaperBroker.open`; preserve exits and baseline behavior when disabled or
   locked.
4. [x] Add state paths and an explicit `forecast_gate` configuration block.
5. [x] Expose the latest calibrated forecast through the dashboard API and render
   quantile paths, status and reasons.
6. [x] Add proportional horizontal pan to every market chart while preserving
   zoom, card drag and card resize.
7. [x] Run focused tests, JavaScript syntax validation, full suite, GitNexus change
   detection and a read-only dashboard smoke test.

Deployment remains deliberately separate: the active dashboard has not been
restarted, and the hourly paper process will load the gate on its next normal
cycle.

## Rollback

Disable `forecast_gate.enabled` for an immediate behavioral rollback. Reverting
the engine integration leaves forecast state files harmless and preserves the
paper ledger.
