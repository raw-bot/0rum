# Post-Run Audit

Complete this audit after the one-shot replay stops.

## Summary

- Replay completed:
- Railway deployed:
- Hermes installed:
- Live trading enabled:
- Secrets used:
- Overall verdict: `PASS`, `PASS WITH FIXES`, or `FAIL`

## Files Reviewed

```text
[ ] /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/pyproject.toml
[ ] /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/Dockerfile
[ ] /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/.env
[ ] /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/hermes_trading/run.py
[ ] /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/hermes_trading/loop.py
[ ] /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/hermes_trading/reflect.py
[ ] /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/hermes_trading/score.py
[ ] /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/hermes_trading/adapters/
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
[ ] Hermes installer was not run during first replay.
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
[ ] Attach Hermes read-only cron.
```

## Required Fixes Before Promotion

```text
No fixes recorded before replay.
```
