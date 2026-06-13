# Security Checklist

Use this checklist before, during, and after the one-shot replay.

## Before Replay

- [ ] Confirm the original prompt hash matches `sandbox-plan/PROMPT_ORIGINAL.sha256`.
- [ ] Create `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home`.
- [ ] Set `HOME=/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home` for the replay process.
- [ ] Confirm no real exchange keys are available in the replay environment.
- [ ] Confirm Railway target is a disposable project.
- [ ] Confirm Claude Code or the executing agent is not running with dangerous permission bypass.
- [ ] Confirm no global Hermes install will run during the first replay.
- [ ] Confirm all command approvals remain manual.

## During Replay

- [ ] Stop if the agent tries to read `~/.ssh`, wallets, browser data, or unrelated home folders.
- [ ] Stop if the agent tries to write outside `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home`.
- [ ] Stop if the agent requests live trading mode.
- [ ] Stop if the agent asks for exchange API keys.
- [ ] Stop if the agent attempts `curl | bash`, `irm | iex`, or similar without inspection.
- [ ] Stop if the agent tries to install Hermes globally before the generated worker is audited.
- [ ] Stop if Railway deploy uses an existing production project.

## Required Paper-Mode Invariants

- [ ] `.env` has `HERMES_TRADING_MODE=paper`.
- [ ] `.env` has `HERMES_TRADING_I_ACCEPT_RISK=false`.
- [ ] Exchange API key fields are empty.
- [ ] Worker code does not import a live execution adapter in paper mode.
- [ ] No withdrawal, transfer, margin, leverage, or order placement function is reachable.

## After Replay

- [ ] List all files created under `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home`.
- [ ] Review `pyproject.toml`, `Dockerfile`, and generated Python files.
- [ ] Review every subprocess call and shell command in generated code.
- [ ] Review Railway variables and confirm no secrets were added.
- [ ] Review Railway logs for external URLs, errors, or suspicious file access.
- [ ] Confirm strategy mutation changes at most one variable per reflection.
- [ ] Confirm generated strategy versions are preserved in history.
- [ ] Confirm Hermes has not been started as a persistent autonomous writer.

## Promotion Gate

Do not connect Hermes cron until all of these are true:

- [ ] Worker has run in paper mode for at least one clean test session.
- [ ] Generated code has been reviewed.
- [ ] Dependencies have been reviewed.
- [ ] A read-only Hermes briefing exists.
- [ ] Manual promotion process exists for strategy changes.
