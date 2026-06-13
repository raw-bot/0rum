# Hermes One-Shot Sandbox Replay Runbook

This runbook tests the original one-shot prompt without modifying it and without giving it access to the real host environment.

## Goals

- Preserve the original prompt behavior as much as possible.
- Keep all prompt-created files inside a disposable HOME.
- Deploy only a paper-mode Railway test worker.
- Prevent global Hermes installation during the first replay.
- Audit generated code before enabling any recurring Hermes loop.

## Non-Goals

- No live trading.
- No real exchange keys.
- No global Hermes install on the first replay.
- No autonomous persistent Hermes writer.
- No direct execution with dangerous permission bypass.

## Current Project Notes

- This folder is not currently a git repository.
- The original prompt hash is recorded in `sandbox-plan/PROMPT_ORIGINAL.sha256`.
- The replay should treat `Docs/Hermes Prompt.md` as immutable input.

## Phase 0: Verify Original Prompt

Run from `/Users/cube/Documents/00-code/HermesTrading`:

```bash
shasum -a 256 "Docs/Hermes Prompt.md"
cat sandbox-plan/PROMPT_ORIGINAL.sha256
```

Expected: both hashes match:

```text
22e414c0724f1472129f201c12397d699b9c3e6b38216e89eabde82ac3cf4bc2
```

If the hash differs, stop and inspect the prompt diff before continuing.

## Phase 1: Prepare Disposable HOME

Create the replay home:

```bash
mkdir -p /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home
mkdir -p /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading
mkdir -p /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading-config
```

Confirm it is empty except for those folders:

```bash
find /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home -maxdepth 2 -print
```

## Phase 2: Prepare Replay Environment

Use this environment for the replay:

```bash
export HOME=/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home
export HERMES_TRADING_MODE=paper
export HERMES_TRADING_I_ACCEPT_RISK=false
unset EXCHANGE_API_KEY
unset EXCHANGE_API_SECRET
unset GLASSNODE_API_KEY
unset NEWS_API_KEY
```

Confirm:

```bash
printf 'HOME=%s\nMODE=%s\nRISK=%s\n' "$HOME" "$HERMES_TRADING_MODE" "$HERMES_TRADING_I_ACCEPT_RISK"
```

Expected:

```text
HOME=/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home
MODE=paper
RISK=false
```

Recommended shortcut:

```bash
./sandbox-plan/start-replay-shell.sh
```

This opens a shell in the project with the same `HOME`, paper-mode variables, and unset exchange/news secrets.

## Phase 3: Run the Prompt With Manual Approvals

Open a fresh Claude Code session or another controlled agent session with:

- current working directory: `/Users/cube/Documents/00-code/HermesTrading`
- `HOME=/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home`
- manual approvals enabled
- no dangerous permission bypass

Paste the original prompt from:

```text
/Users/cube/Documents/00-code/HermesTrading/Docs/Hermes Prompt.md
```

When the prompt reaches Hermes installation, do not execute `curl | bash` or `irm | iex`. Instead stop at that gate and record the attempted command in `sandbox-plan/EXECUTION_LOG.md`.

Allowed first replay outcome:

- local worker scaffolded under `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading`
- paper-mode Railway test deploy attempted or completed
- deterministic fallback reflection attempted or completed
- Hermes install deferred
- no live trading

## Phase 4: Railway Constraints

Use only a disposable Railway project.

Acceptable project names:

- `hermes-trading-paper-test`
- `hermes-one-shot-replay`

Before `railway up --detach`, confirm:

```bash
railway status
```

If the selected project is not disposable, stop.

Do not set any real exchange secrets in Railway.

## Phase 5: Capture Output

After the replay stops, capture created files:

```bash
find /Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home -maxdepth 5 -type f | sort
```

Paste the output into `sandbox-plan/EXECUTION_LOG.md`.

If Railway was used, capture:

```bash
railway status
railway variables
railway logs --tail 200
```

Do not paste secrets into the log. If a command prints a secret, replace it with `[REDACTED]`.

## Phase 6: Post-Run Audit

Complete `sandbox-plan/POST_RUN_AUDIT.md`.

Required review targets:

- `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/pyproject.toml`
- `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/Dockerfile`
- `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/.env`
- `/Users/cube/Documents/00-code/HermesTrading/.sandbox/hermes-one-shot-home/hermes-trading/hermes_trading/`
- Railway variables
- Railway logs

## Phase 7: Extract Only After Audit

Only after audit passes, copy the generated worker into a durable project folder.

Do not copy:

- `.env` with secrets
- local caches
- Railway auth files
- Hermes local state
- any generated file that was not audited

## Phase 8: Hermes Later, Read-Only First

After the worker passes audit, install or run Hermes separately in Docker or on a dedicated VPS.

Initial Hermes mode:

- read-only
- cron explicit
- output Markdown proposal only
- no direct edit to `strategy.yaml`
- manual promotion required

Hermes should become a writer only after a paper-mode observation period and a separate approval.
