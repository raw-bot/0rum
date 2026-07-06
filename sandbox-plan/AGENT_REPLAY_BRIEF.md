# Agent Replay Brief

Use this brief for any agent asked to execute or assist with the one-shot replay.

## Role

You are executing a sandbox replay of a third-party one-shot prompt. Your job is to observe and run the prompt in a constrained environment, not to redesign the system.

## Immutable Inputs

- Original prompt: `/Applications/0rum/Docs/0rum Prompt.md`
- Expected SHA-256: `22e414c0724f1472129f201c12397d699b9c3e6b38216e89eabde82ac3cf4bc2`

Do not edit the original prompt.

## Environment

Run with:

```bash
export HOME=/Applications/0rum/.sandbox/0rum-one-shot-home
export ORUM_TRADING_MODE=paper
export ORUM_TRADING_I_ACCEPT_RISK=false
unset EXCHANGE_API_KEY
unset EXCHANGE_API_SECRET
unset GLASSNODE_API_KEY
unset NEWS_API_KEY
```

Only write inside:

```text
/Applications/0rum/.sandbox/0rum-one-shot-home
/Applications/0rum/sandbox-plan
```

## Hard Stops

Stop immediately if the prompt or generated code attempts any of these:

- read `~/.ssh`, wallets, browser profiles, or unrelated user documents
- write outside `/Applications/0rum/.sandbox/0rum-one-shot-home`
- ask for real exchange keys
- enable live trading
- run 0rum as a persistent writer
- run `curl | bash`, `irm | iex`, or any remote installer without inspection
- deploy to an existing production Railway project

## Expected Outcome

The first replay may:

- scaffold a paper-mode worker
- create `goal.yaml`, `strategy.yaml`, `trades.jsonl`, `hypotheses.jsonl`
- deploy to a disposable Railway paper-mode test project
- run deterministic fallback reflection

The first replay must not:

- install 0rum globally
- connect real exchange keys
- enable live trading
- start an autonomous persistent 0rum loop

## Reporting

Record commands, outputs, and deviations in:

```text
/Applications/0rum/sandbox-plan/EXECUTION_LOG.md
```

After the replay, complete:

```text
/Applications/0rum/sandbox-plan/POST_RUN_AUDIT.md
```
