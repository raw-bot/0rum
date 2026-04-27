# Plan 06-08 Summary — Pipeline Integration (Step 5.5)

## Status: COMPLETE

## Commit
- `92c5560` — feat(06-08): wire RiskGateRunner into pipeline step 5.5 + integration tests

## Changes Applied

### src/pipeline/runner.py
- Added import: `from src.risk import RiskGateRunner`
- Inserted step 5.5 block between `apply_quota` and status_map construction: own `AsyncSessionLocal` context per Pitfall 4, iterates quota survivors calling `risk_runner.evaluate(sig, regime, risk_session)`, accumulates `risk_passed` / `risk_rejected`
- Added `for sig in risk_rejected: status_map[id(sig)] = "REJECTED"` loop (before APPROVED loop — defensive ordering)
- Added `risk_rejected=len(risk_rejected)` to `pipeline.runner.complete` log

### tests/test_pipeline/test_runner.py
- Added `patch("src.pipeline.runner.RiskGateRunner") as mock_rgr_cls` to `test_runner_conflict_resolved_one_approved` multi-patch block with `evaluate = AsyncMock(return_value=MagicMock(passed=True))`

## Test Results
```
tests/test_pipeline/test_runner.py:        2 passed
tests/test_pipeline/test_runner_risk_step.py: 2 passed
Full suite (excl. backtesting): 188 passed
```

## Invariants Verified
- Pitfall 4: `async with AsyncSessionLocal() as risk_session:` exits before `_persist()` is called
- Risk-rejected candidates produce `CandidateSignalORM(status=REJECTED)` but no `ApprovedSignalORM`
- `risk_check_passed` on all persisted `ApprovedSignalORM` rows is always `True` (rejected candidates produce no row)
