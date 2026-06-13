# Allowed Paths

This file defines the only filesystem areas that the one-shot replay may use.

## Host Workspace

Read-only reference material:

- `/Users/cube/Documents/00-code/HermesTrading/Docs/Hermes Prompt.md`
- `/Users/cube/Documents/00-code/HermesTrading/Docs/hermes transcription.md`
- `/Users/cube/Documents/00-code/HermesTrading/Docs/Images/`

Writable control docs:

- `/Users/cube/Documents/00-code/HermesTrading/sandbox-plan/`
- `/Users/cube/Documents/00-code/HermesTrading/docs/decisions/`

## Disposable Replay Home

Use this as HOME during the prompt replay:

- `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home`

Expected prompt output inside that home:

- `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/`
- `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading-config/`
- `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/.cache/`
- `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/.local/`

## Forbidden Paths

Do not allow the replay agent to read or write:

- `/Users/cube/.ssh/`
- `/Users/cube/.aws/`
- `/Users/cube/.config/gh/`
- `/Users/cube/.docker/`
- `/Users/cube/Documents/`
- `/Users/cube/Desktop/`
- `/Users/cube/Downloads/`
- any wallet, seed phrase, browser profile, password store, or exchange credential file
- any path outside `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home` unless it is listed above

## Railway Scope

Railway usage is allowed only for a disposable paper-mode test project.

Forbidden:

- existing production Railway projects
- live trading services
- shared production variables
- real exchange API keys

## Network Scope

Network access is expected for package managers, Railway auth, public market data, and official installer inspection.

Any command that downloads and executes code in one step must be split into inspect-then-run. Example:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh -o /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-install.sh
sed -n '1,240p' /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-install.sh
```

Do not run the installer until it has been inspected and explicitly approved.
