# ADR-002: Lab → 0rum Strategy Promotion Contract

## Status
Proposed

## Date
2026-07-07

## Context
We want 0rum to improve over time by testing new strategies against the live one and
promoting a challenger only when it is genuinely better. The natural architecture
(agreed in discussion) is **two separated concerns**:

- **The Lab** — a research/strategy engine. It may be ambitious and messy: it generates
  challenger strategies, backtests them, runs anti-overfit checks, explains market
  structure, and can later pull sentiment / opportunity data. It is *quarantined* — it
  never edits live behavior.
- **0rum** — the execution arm. Thin, boring, auditable. It runs a **champion** strategy
  that is already validated (today: AK MACD 4h long-only). It does not "think".

Everything the Lab is allowed to send across the boundary is a single, one-directional,
**gated** object: a *strategy candidate* carrying its full proof dossier. This ADR defines
that object and the gate, so that "self-improvement" cannot degrade into slow overfitting
or silently break a working edge.

This is not a rewrite: 0rum's current strategy interface (`goal.yaml` / `strategy.yaml` +
`AkMacdParams`) **is** the frontier. The Lab only produces validated candidates for it.

**Scope decision:** crypto-only for now (BTC / ETH / gold-proxy PAXG — consistent with the
live bot). The contract is written asset-agnostic so an equities extension (the
suppliers/customers "opportunity map") can slot in later without changing the gate.

**Baseline the challenger must beat** (this session's measurement, AK MACD 4h long-only,
its own runner exit, BTC 4h 2021→2026): PF **1.53** full / **2.02** OOS, ~104 long trades,
per-year consistent (only 2021 red). A challenger that does not clearly beat this on OOS
does not pay its complexity.

## Decision

### 1. The frontier object — a Strategy Candidate
A candidate is an immutable, versioned artifact (JSON + human-readable report) with three
parts. Nothing else may cross the boundary.

```
candidate/
  id                : "<family>-<shortsha>-<YYYYMMDD>"     e.g. ak_macd_exit_v2-a1b3c9-20260707
  provenance:
    git_sha         : Lab commit that produced it
    data_window     : [start, end] of bars used
    search_space_N  : how many variants were tried to find this one   # for multiple-comparisons correction
    one_variable    : the SINGLE thing changed vs champion (goal.yaml one_variable_only=true)
  spec:                                                    # maps 1:1 onto the live interface
    strategy_interface : "AkMacdParams" | "goal.yaml" | ...
    params             : {...}                             # exact live-config diff vs champion
    exit               : {...}                             # exits are first-class (0rum's edge lives here)
  evidence:                                                # the proof dossier — see gate below
    {every metric the gate checks, per asset, in-sample AND OOS}
  verdict            : PASS | FAIL   (+ which gate(s) failed)
```

The `spec` must be expressible as a diff to the existing live config. If it cannot be, it
is not a candidate — it is a new project, and needs its own ADR.

### 2. The gate — a candidate may cross ONLY if it clears ALL of these
Costs are always included (0.1% RT crypto spot + realistic slippage). Every check is run
by the existing harness (`backtest.py`, `random_baseline`, `outlier_dominance_check`,
`chronological_split`) plus the MCPT permutation gate added this session.

| # | Gate | Threshold |
|---|------|-----------|
| G1 | **Beats champion, OOS** | challenger OOS objective (PF / expectancy on active windows) > champion OOS **by a margin**, not within noise |
| G2 | **Walk-forward** | positive across rolling train→test folds, not just one chronological split (never full in-sample) |
| G3 | **MCPT significance** | in-sample MCPT p ≤ **0.01**, computed against the **full search space** (`search_space_N`) — re-optimise the whole grid on each permutation, not the single winner |
| G4 | **Not outlier-dominated** | PF and total return survive dropping the top 2 trades |
| G5 | **Beats random entries** | beats ≥ **95%** of random-entry sims (same exit rule + costs) |
| G6 | **Beats mandatory benchmark** | beats Donchian-20/55 (and Supertrend 10,3) on the same window — a candidate that can't beat Donchian doesn't pay its complexity |
| G7 | **Multi-asset without re-tuning** | profitable on ≥ 2 **decorrelated** assets with identical params (BTC and gold/PAXG — not BTC+ETH, corr 0.81) |
| G8 | **Per-year consistency** | not a one-bull-run: positive in the majority of years, no catastrophic single year |
| G9 | **Minimum evidence** | ≥ 50 resolved trades in the test region; below that, "insufficient evidence", auto-FAIL |
| G10 | **One variable** | differs from champion by exactly one lever (respects `one_variable_only=true`) |

A candidate that fails any gate is archived with its dossier (negative results are kept —
they are how the Lab avoids re-testing dead ends), and never reaches the bot.

