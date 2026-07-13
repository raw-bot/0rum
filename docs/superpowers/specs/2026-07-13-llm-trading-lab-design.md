# LLM Trading Lab: auditable decisions, experiential memory, and leverage policy

Date: 2026-07-13

Status: approved by the user; observer/shadow foundation implemented

Target repository: `/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading`

## 1. Purpose

Add a paper-only LLM trading laboratory to the live 0rum dashboard. The model must reason like the supplied NoF1 reference logs: combine market structure, derivatives positioning, narrative-versus-price reaction, pain trades, alternative hypotheses, portfolio state, and explicit invalidation before deciding.

Every invocation must be visible and reconstructable. Closed decisions must be evaluated later, converted into structured lessons, and retrieved when a similar market state appears. The laboratory must compare a frozen reference LLM with an evolving LLM so that apparent learning can be measured instead of assumed.

The first operational scope is BTC/USDT, matching the existing bot. The contracts and storage formats must be symbol-agnostic so a later multi-asset perpetual portfolio does not require redesign.

## 2. Confirmed constraints

- This work belongs in the live bot repository, not the research/backtest repository.
- The existing dashboard is served on `127.0.0.1:8787`.
- No live process may be restarted without explicit user confirmation.
- No real broker or exchange order may be placed by this feature.
- Paper mode is allowed to take aggressive and account-destroying decisions.
- The LLM may choose direction, position size, leverage, stop loss, take profit, order type, horizon, and invalidation for every decision.
- Leverage is chosen per decision, not fixed by confidence bands.
- A configurable mechanical maximum remains outside the model's control.
- French retail eligibility must be shown separately from experimental paper eligibility.
- All model outputs are untrusted data and must pass deterministic schema and accounting validation.
- Secrets must come from environment variables and must never enter prompts, logs, fixtures, or dashboard responses.

## 3. Reference behaviour

The two user-provided logs contribute different parts of the target behaviour.

The first log contributes position-management discipline:

1. inspect every open position before seeking a new trade;
2. compare the current state with the original stop, target, and invalidation;
3. avoid silently changing a live thesis;
4. emit an explicit decision for every position, including HOLD;
5. copy the exact risk parameters into the executable decision.

The NoF1 log contributes market analysis:

1. raw price structure across short and long timeframes;
2. funding, open interest, liquidity, and relative volume;
3. narrative-versus-reality classification: priced in, absorption, distribution, or divergence;
4. liquidation hunting and the likely pain trade;
5. trend, mean-reversion, and microstructure hypotheses;
6. latent risks and unpriced catalysts;
7. portfolio-aware choice among entry, hold, add, reduce, close, and no trade.

The verbose `CHAIN_OF_THOUGHT` text is not copied as a contract. Instead, the bot produces a concise, explicit decision audit with the following observable stages:

`facts -> interpretation -> main thesis -> counter-thesis -> portfolio impact -> decision -> risk -> invalidation`

This audit is user-readable French text plus validated machine fields. It is not presented as hidden internal reasoning.

## 4. Delivery boundaries

The feature is delivered as one architecture in six independently verifiable slices:

1. immutable data contracts and journal;
2. OpenRouter provider and market analyst in observer/shadow mode;
3. paper decision lanes and leverage policy;
4. deterministic outcome evaluation and LLM post-mortem;
5. case retrieval and evolving challenger;
6. dashboard decision, lesson, and comparison views.

Exchange connectivity in this design is read-only market data plus the existing paper executor. Real execution is explicitly excluded.

NOFX is AGPL-3.0 and primarily Go/TypeScript. No NOFX source code will be copied into this Python repository. Its provider and risk-engine patterns may be used as architectural references. Exchange market data will be implemented through the bot's existing CCXT boundary and public exchange APIs, avoiding a license-coupled code import.

## 5. System architecture

