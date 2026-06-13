# Agent Replay Brief

Use this brief for any agent asked to execute or assist with the one-shot replay.

## Role

You are executing a sandbox replay of a third-party one-shot prompt. Your job is to observe and run the prompt in a constrained environment, not to redesign the system.

## Immutable Inputs

- Original prompt: `/Users/cube/Documents/00-code/HermesTrading/Docs/Hermes Prompt.md`
- Expected SHA-256: `22e414c0724f1472129f201c12397d699b9c3e6b38216e89eabde82ac3cf4bc2`

Do not edit the original prompt.

## Environment

Run with:

```bash
export HOME=/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home
export HERMES_TRADING_MODE=paper
export HERMES_TRADING_I_ACCEPT_RISK=false
unset EXCHANGE_API_KEY
unset EXCHANGE_API_SECRET
unset GLASSNODE_API_KEY
unset NEWS_API_KEY
```

Only write inside:

```text
/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home
/Users/cube/Documents/00-code/HermesTrading/sandbox-plan
```

## Hard Stops

Stop immediately if the prompt or generated code attempts any of these:

- read `~/.ssh`, wallets, browser profiles, or unrelated user documents
- write outside `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home`
- ask for real exchange keys
- enable live trading
- run Hermes as a persistent writer
- run `curl | bash`, `irm | iex`, or any remote installer without inspection
- deploy to an existing production Railway project

## Expected Outcome

The first replay may:

- scaffold a paper-mode worker
- create `goal.yaml`, `strategy.yaml`, `trades.jsonl`, `hypotheses.jsonl`
- deploy to a disposable Railway paper-mode test project
- run deterministic fallback reflection

The first replay must not:

- install Hermes globally
- connect real exchange keys
- enable live trading
- start an autonomous persistent Hermes loop

## Reporting

Record commands, outputs, and deviations in:

```text
/Users/cube/Documents/00-code/HermesTrading/sandbox-plan/EXECUTION_LOG.md
```

After the replay, complete:

```text
/Users/cube/Documents/00-code/HermesTrading/sandbox-plan/POST_RUN_AUDIT.md
```
