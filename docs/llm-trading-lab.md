# LLM trading laboratory

## Current status

The LLM laboratory is opt-in and isolated from the native portfolio. Its core
runtime remains a one-shot command, while the optional
`com.0rum.llm-paper` LaunchAgent invokes one autonomous paper cycle per hour.
It can observe the market, produce two fully
specified decisions, execute accepted **market** actions in two experimental
paper accounts, evaluate closed outcomes and promote bounded lessons.

It never places a real exchange order. `paper_autonomous` mutates only:

- `state/llm_reference_account.json`;
- `state/llm_evolving_account.json`;
- the append-only `state/llm_*.jsonl` laboratory journals.

It does not mutate the native `paper_positions.json`, `paper_fills.jsonl` or
strategy state. The LLM agent is independent from the authoritative
`com.0rum.paper` portfolio agent and from the retired legacy engine.

## Modes and activation

| Mode | Brief | Decisions | LLM paper mutation |
|---|---:|---:|---:|
| `off` | no | no | no |
| `observer` | yes | no | no |
| `shadow` | yes | reference + evolving | no |
| `paper_assisted` | refused | — | no merge contract exists |
| `paper_autonomous` | yes | reference + evolving | yes, isolated accounts only |

Every low-level invocation requires `--once`. Autonomous paper additionally
requires `--confirm-paper` on that invocation; confirmation is not persisted.
The hourly wrapper always supplies both flags after retrieving its credential
from macOS Keychain.

Create or update the Keychain entry interactively. `-w` is deliberately the
last option so `/usr/bin/security` prompts instead of receiving the key on the
command line:

```bash
security add-generic-password -U -a "$USER" -s "0rum-openrouter" -l "0rum OpenRouter API key" -w
```

Install and operate the paper-only hourly agent with:

```bash
./scripts/install_llm_paper_agent.sh install
./scripts/install_llm_paper_agent.sh status
./scripts/install_llm_paper_agent.sh disable
./scripts/install_llm_paper_agent.sh enable
./scripts/install_llm_paper_agent.sh uninstall
```

`disable` and `uninstall` stop future calls without deleting the isolated paper
accounts, append-only journals or Keychain entry. If the credential itself must
be removed, do so explicitly with:

```bash
security delete-generic-password -a "$USER" -s "0rum-openrouter"
```

Never paste the key into a shell command, YAML file, plist, source file,
documentation or state. The agent reads it internally and publishes only a
redacted runtime heartbeat.

The runner refuses `paper_autonomous` without the confirmation flag. It also
refuses `paper_assisted`: the native strategy does not yet define which fields
an LLM may override, so inventing a merge rule would make the control portfolio
unreliable.

## Model and configuration

The installed model is `deepseek/deepseek-v4-pro` through OpenRouter strict
structured output. Provider fallback and silent model substitution are
disabled. The low-level provider still receives `OPENROUTER_API_KEY` inside an
in-memory environment mapping; the LaunchAgent plist never contains it.

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
  paper_starting_balance_usd: 10000
  paper_fee_rate: 0.0005
  paper_maintenance_margin_rate: 0.005
  allow_stop_beyond_liquidation: false
  jurisdiction_profile: fr_retail
  max_retrieved_lessons: 5
```

```bash
uv run python scripts/run_llm_lab.py \
  --config /absolute/path/to/llm.yaml \
  --mode shadow \
  --once