```text
Spot candles + perp public data + macro/on-chain + timestamped headlines
                              |
                              v
                     Immutable MarketSnapshot
                              |
                 +------------+-------------+
                 |                          |
                 v                          v
          Market Analyst LLM          Similar-case retrieval
                 |                          |
                 +------------+-------------+
                              v
                      Immutable MarketBrief
                              |
                              v
                Reference Trader / Evolving Trader
                              |
                              v
                     ProposedDecision objects
                              |
                    deterministic validation
                              |
                              v
                 jurisdiction + venue + paper clamps
                              |
                              v
                       existing PaperExecutor
                              |
                              v
                 outcome evaluator and post-mortem
                              |
                              v
                     versioned Lesson records
```

The market analyst and trader use separate prompts and separate journal records. A market brief may influence several trade decisions, but it cannot be rewritten after creation.

## 6. Components

### 6.1 Market snapshot assembler

The assembler normalizes only information available at the decision cutoff:

- closed spot candles, oldest to newest;
- current price and spread timestamp;
- multi-timeframe indicators already computed by 0rum;
- public perpetual funding, open interest, mark price, and order-book summary;
- current macro and on-chain adapter outputs;
- timestamped headline evidence;
- account equity, free cash, open positions, liquidation estimates, and existing exit plans;
- decisions already emitted for the current closed candle;
- provenance and staleness for every field.

The snapshot has a stable identifier and a SHA-256 content hash. Missing or stale sources remain explicit; they are never replaced by invented neutral values.

### 6.2 Read-only exchange data

The existing CCXT provider remains the spot candle source. A separate read-only derivatives provider obtains BTC perpetual public data without credentials. It exposes a provider-neutral contract for:

- funding rate and next funding timestamp;
- open interest and observation timestamp;
- mark/index price;
- best bid/ask and bounded depth summary;
- provider status and staleness.

Failure of the derivatives source does not stop spot paper trading. It marks the field unavailable and lowers the evidence completeness shown to the model and dashboard.

### 6.3 News evidence

Version 1 uses the GDELT DOC 2.0 API as a no-key global headline source, queried for BTC, crypto-market, monetary-policy, inflation, rates, and material geopolitical terms. The existing `NEWS_API_KEY` path remains an optional configured provider. Only normalized title, publisher domain, URL, publication time, retrieval time, language, and evidence ID are supplied to the LLM.

Fetched text is untrusted evidence, never instructions. The prompt requires the model to cite supplied evidence IDs only. Raw article HTML and arbitrary web browsing are excluded from the trading call. Duplicate URLs and near-duplicate titles are collapsed. Historical replay may only use evidence whose publication timestamp is at or before the replay cutoff.

### 6.4 LLM provider

The first remote provider is OpenRouter with a configurable model, initially `deepseek/deepseek-v4-pro`. The provider contract supports later local Ollama or other remote implementations without changing the decision pipeline.

Required behaviour:

- JSON-schema-constrained output;
- explicit request timeout;
- bounded retry only for transport or parse failures;
- pinned model name in every record;
- no silent fallback to another model or provider;
- invalid or empty output becomes a logged `model_error` and no paper order;
- API keys are read from `OPENROUTER_API_KEY` and are never serialized.

### 6.5 Market analyst

The analyst produces a `MarketBrief` in French containing:

- observed facts;
- evidence completeness and freshness;
- market regime and horizons;
- bullish, bearish, neutral, or uncertain bias;
- narrative-versus-price classifications;
- likely pain trade;
- main scenario and alternatives;
- unpriced catalysts;
- evidence IDs;
- confidence;
- conditions that invalidate the brief.

Default cadence is hourly plus a refresh when a new high-priority headline arrives. A trade decision may force a refresh when the latest brief is stale.

### 6.6 Trader

Each trader receives the same snapshot and market brief. The evolving lane additionally receives retrieved lessons. The model may emit:

- `hold`;
- `open_long`;
- `open_short`;
- `add`;
- `reduce`;
- `close`.

Every decision includes:

- symbol and horizon;
- requested action and direction;
- requested notional or equity fraction;
- requested leverage;
- order type and optional limit price;
- stop loss;
- one or more take-profit levels with fractions;
- optional trailing stop and time exit;
- confidence;
- main thesis;
- counter-thesis;
- risk rationale;
- invalidation condition;
- evidence and lesson IDs used;
- a concise French decision memo.

