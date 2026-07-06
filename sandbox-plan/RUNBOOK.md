# 0rum One-Shot Sandbox Replay Runbook

This runbook tests the original one-shot prompt without modifying it and without giving it access to the real host environment.

## Goals

- Preserve the original prompt behavior as much as possible.
- Keep all prompt-created files inside a disposable HOME.
- Deploy only a paper-mode Railway test worker.
- Prevent global 0rum installation during the first replay.
- Audit generated code before enabling any recurring 0rum loop.

## Non-Goals

- No live trading.
- No real exchange keys.
- No global 0rum install on the first replay.
- No autonomous persistent 0rum writer.
- No direct execution with dangerous permission bypass.

## Current Project Notes

- This folder is not currently a git repository.
- The original prompt hash is recorded in `sandbox-plan/PROMPT_ORIGINAL.sha256`.
- The replay should treat `Docs/0rum Prompt.md` as immutable input.

## Phase 0: Verify Original Prompt

Run from `/Applications/0rum`:

```bash
shasum -a 256 "Docs/0rum Prompt.md"
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
mkdir -p /Applications/0rum/.sandbox/0rum-one-shot-home
mkdir -p /Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading
mkdir -p /Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading-config
```

Confirm it is empty except for those folders:

```bash
find /Applications/0rum/.sandbox/0rum-one-shot-home -maxdepth 2 -print
```

## Phase 2: Prepare Replay Environment

Use this environment for the replay:

```bash
export HOME=/Applications/0rum/.sandbox/0rum-one-shot-home
export ORUM_TRADING_MODE=paper
export ORUM_TRADING_I_ACCEPT_RISK=false
unset EXCHANGE_API_KEY
unset EXCHANGE_API_SECRET
unset GLASSNODE_API_KEY
unset NEWS_API_KEY
```

Confirm:

```bash
printf 'HOME=%s\nMODE=%s\nRISK=%s\n' "$HOME" "$ORUM_TRADING_MODE" "$ORUM_TRADING_I_ACCEPT_RISK"
```

Expected:

```text
HOME=/Applications/0rum/.sandbox/0rum-one-shot-home
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

- current working directory: `/Applications/0rum`
- `HOME=/Applications/0rum/.sandbox/0rum-one-shot-home`
- manual approvals enabled
- no dangerous permission bypass

Paste the original prompt from:

```text
/Applications/0rum/Docs/0rum Prompt.md
```

When the prompt reaches 0rum installation, do not execute `curl | bash` or `irm | iex`. Instead stop at that gate and record the attempted command in `sandbox-plan/EXECUTION_LOG.md`.

Allowed first replay outcome:

- local worker scaffolded under `/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading`
- paper-mode Railway test deploy attempted or completed
- deterministic fallback reflection attempted or completed
- 0rum install deferred
- no live trading

## Phase 4: Railway Constraints

Use only a disposable Railway project.

Acceptable project names:

- `0rum-trading-paper-test`
- `0rum-one-shot-replay`

Before `railway up --detach`, confirm:

```bash
railway status
```

If the selected project is not disposable, stop.

Do not set any real exchange secrets in Railway.

## Phase 5: Capture Output

After the replay stops, capture created files:

```bash
find /Applications/0rum/.sandbox/0rum-one-shot-home -maxdepth 5 -type f | sort
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

- `/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/pyproject.toml`
- `/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/Dockerfile`
- `/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/.env`
- `/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/orum/`
- Railway variables
- Railway logs

## Phase 7: Extract Only After Audit

Only after audit passes, copy the generated worker into a durable project folder.

Do not copy:

- `.env` with secrets
- local caches
- Railway auth files
- 0rum local state
- any generated file that was not audited

## Phase 8: 0rum Later, Read-Only First

After the worker passes audit, install or run 0rum separately in Docker or on a dedicated VPS.

Initial 0rum mode:

- read-only
- cron explicit
- output Markdown proposal only
- no direct edit to `strategy.yaml`
- manual promotion required

0rum should become a writer only after a paper-mode observation period and a separate approval.
