# ADR-011: Periodic self-re-audit of the live champion

## Status

Accepted — implemented 2026-07-26. The three Open Questions below were
resolved with the simplest defensible default rather than blocking on
operator input; see "Decisions taken on implementation" for what was chosen
and why each is easy to revisit.

## Date

2026-07-26

## Context

`Docs/PROMPT_LOOP_INTEGRITY_AUDIT.md` (workshop repo) re-read 0rum's governance
architecture against an external framework on self-improving systems and found
one blind spot not covered by the workshop's `ADR-002-lab-to-bot-promotion-contract.md`
(status: Proposed there too — the G1-G10 gate table it describes is a design,
not yet wired into any registry file in this repo):

Every **challenger** is gated hard against the current champion (AK MACD 4h
long-only, baseline PF 1.53 full / 2.02 OOS) before it can be promoted. But
nothing ever re-checks the **champion itself** against fresh data. A champion
whose edge has decayed (regime change) keeps running in paper/live
indefinitely as long as no challenger has explicitly beaten it — there is no
loop that periodically asks "does the champion still hold up on its own
terms?" independent of the promotion competition.

This ADR proposes closing that gap with a **read-only, non-blocking
self-audit**, not a new gate on trading itself.

## Decision (proposed)

### Cadence
**Monthly (calendar-based)**, not trade-count-based. At the champion's
observed frequency (~1 trade / ~3 weeks per the workshop ADR-002 baseline), a
trade-count trigger (e.g. "every 10 closed trades") would take ~7 months to
fire once — too slow to be a useful drift signal. A monthly cadence is
independent of how active the market currently is.

### What gets re-checked
A **subset** of the workshop ADR-002 gate vocabulary, reasoned as
self-comparison against the champion's own frozen baseline rather than
against a challenger:

- **G1-style** — current OOS objective (PF / expectancy) on the most recent
  rolling window vs the champion's own recorded baseline (1.53 / 2.02). Not
  "beats a challenger", but "hasn't drifted materially below its own dossier".
- **G2-style** — walk-forward: the most recent rolling train→test fold stays
  positive, not just the full-window aggregate (a fold going negative can hide
  inside a still-positive aggregate).
- **G8-style** — per-year consistency, extended to include the newest
  (partial) year.

G3-G7 and G9-G10 (MCPT significance, outlier-dominance, random/benchmark
beats, multi-asset, minimum trades, one-variable) are properties of a
*candidate at promotion time* and don't apply to re-auditing an
already-running champion — re-running them monthly would not answer "is the
edge still there," so they're intentionally excluded from this loop.

### Implementation reality check
There is currently **no reusable, parameterized harness** to run this
unattended: `scripts/backtest_ak_macd_long.py` is a one-off script (no CLI
args, fetches live klines inline, no per-year or walk-forward breakdown built
in). Turning "G1/G2/G8-style checks on the freshest window" into something a
monthly job can call unattended is real engineering work, not just wiring —
this is the main cost of implementing B, not the output format below.

### Output
New file `state/champion_reaudit_status.json`, analogous in spirit to the
still-unimplemented `candidate_status.json` referenced in workshop ADR-002
§3.1 (that file does not exist yet anywhere in this repo — there is no
champion/challenger registry today, so this would be the first artifact of
that kind):

```json
{
  "audited_at": "2026-08-26T00:00:00Z",
  "window": {"start": "...", "end": "..."},
  "checks": {
    "g1_oos_objective": {"status": "pass", "current": 1.61, "baseline": 1.53},
    "g2_walk_forward":  {"status": "pass", "latest_fold_pf": 1.22},
    "g8_per_year":      {"status": "fail", "detail": "2026 negative YTD"}
  },
  "verdict": "drift_detected"
}
```

`verdict` is one of `conforming` / `drift_detected` — a narrative status, never
a numeric "days until action" or "% health" gauge (workshop ADR-002's
anti-pattern rule applies here too: a live meter invites reacting to noise on
a single bad fold).

### Dashboard surfacing
No new parallel UI system. When `verdict == "drift_detected"`, the dashboard's
existing `_llm_lab_state` alert list gains one more entry, same shape as every
other alert already handled by `orum/static/bot.js`:

```python
{"kind": "champion_drift", "level": "warning", "message": "Champion : dérive détectée sur <check>."}
```

This is a **separate, later, smaller change** than the audit computation
itself — the audit script needs zero involvement from any live process; only
this last step (teaching the dashboard to read one more file) touches
`dashboard.py` and would need the same restart discipline as change A.

### What this explicitly does NOT do
- No automatic promotion, demotion, or trading-logic change of any kind.
- No restart or modification of any live process (worker, watcher, dashboard,
  producer) to *compute* the audit — it is a standalone script writing a JSON
  file, run manually or via its own separate scheduled job, decoupled from
  `com.0rum.dashboard`.
- No new registry — this does not retroactively implement workshop ADR-002
  §3.1; it only reuses its file-format spirit for one narrow purpose.

## Open Questions — resolved with defaults on implementation
1. **Drift threshold.** Implemented as "still profitable at all"
   (profit factor ≥ 1.0) on both the full window (G1-style) and the most
   recent of 3 rolling folds (G2-style), rather than a margin against the
   1.53 historical baseline (kept in the output as context, not as the
   pass/fail bar). Reasoning: a fixed margin against 1.53 needs a variance
   estimate this v1 doesn't have; "is it still net positive" is the
   unambiguous, defensible first bar. Easy to tighten later once a few
   months of real re-audit output exist to judge normal variance from.
2. **Trigger location.** `scripts/champion_reaudit.py` is a standalone CLI,
   not registered anywhere — no `launchd` job or cron entry was created.
   Running it (and deciding the cadence infrastructure) is left as an
   explicit operator action, consistent with "never touch live processes
   without confirmation." The dashboard chip reads whatever the file last
   said, including "never run" (`not_yet_audited`).
3. **Acknowledgement.** None — self-clearing, as proposed. Each run
   overwrites `state/champion_reaudit_status.json` with only the latest
   verdict; no new ack/mute state was added.

## Implementation
- `scripts/champion_reaudit.py` — replays `state/strategy.yaml` through the
  existing `orum.dsl.backtest.simulate`/`load_history` engine (the same one
  `orum/reflect.py` already uses to gate proposed mutations), over a 60-day
  window split into 3 folds. Writes `state/champion_reaudit_status.json`.
  Below `MIN_TRADES_FOR_VERDICT` (5) trades in the window, verdict is
  `insufficient_data` rather than a guess (G9-style).
- `orum/dashboard.py` — `_champion_reaudit_status()` reads that file and
  returns a chip (`{status, label, detail, audited_at}`), wired into
  `build_snapshot()` as `"champion_reaudit"`, next to `"guardrail"`.
- `orum/static/dashboard.js` — one more chip in the top bar next to
  `guardrail`, same rendering pattern (colored by status, label + tooltip
  detail).
- Tests: `tests/test_champion_reaudit.py` (verdict logic: insufficient
  evidence, conforming, drift confined to the latest fold) and two additions
  to `tests/test_dashboard_state.py` (chip absent → `not_yet_audited`; chip
  reflects a written report).

## Consequences
- Closes the "blindness upward" gap flagged in the loop-integrity audit
  without adding any automated authority over live capital.
- Surfaces a real engineering cost (no parameterized backtest harness exists
  yet) that was invisible from the audit prompt alone.
- Adds one more read-only JSON file and one more dashboard alert kind; no
  change to trading logic, execution, or existing file formats.