HOLD is a complete decision. It must state why no action is preferred and what would change the decision.

### 6.7 Deterministic validator

The validator checks mechanics, not whether the trade is wise:

- schema and numeric finiteness;
- known symbol and action;
- positive size and leverage;
- side-consistent stop and target geometry;
- take-profit fractions sum to at most one;
- venue precision and minimum notional;
- available paper collateral;
- estimated liquidation is compatible with the proposed stop;
- no duplicate execution for the same lane and candle;
- snapshot and price are fresh;
- textual action agrees with structured action;
- `add`, `reduce`, and `close` agree with the actual position state.

A rejected proposal remains visible with every rejection reason.

## 7. Modes and comparison lanes

Global mode is one of:

- `off`: no LLM calls;
- `observer`: market briefs only;
- `shadow`: briefs and decisions, no paper execution;
- `paper_assisted`: LLM controls selected decision fields while the native strategy supplies the remaining fields;
- `paper_autonomous`: the LLM controls direction, size, leverage, entries, and exits.

Paper-autonomous mode contains two initial lanes:

- `llm_reference`: frozen prompt and no retrieved lessons;
- `llm_evolving`: same base prompt plus approved retrieved lessons.

The existing native paper portfolio remains the non-LLM control. A later `llm_wild` lane may auto-apply single-case lessons immediately, but it is excluded from version 1 because reference and evolving lanes are sufficient to prove the complete learning loop.

## 8. Leverage and French retail eligibility

Leverage has three distinct values:

- `requested_leverage`: selected by the LLM for this decision;
- `paper_effective_leverage`: the leverage simulated after mechanical paper and venue clamps;
- `fr_retail_eligible_leverage`: the maximum that the configured French retail profile would permit for a comparable regulated product.

The initial paper configuration is:

```yaml
llm_trading:
  paper_min_leverage: 1.0
  paper_max_leverage: 40.0
  jurisdiction_profile: fr_retail
  crypto_derivative_live_eligibility_cap: 2.0
```

The 40x paper maximum is configurable. It is not derived mechanically from model confidence. The model must explain why its selected leverage is compatible with volatility, stop distance, liquidation distance, and portfolio exposure. High confidence does not guarantee high leverage, and low confidence does not prohibit an intentionally asymmetric experiment.

The jurisdiction result never changes the paper simulation. If a model requests 20x, the paper lane may simulate 20x while the same card displays `FR retail live eligibility: 2x; excess: 18x; experimental only`.

For French non-professional clients, the AMF's CFD intervention framework uses these leverage caps by underlying class:

- major FX pairs: 30x;
- non-major FX pairs, gold, and major indices: 20x;
- commodities other than gold and non-major equity indices: 10x;
- individual equities and other reference values: 5x;
- crypto-assets: 2x.

Perpetual contracts are assessed by economic characteristics rather than marketing name. Because ESMA stated in February 2026 that many leveraged perpetuals are likely to fall within national CFD intervention measures, the `fr_retail` profile treats a crypto perpetual as 2x-eligible unless a later product-specific legal review provides a versioned override. An override requires source URL, review date, product identifier, account classification, and manually configured cap. The software does not infer professional-client status.

This is a software eligibility profile, not legal advice. Real execution remains disabled.

Official references:

- AMF, CFD leverage limits: <https://www.amf-france.org/fr/espace-epargnants/comprendre-les-produits-financiers/produits-complexes/cfd>
- AMF, choosing a crypto provider and the 2x crypto-CFD cap: <https://www.amf-france.org/fr/espace-epargnants/proteger-son-epargne/crypto-actifs-bitcoin-etc/investir-en-crypto-monnaies-quel-professionnel-choisir>
- ESMA, perpetual futures and CFD intervention measures, 24 February 2026: <https://www.esma.europa.eu/press-news/esma-news/esma-reminds-firms-their-obligations-under-cfd-product-intervention-measures>
- AMF/ACPR warning on unauthorized crypto-derivative providers, 21 April 2026: <https://www.amf-france.org/fr/actualites-publications/communiques/communiques-de-lamf/lamf-et-lacpr-mettent-en-garde-le-public-contre-plusieurs-acteurs-proposant-en-france-des-0>