### 3. Promotion protocol — even a PASS does not auto-replace live
1. **Registry.** 0rum keeps a champion/challenger registry: current champion + every
   promoted-and-shadowed candidate, each with its frozen dossier and timestamps.
2. **Paper-forward first.** A PASS candidate runs in **paper/shadow** for a real forward
   window (length fixed a-priori, e.g. ≥ N weeks or ≥ K trades) alongside the champion.
   Backtest PASS is necessary, not sufficient — the forward window checks live/backtest
   agreement (does reality match the dossier?).
3. **Human gate.** Promotion to live capital requires **explicit operator sign-off**. The
   Lab proposes; the operator disposes. No automated live swap.
4. **One variable, reversible.** Promotion changes exactly one lever and is a config swap
   with a recorded rollback point (the previous champion stays in the registry).

### 4. What the Lab may do freely (no gate needed)
Read-only work that cannot touch live capital needs no promotion gate: the chart
explanation/annotation layer, the user-challengeable-thesis research pass, sentiment
collection, and the opportunity map. These are **context and research** outputs. If any of
them ever wants to influence entries/exits, that influence becomes a candidate and must
clear the full gate like anything else.

### 5. Observability — surfacing gate status on the dashboard
The gate must be *visible*, mirroring the existing `_guardrail_status` chip in
`orum/dashboard.py`. But the gate is a **conjunction of 10 binary gates + paper-forward**,
not a single scalar — so a lone "distance to threshold" gauge is the wrong shape (a
candidate can sit at 9/10 forever if one gate never passes). Two distinct surfaces:

- **Lab view — candidate scorecard.** Per challenger: a traffic-light per gate (G1–G10),
  which gate blocks and by how much. Rendered from the candidate's **frozen** dossier.

  ```
  ak_macd_exit_v2   ●●●●○●○●●●  8/10  BLOCKED
    G1 OOS>champ ✅  G2 walk-fwd ✅  G3 MCPT ❌ p=0.04  G4 outlier ✅  G5 >rand ✅
    G6 Donchian ✅   G7 multi-asset ❌ gold negative   G8 per-year ✅  G9 ≥50tr ✅  G10 one-var ✅
  ```

- **Live dash — promotion readiness.** Is a PASS candidate currently in paper-forward?
  Here a scalar bar *is* legitimate: `X/N weeks` or `K/M trades` complete, plus its running
  OOS-margin vs champion, plus champion health. This is the only place "approaching a
  decision" is meaningful.

**Anti-pattern (do NOT build):** a live "% to passing" meter on an *un-frozen* candidate. It
invites tuning-until-green — the exact p-hacking the gate exists to prevent. Traffic lights
on frozen dossiers + a paper-forward progress bar are safe; a live "distance to pass" gauge
on a mutable candidate is not.

**Wiring (read-only, zero live risk).** The Lab writes `state/candidate_status.json` (the 10
booleans + their values + paper-forward progress). The dashboard reads and renders it exactly
like `_guardrail_status`. Nothing on this path can alter live behavior.

## Alternatives Considered

### Let the bot self-adjust from its own live wins/losses, trade by trade
- Rejected. At ~1 trade / ~3 weeks (4h long-only), live trades are far too sparse to learn
  from; adjusting on them fits noise. The learning signal is backtests on new bars, gated;
  the live trade log is for monitoring agreement, not training.

### Auto-promote any challenger that beats the champion in backtest
- Rejected. An automated challenger that tries many variants weekly is a multiple-comparisons
  machine — it *will* find spurious winners. G3 (MCPT over the full search space) + G7
  (decorrelated multi-asset) + paper-forward + human sign-off exist precisely to stop this.

### Build one big app that both researches and executes
- Rejected. Coupling makes experimental (possibly-overfit) code able to break live capital.
  The quarantine boundary is the safety property; keep them two apps, one narrow gated pipe.

### Design the contract for equities now (opportunity map / supply chains)
- Deferred. The gate is asset-agnostic already; the equities data model is a much larger
  scope. Crypto-only first, extend later without changing the gate.

## Consequences
- The Lab can run, search, and be wrong without ever perturbing the live bot accumulating
  its 4h trades.
- "Self-improvement gated by testing" becomes concrete and enforceable: a candidate is a
  file, the gate is a checklist of existing harness checks + MCPT, promotion is human and
  reversible.
- The current live config (AK MACD 4h long-only) is the first champion; ADR-002 defines how
  anything is ever allowed to replace it.
- Negative candidates are retained as a growing map of what doesn't work.
- Open items to specify next: exact numeric margins for G1/G8, the walk-forward fold schedule,
  the paper-forward window length, and the registry file format.
