# Post-Run Audit

Complete this audit after the one-shot replay stops.

## Summary

- Replay completed:
- Railway deployed:
- 0rum installed:
- Live trading enabled:
- Secrets used:
- Overall verdict: `PASS`, `PASS WITH FIXES`, or `FAIL`

## Files Reviewed

```text
[ ] /Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/pyproject.toml
[ ] /Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/Dockerfile
[ ] /Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/.env
[ ] /Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/orum/run.py
[ ] /Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/orum/loop.py
[ ] /Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/orum/reflect.py
[ ] /Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/orum/score.py
[ ] /Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/orum/adapters/
[ ] Railway variables
[ ] Railway logs
```

## Security Findings

Record findings as `P0`, `P1`, `P2`, or `P3`.

```text
No findings recorded before replay.
```

## Trading Safety Findings

Check all of these:

```text
[ ] Paper mode is enforced.
[ ] Risk acceptance flag is false.
[ ] No live exchange order path is reachable.
[ ] No withdrawal or transfer function exists.
[ ] Position sizing is bounded.
[ ] Drawdown stop exists.
[ ] Circuit breaker exists.
[ ] Reflection changes exactly one variable.
[ ] Strategy history is preserved.
```

## Supply Chain Findings

Check all of these:

```text
[ ] Dependencies are listed and explainable.
[ ] Dockerfile does not execute uninspected remote scripts except approved installers.
[ ] 0rum installer was not run during first replay.
[ ] No hidden postinstall behavior observed.
```

## Code Quality Findings

Check all of these:

```text
[ ] Generated code runs locally in paper mode.
[ ] Basic tests or smoke commands pass.
[ ] Errors stop safely rather than retrying blindly.
[ ] Logs contain enough information for diagnosis.
[ ] State files remain valid YAML or JSONL.
```

## Promotion Decision

Choose one:

```text
[ ] Do not promote. Discard replay output.
[ ] Promote after listed fixes.
[ ] Promote to durable paper-mode repo.
[ ] Attach 0rum read-only cron.
```

## Required Fixes Before Promotion

```text
No fixes recorded before replay.
```