```

The CLI mode overrides the YAML mode. A direct remote invocation requires an
in-memory `OPENROUTER_API_KEY`; `off` returns before provider construction or
state I/O.

## What the model decides

The model is deliberately allowed to take risk in this research environment.
For every proposal it can choose:

- `hold`, `open_long`, `open_short`, `add`, `reduce` or `close`;
- fraction of experimental paper equity;
- requested leverage, clamped only by the configured paper range;
- market or limit intent and a limit price;
- stop loss, one or more take-profit prices and fractions;
- optional trailing stop and time exit;
- confidence, thesis, counter-thesis, risk rationale and invalidation;
- a concise French memo explaining the action.

The executable v1 supports market actions. Limit intents remain visible in the
decision journal but are rejected with
`limit_order_execution_not_installed`; they are never silently filled at the
current market price. A pending-order book is required before limits can be
honestly simulated.

The system stores observable rationale, not hidden chain-of-thought. `HOLD`,
model errors, validation rejections and executions are all first-class events.

## Evidence and market opinion

One cycle builds a canonical point-in-time snapshot from:

- closed Binance spot candles for `BTC/USDT`;
- public Binance USD-M funding, open interest, ticker and order-book evidence;
- bounded, dated GDELT headlines without article-body downloads;
- the separate native/reference/evolving portfolio state.

Forming candles and evidence after the cutoff are rejected. Secondary-source
failures remain visible. Macro and on-chain evidence are explicitly
`not_configured`; the analyst must not imply that those sources were checked.

The French market brief separates facts and interpretation and records bias,
regime, horizons, narrative versus price, pain trade, scenarios, catalysts,
confidence and invalidation. Every record links model, prompt version,
snapshot ID/hash, evidence IDs, request ID, latency and usage.

## Isolated paper simulator

Each LLM lane owns an isolated-margin derivatives book. Opening size is:

```text
allocated_equity = account_equity × equity_fraction
notional = allocated_equity × paper_effective_leverage
quantity = notional / entry_price
```

The simulator supports long/short, 1x–40x by default, fees, add/reduce/close,
partial take profits, stop, time exit and an explicit isolated-liquidation
estimate. A trailing stop ratchets only after the current candle's adverse
events have been evaluated, so it cannot use that candle's future path. It
consumes closed OHLC candles only. Same-bar ambiguity is
pessimistic and deterministic:

```text
liquidation → stop → take profit
```

One decision/candle/position event is idempotent. Fills are appended before an
atomic account publication, so a retry completes or reuses the operation
without charging fees twice. A valid proposal journaled before a crash is
resumed on the next autonomous run without asking the model again, using its
server-recorded snapshot cutoff, price and candle timestamp even when the next
snapshot has changed. Journal and account publication use inter-process file
locks in addition to atomic replacement.

This is an experimental approximation, not an exchange liquidation engine.

## Outcomes, post-mortems and lessons

Final paper fills trigger deterministic metrics derived from the complete fill
ledger: cost-adjusted margin return, actual account return, MFE, MAE, actual
exit reason/time, HOLD and opposite-direction
counterfactuals, pessimistic collision result and confidence calibration.
The LLM post-mortem receives those immutable metrics and a fixed error
taxonomy; it cannot rewrite performance.

Lessons are append-only state transitions. One matching case creates a
`candidate`; two non-conflicting corroborating completed cases can create
`active`. Counterexamples can reject an active lesson. Retrieval requires a
minimum deterministic similarity and is capped at five by default. The
reference lane is evaluated but never trains or receives lessons; only the
evolving lane can train and receive matching active lessons. Historical
learning conditions come from the persisted decision and brief; unavailable
point-in-time fields stay `unknown` instead of borrowing future indicators.
Comparison compounds actual account returns inside a calendar interval where
both lanes have outcomes, otherwise it labels the coverage as missing.

## Dashboard and audit trail

`GET /api/state` includes a bounded `llm_lab` object. The existing dashboard
renders a read-only LLM card with:

- current French market opinion and freshness;
- decision/fill/rejection/error timeline, including `HOLD`;
- requested, effective paper and French-retail reference leverage;
- separate lane accounts, positions, stops and liquidation estimates;
- outcomes, post-mortems and candidate/active lessons;
- reference/evolving metrics on the shared cutoff intersection;
- alerts for stale evidence, bias/model changes, rejection, contradiction,
  leverage excess and lane divergence.

Reads are bounded and tolerate malformed JSONL tails, wrong optional types and
oversized account files. Model text is HTML escaped. The dashboard has no LLM
activation button.

Trace the journals directly when needed:

```bash
tail -n 1 state/llm_market_briefs.jsonl | jq
tail -n 20 state/llm_decisions.jsonl | jq
tail -n 20 state/llm_paper_fills.jsonl | jq
tail -n 20 state/llm_outcomes.jsonl | jq
tail -n 20 state/llm_postmortems.jsonl | jq
tail -n 20 state/llm_lessons.jsonl | jq
```

Tests and ad-hoc runs can redirect every laboratory state file before import:

```bash
export 0RUM_STATE_DIR="$(mktemp -d)"
```

## Offline replay

Replay validates unique chronological candles, aligns each decision to an
eligible candle close, and derives outcomes only after the fill loop. It never calls
OpenRouter, GDELT, Binance or current news:

```bash
uv run python scripts/replay_llm_lab.py --pretty
uv run python scripts/replay_llm_lab.py --input /absolute/path/to/fixture.json --pretty
```

The built-in acceptance fixture proves a 20x long and 40x short while keeping
the French-retail reference at 2x, duplicate rejection, pessimistic
liquidation ordering, reproducible outcomes, common-window comparison and
lesson activation after two corroborating cases. The report carries a stable
SHA-256 replay digest.

## Paper leverage versus French retail eligibility

Two independent values are always retained:

- `paper_effective_leverage`: requested leverage clamped only to the configured
  paper experiment range;
- `fr_retail_eligible_leverage`: informational product/jurisdiction reference.

For the conservative `fr_retail` + crypto perpetual/CFD-like profile, the
displayed reference is 2x. Thus a requested 20x remains 20x in paper and is
labelled `experimental_only=true`; the legal annotation never edits the
experiment.

This is based on product-intervention rules, not a database of French case law.
The AMF states a 2x limit for crypto CFDs offered to retail clients. ESMA's
24 February 2026 statement says derivatives marketed as perpetual futures are
likely to fall within national CFD measures when their characteristics meet
the CFD definition. Product classification, provider authorization and client
status remain case-specific. This software label is not legal advice and never
authorizes a real trade.

Primary references:

- [AMF — CFDs and retail leverage](https://www.amf-france.org/fr/espace-epargnants/comprendre-les-produits-financiers/produits-complexes/cfd)
- [AMF — choosing an authorized crypto provider](https://www.amf-france.org/fr/espace-epargnants/proteger-son-epargne/crypto-actifs-bitcoin-etc/investir-en-crypto-monnaies-quel-professionnel-choisir)
- [ESMA — perpetual futures and CFD measures, 24 February 2026](https://www.esma.europa.eu/press-news/esma-news/esma-reminds-firms-their-obligations-under-cfd-product-intervention-measures)

## Remaining boundaries

- Real-money execution and private exchange methods are structurally absent.
- `paper_assisted` remains refused until an ownership/merge contract with the
  native strategy exists.
- Limit-order pending state is not installed; limit proposals are rejected.
- Macro/on-chain adapters are not configured.
- No profitability claim follows from passing simulations or replay tests.

Provider references:

- [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
- [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)
- [CCXT public market-data API](https://github.com/ccxt/ccxt/wiki/manual)
- [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
