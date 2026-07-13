# LLM trading laboratory

## Current status

The LLM laboratory is opt-in and non-executing. Its default mode is `off`; it
does not join the worker, scheduler or dashboard process and does not place or
simulate orders. It currently provides a one-shot observer/shadow research
loop around `BTC/USDT`:

1. fetch closed Binance spot candles and public Binance USD-M evidence;
2. fetch point-in-time GDELT headlines without downloading article bodies;
3. build and hash a canonical market snapshot;
4. ask `deepseek/deepseek-v4-pro` through OpenRouter for a French market brief;
5. optionally ask for two complete shadow decisions;
6. append valid responses and failures to durable JSONL journals.

The foundation intentionally refuses `paper_assisted` and
`paper_autonomous`. Those modes require a separate paper execution and outcome
learning phase with idempotency and accounting tests.

## Modes

| Mode | Snapshot | Market brief | Decisions | Position mutation |
|---|---:|---:|---:|---:|
| `off` | no | no | no | impossible |
| `observer` | yes | yes | no | impossible |
| `shadow` | yes | yes | reference + evolving | impossible |
| `paper_assisted` | refused | — | — | not installed |
| `paper_autonomous` | refused | — | — | not installed |

`off` returns before constructing a snapshot or touching any provider. The CLI
requires `--once`; no background loop is silently installed.

## Run a cycle

Use a shell environment variable for the secret. Do not put a key in YAML,
source code, documentation or a journal.

```bash
export OPENROUTER_API_KEY="..."
uv run python scripts/run_llm_lab.py --mode observer --once
uv run python scripts/run_llm_lab.py --mode shadow --once
```

An optional dedicated YAML file can tune the laboratory:

```yaml
llm_trading:
  mode: off
  provider: openrouter
  model: deepseek/deepseek-v4-pro
  analyst_interval_minutes: 60
  decision_timeframe: 15m
  request_timeout_seconds: 60
  max_parse_retries: 1
  paper_min_leverage: 1
  paper_max_leverage: 40
  jurisdiction_profile: fr_retail
  max_retrieved_lessons: 5
```

```bash
uv run python scripts/run_llm_lab.py \
  --config /absolute/path/to/llm.yaml \
  --mode shadow \
  --once
```

The explicit CLI mode overrides the YAML mode. A missing
`OPENROUTER_API_KEY` is an error only for a mode that makes a remote call.
OpenRouter provider fallback is disabled and the returned model ID must match
the configured model.

## What the model is allowed to decide

The trader is deliberately not reduced to a direction classifier. For an
entry or add proposal it chooses:

- long, short, hold, add, reduce or close;
- fraction of paper equity;
- requested leverage;
- market or limit order and limit price;
- stop loss;
- one or more take-profit levels and fractions;
- optional trailing stop and time exit;
- confidence, thesis, counter-thesis, risk rationale and invalidation;
- a plain-French memo explaining what it proposes and why.

Loss and liquidation are valid experimental outcomes. There is no hidden
formula that reduces leverage because confidence is low. Mechanical contract
validation still rejects incomplete or contradictory geometry.

The analyst separately reports facts, interpretation, market regime, multiple
horizons, narrative versus price, pain trade, main and alternate scenarios,
catalysts, confidence and invalidation. Evidence text is treated as untrusted
data, never as instructions to the model.

## Reference and evolving lanes

`shadow` evaluates the same point-in-time snapshot in two lanes:

- `llm_reference` receives no learned lessons. It is the stable comparison.
- `llm_evolving` may receive a bounded set of validated lessons when the next
  phase starts producing them.

A valid decision already recorded for the same snapshot ID and lane is reused
instead of calling the model again. This makes one-shot retries idempotent at
the decision layer. The current foundation does not yet calculate outcomes or
write lessons, so the evolving lane normally starts without memory.

## Evidence and point-in-time rules

- Candles are active Binance spot-market candles, normalized oldest to newest.
- Forming candles are removed locally and rejected again by the snapshot
  builder if their close lies after the cutoff.
- Binance USD-M funding, open interest, ticker and order-book evidence use
  public CCXT endpoints without exchange credentials.
- GDELT DOC 2.0 supplies bounded, dated headlines. URLs and normalized titles
  are deduplicated, tracking parameters are removed and article bodies are
  never fetched.
- Evidence published after the snapshot cutoff is rejected.
- Secondary source failures remain visible in the snapshot instead of being
  silently converted into invented facts.
- Macro and on-chain adapters are explicitly `not_configured` in this phase.

Every model-facing snapshot has a stable SHA-256 content hash. A journal entry
also records model, prompt version, request ID, latency, token usage and status.

## Paper leverage versus French retail eligibility

These are two independent numbers:

- `paper_effective_leverage` is the requested leverage clamped only to the
  configured experimental paper range, currently 1x–40x by default.
- `fr_retail_eligible_leverage` is an informational eligibility ceiling for
  the configured jurisdiction/product profile.

For the current conservative `fr_retail` + crypto perpetual/CFD-like profile,
the displayed eligibility is 2x. A 20x paper proposal therefore remains 20x in
the experiment and is labelled with 18x excess plus `experimental_only=true`.
The legal display never silently changes the paper experiment.

This 2x value comes from the French/European retail CFD product-intervention
framework, not from a database of French court judgments. ESMA also reminded
firms in February 2026 that leveraged perpetual futures may fall within those
CFD measures when their characteristics qualify. Product classification,
client status and provider authorization still require case-specific legal
review; the display is not legal advice and does not authorize a real trade.

Primary references:

- [AMF — contracts for difference and retail leverage](https://www.amf-france.org/fr/espace-epargnants/comprendre-les-produits-financiers/produits-complexes/cfd)
- [ESMA — reminder on perpetual futures and CFD measures, 24 February 2026](https://www.esma.europa.eu/press-news/esma-news/esma-reminds-firms-their-obligations-under-cfd-product-intervention-measures)
- [ESMA — CFD leverage limits by asset class](https://www.esma.europa.eu/press-news/esma-news/esma-adopts-final-product-intervention-measures-cfds-and-binary-options)

## Inspect decisions and errors

The CLI prints snapshot, brief and decision IDs plus each lane status. Full
verbal decisions live under the redirectable `state/` root:

```bash
tail -n 1 state/llm_market_briefs.jsonl | jq
tail -n 2 state/llm_decisions.jsonl | jq
```

Files are append-only, locked, flushed and `fsync`'d. Model/schema/provenance
errors are journaled as `model_error`, including the bounded error and raw JSON
payload when available. API keys are absent from representations and errors.

Tests or ad-hoc experiments can redirect all state before importing `orum`:

```bash
export 0RUM_STATE_DIR="$(mktemp -d)"
```

## Safety boundary and next phase

No LLM module imports the existing paper engine or paper broker, and the CLI
constructs public market clients without exchange keys. This phase can form an
opinion and propose an aggressive paper trade, but it cannot mutate balances,
positions or fills.

The next implementation phase adds a separate, idempotent paper adapter,
outcome snapshots, post-mortems, validated lesson promotion, reference versus
evolving evaluation, dashboard visibility and operator approval for assisted
mode. Real-money execution remains out of scope.

Provider references:

- [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
- [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)
- [CCXT unified public market-data API](https://github.com/ccxt/ccxt/wiki/manual)
- [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
