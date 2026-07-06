# Allowed Paths

This file defines the only filesystem areas that the one-shot replay may use.

## Host Workspace

Read-only reference material:

- `/Applications/0rum/Docs/0rum Prompt.md`
- `/Applications/0rum/Docs/0rum transcription.md`
- `/Applications/0rum/Docs/Images/`

Writable control docs:

- `/Applications/0rum/sandbox-plan/`
- `/Applications/0rum/docs/decisions/`

## Disposable Replay Home

Use this as HOME during the prompt replay:

- `/Applications/0rum/.sandbox/0rum-one-shot-home`

Expected prompt output inside that home:

- `/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/`
- `/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading-config/`
- `/Applications/0rum/.sandbox/0rum-one-shot-home/.cache/`
- `/Applications/0rum/.sandbox/0rum-one-shot-home/.local/`

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
- any path outside `/Applications/0rum/.sandbox/0rum-one-shot-home` unless it is listed above

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
curl -fsSL https://raw.githubusercontent.com/NousResearch/0rum-agent/main/scripts/install.sh -o /Applications/0rum/.sandbox/0rum-install.sh
sed -n '1,240p' /Applications/0rum/.sandbox/0rum-install.sh
```

Do not run the installer until it has been inspected and explicitly approved.
