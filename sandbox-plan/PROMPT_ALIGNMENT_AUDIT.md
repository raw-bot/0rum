# Prompt Alignment Audit

Date: 2026-06-01

Canonical sources:

- `Docs/0rum Prompt.md`
- `Docs/0rum transcription.md`

Replay target:

- `/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading`

## Verdict

`PASS WITH FIXES`

The local sandbox matches the core architecture from the one-shot prompt: a paper-mode worker, state files, adapter contracts, score function, deterministic fallback reflection, and a future 0rum reflection mode.

It intentionally deviates from the prompt where the operator requested safer trading constraints:

- `+7%` is treated as an ambitious objective, not a risk mandate.
- Reflection evaluates every 10 closed trades but may apply no change.
- A change is allowed only when the data justifies it.
- 0rum is not allowed to trade or rewrite strategy autonomously yet.
- Railway is deferred until the local worker and dashboard are understandable.

## What Matches

- Worker state lives under `state/`.
- `goal.yaml` defines success, failure, Sharpe, and reflection cadence.
- `strategy.yaml` starts from an RSI long strategy and evolves by version.
- `loop.py` pulls price, on-chain, news, and macro data through adapters.
- Every adapter returns `schema_version`.
- `score.py` scores realised return, drawdown, and Sharpe.
- `reflect.py` has `--fallback` and `--0rum` modes.
- Paper trades are written to `trades.jsonl`.
- Reflection decisions are written to `hypotheses.jsonl`.
- Strategy history is preserved under `state/history/` when a change is applied.

## Intentional Safety Deviations

- Original prompt fallback always changes exactly one variable after the trigger. This sandbox allows no-change when evidence is weak.
- Original prompt deploys to Railway before 0rum handoff. This sandbox stays local first.
- Original prompt installs or launches 0rum late in the flow. This sandbox detects 0rum but does not hand off control yet.
- Original prompt frames 0rum as the brain watching Railway. This sandbox currently uses deterministic fallback only.
- The transcription describes a first read-only/review-only cycle before autonomous writing. We keep that as the next recommended gate.

## Current Gaps

- The dashboard must clearly show that 0rum is inactive and fallback reflection is active.
- The dashboard must show an activity log so data, signals, decisions, and paper trades are auditable.
- Historical duplicate sample trades should be visually marked as legacy data.
- The local worker should run long enough to produce fresh post-deduplication trades before Railway.
- 0rum `--0rum` mode should be tested in read-only/proposal mode before allowing writes.

## Data Sources Observed

- Price: `binance_public`
- On-chain: `blockchain_info_public`
- Macro: `stooq_public`
- News: currently `offline_fallback` when public news fetch fails

## Promotion Gate

Do not deploy to Railway until:

- local paper mode runs without duplicate trades,
- the dashboard activity log is understandable,
- fallback reflection is auditable,
- 0rum mode can produce a proposal without writing automatically,
- all safety tests and compilation checks pass.
