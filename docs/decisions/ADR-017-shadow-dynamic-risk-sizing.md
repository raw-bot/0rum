# ADR-017: Observe Dynamic Risk Sizing Before Promotion

## Status

Accepted

## Date

2026-09-02

## Context

ADR-016 connected the unified paper portfolio to actual-stop sizing and a 5%
entry drawdown kill-switch. The operator clarified that the objective is not
to suppress rare large BTC trends: risk should be allowed to vary when a
higher success probability and payoff can be demonstrated.

The available evidence does not yet demonstrate a trade-specific probability
uplift. The live paper ledger has 27 completed positions and only five UT Bot
positions. The frozen `iso_utbot` replay has 168 completed episodes with a
36.3% net win rate; its positive expectation comes from wins averaging much
more than losses, not from frequent wins. H4 close above EMA200 did not improve
the observed win rate (36.2% above versus 36.8% below).

The prior KNN forecast was removed after a 3,700-origin out-of-sample study
found negative skill versus climatology and overconfident intervals. A new
score must not inherit the name or authority of that failed system.

An adversarial review and an external Gemini review both rejected immediate
activation. They identified small correlated samples, payoff sensitivity to
outliers, selection on already-inspected data, gap risk and arbitrary Kelly,
drawdown and notional thresholds.

## Decision

Dynamic risk is implemented as a read-only paper shadow:

- the runtime loads a versioned research artifact;
- it selects a stop-distance/legacy-2x-ATR-risk research bucket using only
  entry-time values;
- it logs the empirical-Bayes win estimate, winsorized payoff, full Kelly,
  fractional Kelly, proposed shadow risk and artifact hash;
- the proposal never reaches `PaperBroker.open` and cannot alter the applied
  `risk_pct`;
- a missing, malformed, mismatched or unmatched artifact logs a fallback equal
  to the active base risk and can never create an uplift;
- the artifact is explicitly `research_only` and `promotion_eligible: false`.

The current artifact covers only `btc_utbot_m15_h1`. It defines success as net
realized PnL after both entry and exit fees greater than zero. Position episodes
come from the isolated UT Bot replay, which has no topups. Positive stop-R is
winsorized at 4R for the payoff estimate; negative stop-R is not capped. A
100-episode global-strategy prior shrinks bucket win rates, the research Kelly
fraction is 1/10, and the proposed risk is capped at 2%. These choices are
research hypotheses, not validated production parameters.

Because all available historical periods have now informed the design, none
can be called an untouched final holdout. Promotion requires a prospective
holdout beginning after this ADR, plus a newly locked evaluation protocol.

The 5% hard entry stop from ADR-016 is replaced in the main paper account by a
continuous drawdown policy:

- below 10% current peak-to-equity drawdown: multiplier 1.0;
- from 10% to below 20%: linear reduction from 1.0 toward 0.1;
- at or above 20%: multiplier 0 and new entries halt;
- exits and protective monitoring always remain active.

This drawdown policy changes applied paper risk. The dynamic Kelly proposal
does not. The 1x BTC notional cap and one-open-BTC-position limit remain in
force; the suggested 3x relaxation is rejected until portfolio-level replay
models gap and synthetic leverage risk.

## Consequences

- At the activation drawdown near 13%, new entries are permitted at roughly
  73% of each strategy's configured base risk rather than blocked completely.
- UT Bot remains able to capture a new trend, subject to the existing one-BTC
  position and 1x BTC notional caps.
- A wide-stop proposal comparable with the recent outlier is expected to be
  smaller in the current research artifact. This is an honest ex-ante result:
  the inspected historical bucket did not show a higher win probability.
- Shadow output measures a counterfactual size on the same accepted entries;
  it does not independently prove that rejected entries would have won.
- A stop budget is not a guaranteed maximum loss. Gap, spread, fee and
  slippage stress remain required before any broker-capable design.

## Promotion Contract

Dynamic sizing stays shadow until a future ADR demonstrates all of the
following on a locked prospective holdout:

1. Episode identity and embargo rules prevent topups and overlapping signals
   from inflating sample size.
2. The event label, features, bucket boundaries, shrinkage, winsorization,
   Kelly fraction and caps were frozen before holdout observation.
3. Probability quality beats the unconditional strategy baseline using paired
   Brier score and calibration intervals.
4. Expected stop-R and log-growth improve without materially degrading Calmar,
   Ulcer Index, maximum drawdown or the contribution of the top 5% winners.
5. Replay uses next-open entries, both fees, adverse stop gaps and slippage.
6. Artifact fingerprints cover strategy parameters/code, data, execution,
   feature definitions, costs and schema version.
7. Applying the policy remains atomic with the recorded size and frozen stop.

No threshold is promoted merely because 30 live trades have accumulated. The
required effective sample size must be set before the prospective review.

## Alternatives Considered

### Increase risk because the recent trade became a large winner

Rejected. Its result was unknowable at entry. The comparable wide-stop bucket
had a lower observed win rate than the narrower bucket.

### Use raw strategy or dashboard win rate

Rejected. Win rate ignores payoff asymmetry and correlated tranches. Applied
alone, it would reduce UT Bot precisely because UT Bot is a low-hit-rate trend
follower with rare large winners.

### Activate fractional Kelly immediately

Rejected. Kelly is highly sensitive to probability and payoff error. The
research sample and already-inspected bucket selection cannot validate an
uplift.

### Raise BTC notional to 3x

Rejected for this phase. The paper account does not model borrowing, margin,
liquidation or sufficiently adverse gaps. Keeping 1x isolates the sizing study
from a simultaneous leverage experiment.

## Rollback

1. Set `dynamic_risk_shadow.enabled: false` to stop research logging.
2. Restore `entry_drawdown_kill_pct: 0.05` only if the operator again prefers
   a hard 5% entry stop; it is mutually exclusive with the drawdown scale.
3. Do not delete research journals or rewrite existing paper positions.

This ADR authorizes paper behavior only and creates no broker/live authority.