## 9. Immutable journal

Version 1 uses append-only JSONL, matching the current bot's operational style. New code owns the writes and uses a single-process lock plus flush-and-fsync. The dashboard is read-only.

Files:

- `state/llm_market_briefs.jsonl`;
- `state/llm_decisions.jsonl`;
- `state/llm_outcomes.jsonl`;
- `state/llm_lessons.jsonl`.

Records are never updated in place. Outcomes and lessons refer to `decision_id`. Each record contains `schema_version`, UTC timestamps, lane, model identity, prompt version, snapshot hash, and provenance.

`llm_decisions.jsonl` stores both proposed and effective values, validation status, execution status, latency, token usage when returned by the provider, and error details. It never stores secrets or raw authorization headers.

The existing `state/trades.jsonl` is not the source of truth for learning. Outcome matching uses the unified paper ledger already used by the dashboard.

## 10. Outcome evaluation

Every actionable decision is evaluated at fixed closed-candle horizons and at trade close. Deterministic metrics are computed before any LLM post-mortem:

- return after costs;
- maximum favourable excursion;
- maximum adverse excursion;
- time to target, stop, liquidation, and close;
- realized PnL and account return;
- counterfactual HOLD return;
- counterfactual opposite-direction return;
- stop and target collision using pessimistic same-bar ordering;
- whether the main thesis or invalidation condition occurred;
- confidence calibration contribution.

The LLM post-mortem must classify the outcome without changing those metrics. Error categories include:

- wrong direction;
- correct direction, poor timing;
- stop too tight or too loose;
- target too ambitious or too conservative;
- leverage or size mismatch;
- narrative interpreted incorrectly;
- price reaction ignored;
- correlation or portfolio exposure ignored;
- regime change;
- stale or missing data;
- structured/textual inconsistency;
- execution or simulator failure rather than decision failure;
- good process with an adverse random outcome;
- bad process with a lucky profitable outcome.

## 11. Lessons and retrieval

A `Lesson` is structured, versioned, and falsifiable. It contains:

- conditions where it applies;
- observed error or success;
- proposed behavioural adjustment;
- supporting decision IDs;
- counterexamples;
- sample count;
- evidence strength;
- created and expiry timestamps;
- state: `candidate`, `active`, `rejected`, `expired`, or `superseded`.

The evolving lane retrieves at most five active cases. Version 1 uses deterministic similarity over symbol, regime, volatility bucket, side, funding sign, open-interest change bucket, narrative classification, portfolio exposure, and action. Text embeddings and vector databases are deliberately excluded from version 1.

A single loss may create a candidate lesson but cannot activate it in the evolving lane. Default activation requires either:

- two supporting completed cases with no stronger counterexample; or
- one completed case plus a replay showing improvement over the same historical decision window.

Lessons modify context supplied to the evolving trader. They do not rewrite Python, execute shell commands, alter provider credentials, or silently replace the base prompt. Prompt and schema versions remain controlled source files.

## 12. Learning comparison and promotion

Reference and evolving lanes receive identical cutoff data. Their results are compared on:

- compounded return after simulated costs;
- maximum drawdown;
- Calmar ratio;
- logarithmic growth;
- liquidation and ruin rate;
- turnover;
- confidence calibration;
- decision validity rate;
- stability by market regime.

The evolving lane may activate lessons automatically under the activation rule. It does not replace the frozen reference lane. Promotion means creating a new explicit reference version after a measured comparison; it never deletes the prior version or its history.

Rollback is selecting an earlier prompt/lesson-set version. Journal records remain immutable.

## 13. Dashboard

The dashboard adds:

1. a current market-opinion card with bias, horizons, confidence, freshness, catalysts, and source evidence;
2. a complete decision timeline including HOLD, model errors, rejections, and executions;
3. a decision detail view showing facts, interpretation, thesis, counter-thesis, requested risk, effective risk, French retail eligibility, and validation results;
4. a post-mortem view showing what happened, error classification, counterfactuals, and resulting lessons;
5. a memory view listing active, candidate, expired, and superseded lessons;
6. a portfolio comparison for native, LLM reference, and LLM evolving lanes;
7. alerts for bias flips, leverage spikes, stale evidence, model/provider changes, action/text disagreement, and reference/evolving divergence.

Every visible decision links to its snapshot hash, brief, outcomes, and lessons. There are no invisible LLM invocations.

## 14. Error handling

- Market-data failure: preserve missing source and continue only if price requirements for the selected mode remain satisfied.
- Headline failure: analyst may operate with `news unavailable`; it must not imply news awareness.
- LLM timeout or invalid JSON: journal error, emit no action, and preserve the native/reference lanes.
- Duplicate decision: reject before execution and log the original decision ID.
- Journal write failure: fail closed for the affected LLM lane; do not execute an unjournaled proposal.
- Outcome matching ambiguity: emit a reconciliation error; do not fabricate a post-mortem.
- Contradictory lesson: keep both as candidates until evidence resolves the conflict.
- OpenRouter unavailable: no silent fallback; local-model support is a separate explicitly configured provider.

## 15. Configuration

Configuration is explicit and safe by default:

```yaml
llm_trading:
  mode: off
  provider: openrouter
  model: deepseek/deepseek-v4-pro
  analyst_interval_minutes: 60
  decision_timeframe: 15m
  request_timeout_seconds: 60
  max_parse_retries: 1
  paper_min_leverage: 1.0
  paper_max_leverage: 40.0
  jurisdiction_profile: fr_retail
  reference_lane_enabled: true
  evolving_lane_enabled: true
  max_retrieved_lessons: 5
  lesson_min_support: 2
  news_provider: gdelt
  derivatives_provider: binance_usdm_public
```

The initial committed mode remains `off`. Enabling observer, shadow, or paper execution is a separate operator action. No implementation step restarts the running processes.

## 16. Verification

Each slice requires focused tests plus an end-to-end replay:

- schema round trips and malformed model outputs;
- no secret serialization;
- staleness and point-in-time cutoff tests;
- leverage requested/effective/FR-eligible separation;
- crypto 2x French retail eligibility and configurable paper 40x simulation;
- liquidation and pessimistic intrabar collision tests;
- duplicate and concurrency tests for journal writes;
- outcome-to-decision reconciliation;
- post-mortem metrics independent of LLM prose;
- lesson activation, expiry, contradiction, and retrieval ranking;
- identical inputs for reference and evolving lanes;
- dashboard snapshot and rendering tests for all statuses;
- OpenRouter contract test using a stubbed HTTP transport;
- paper replay proving no real executor method is reachable;
- regression tests for the existing native paper path when LLM mode is `off`.

No process restart is part of verification. Runtime activation and visual verification on port 8787 require separate user approval.

## 17. Rollback

- Keep `llm_trading.mode: off` as the default and immediate feature rollback.
- New journal files are additive and ignored by the native decision path.
- Each implementation slice is committed separately.
- The native paper executor and strategy path remain usable without OpenRouter or news access.
- A failed evolving experiment is rolled back by selecting the prior lesson-set version; no journal data is deleted.

## 18. Acceptance criteria

The design is satisfied when:

1. DeepSeek V4 can produce a French market brief and structured BTC paper decision through OpenRouter.
2. The LLM can request a decision-specific leverage up to the configured paper maximum.
3. A 20x paper proposal can be simulated while being visibly marked as above the 2x French retail crypto-derivative eligibility profile.
4. Every invocation, HOLD, rejection, execution, outcome, and lesson is visible and linked.
5. Closed decisions receive deterministic outcome metrics and an auditable post-mortem.
6. Similar active lessons are retrieved only for the evolving lane.
7. Reference, evolving, and native results are comparable over the same cutoff data.
8. LLM mode `off` leaves current native behaviour unchanged.
9. No real exchange order path is enabled.
10. No live process is restarted without explicit approval.
