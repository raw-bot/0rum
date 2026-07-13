# LLM Trading Lab Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tested, paper-only observer/shadow foundation for auditable DeepSeek V4 market briefs and decisions, including immutable journals, public evidence adapters, and separate paper-versus-French-retail leverage results.

**Architecture:** Add a focused `orum.llm` package whose pure contracts and policies do not depend on the existing strategy engine. Provider adapters feed an immutable point-in-time snapshot; separate analyst and trader services call OpenRouter and journal validated outputs. The foundation stops before `PaperBroker.open`, so it cannot execute even a simulated LLM order yet.

**Tech Stack:** Python 3.12, dataclasses, jsonschema, httpx, CCXT, YAML, JSONL, pytest/unittest, OpenRouter Chat Completions, GDELT DOC 2.0.

**Design spec:** `docs/superpowers/specs/2026-07-13-llm-trading-lab-design.md`

**Worktree:** `/private/tmp/0rum-llm-trading-lab`

---

## File map

- Create `config/portfolio.yaml`: committed fallback for the static portfolio definition; runtime state may override it.
- Modify `scripts/run_paper_portfolio.py`: load state override or committed fallback without coupling tests to live state.
- Create `orum/llm/__init__.py`: public package exports only.
- Create `orum/llm/config.py`: typed LLM modes and YAML configuration parsing.
- Create `orum/llm/contracts.py`: immutable evidence, snapshot, brief, decision, and error contracts.
- Create `orum/llm/journal.py`: append-only fsynced JSONL writer and bounded reader.
- Create `orum/llm/leverage.py`: decision-specific paper clamp and French-retail eligibility calculation.
- Create `orum/llm/openrouter.py`: provider-neutral JSON completion protocol and OpenRouter implementation.
- Create `orum/llm/derivatives.py`: read-only CCXT public perpetual-market evidence.
- Create `orum/llm/news.py`: GDELT headline evidence with point-in-time filtering and deduplication.
- Create `orum/llm/snapshot.py`: canonical market snapshot and stable content hash.
- Create `orum/llm/prompts.py`: French analyst/trader prompt builders with supplied-evidence-only rules.
- Create `orum/llm/services.py`: analyst and trader orchestration, parsing, leverage annotation, and journaling.
- Create `orum/llm/runtime.py`: mode gating for off/observer/shadow with dependency injection.
- Create `scripts/run_llm_lab.py`: explicit one-shot observer/shadow command; no paper execution.
- Modify `orum/paths.py`: paths for LLM briefs and decisions journals.
- Create focused `tests/test_llm_*.py` modules corresponding to each production file.

## Task 0: Repair the reproducible portfolio configuration baseline

**Files:**
- Create: `config/portfolio.yaml`
- Modify: `scripts/run_paper_portfolio.py:24-90`
- Modify: `tests/test_run_paper_portfolio.py:1-38`

- [ ] **Step 1: Run GitNexus impact before modifying `load_config`**

Run the GitNexus impact tool with target `load_config`, file `scripts/run_paper_portfolio.py`, direction `upstream`, repository `0rum-trading`.

Expected: the runtime tests and `build_engine` are direct dependants; report HIGH or CRITICAL before editing if returned.

- [ ] **Step 2: Write the failing fallback-path test**

Add this test and import `Path` plus `TemporaryDirectory`:

```python
def test_load_config_falls_back_to_committed_config_when_state_override_is_absent(self):
    with TemporaryDirectory() as tmp:
        missing = Path(tmp) / "portfolio.yaml"
        config = run_paper_portfolio.load_config(path=missing)
    self.assertIn("strategies", config)
    self.assertIn("btc_utbot_m15_h1", {row["id"] for row in config["strategies"]})
```

- [ ] **Step 3: Run the focused test and confirm the expected failure**

Run:

```bash
uv run pytest tests/test_run_paper_portfolio.py::PaperPortfolioRuntimeTests::test_load_config_falls_back_to_committed_config_when_state_override_is_absent -v
```

Expected: FAIL because `load_config` does not accept `path` and no committed fallback exists.

- [ ] **Step 4: Add the committed portfolio config and minimal fallback**

Create `config/portfolio.yaml` with the current non-secret static configuration: starting balance 10000, candle limit 300, total stop risk 0.05, symbol stop risk 0.03, forecast gate enabled, and the four current sleeves (`btc_ak_macd_4h`, `btc_utbot_m15_h1`, `eth_donchian`, `gold_cot`) exactly as recorded in the approved runtime checkpoint.

Modify the runner with:

```python
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PORTFOLIO_PATH = ROOT_DIR / "config" / "portfolio.yaml"
PORTFOLIO_PATH = STATE_DIR / "portfolio.yaml"


def load_config(path: Path | None = None) -> dict:
    candidate = path or PORTFOLIO_PATH
    source = candidate if candidate.exists() else DEFAULT_PORTFOLIO_PATH
    return yaml.safe_load(source.read_text()) or {}
```

Do not change `PAPER_LOCK_PATH` or any state-ledger path.

- [ ] **Step 5: Verify the two pre-existing failures are fixed**

Run:

```bash
uv run pytest tests/test_run_paper_portfolio.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 6: Run the full baseline and commit**

Run:

```bash
uv run pytest -q
```

Expected: 512 tests and 7 subtests pass, with zero failures.

Run GitNexus `detect_changes` with `scope=all` and this worktree, then commit only the three task files:

```bash
git add config/portfolio.yaml scripts/run_paper_portfolio.py tests/test_run_paper_portfolio.py
git commit -m "fix: make paper portfolio config reproducible"
```

## Task 1: Add modes and immutable contracts

**Files:**
- Create: `orum/llm/__init__.py`
- Create: `orum/llm/config.py`
- Create: `orum/llm/contracts.py`
- Create: `tests/test_llm_config.py`
- Create: `tests/test_llm_contracts.py`

- [ ] **Step 1: Write failing mode and contract tests**

Create tests covering:

```python
from datetime import UTC, datetime

from orum.llm.config import LlmMode, LlmTradingConfig
from orum.llm.contracts import Evidence, MarketBrief, ProposedDecision


def test_default_config_is_off_and_aggressive_paper_cap_is_explicit():
    cfg = LlmTradingConfig.from_mapping({})
    assert cfg.mode is LlmMode.OFF
    assert cfg.paper_max_leverage == 40.0
    assert cfg.jurisdiction_profile == "fr_retail"


def test_decision_requires_visible_thesis_counter_thesis_and_invalidation():
    decision = ProposedDecision.from_mapping({
        "decision_id": "dec-1", "created_at": "2026-07-13T12:00:00+00:00",
        "lane": "llm_reference", "symbol": "BTC/USDT", "horizon": "4h",
        "action": "open_long", "equity_fraction": 0.25, "requested_leverage": 20,
        "order_type": "market", "stop_loss": 99000, "take_profits": [{"price": 105000, "fraction": 1.0}],
        "confidence": 0.72, "thesis": "Breakout supported by flow",
        "counter_thesis": "Funding is crowded", "risk_rationale": "Wide stop and event risk",
        "invalidation": "Closed H1 candle below 99000", "memo_fr": "Je tente le breakout.",
        "evidence_ids": ["ev-1"], "lesson_ids": [],
    })
    assert decision.requested_leverage == 20
    assert decision.counter_thesis == "Funding is crowded"
```

Also assert invalid actions, negative leverage, missing memo, confidence outside `[0, 1]`, and take-profit fractions above `1.0` raise `ContractError`.

- [ ] **Step 2: Run tests to verify import failures**

Run:

```bash
uv run pytest tests/test_llm_config.py tests/test_llm_contracts.py -v
```

Expected: collection FAIL because `orum.llm` does not exist.

- [ ] **Step 3: Implement typed modes and strict dataclasses**

Implement:

```python
class LlmMode(str, Enum):
    OFF = "off"
    OBSERVER = "observer"
    SHADOW = "shadow"
    PAPER_ASSISTED = "paper_assisted"
    PAPER_AUTONOMOUS = "paper_autonomous"


@dataclass(frozen=True)
class LlmTradingConfig:
    mode: LlmMode = LlmMode.OFF
    provider: str = "openrouter"
    model: str = "deepseek/deepseek-v4-pro"
    analyst_interval_minutes: int = 60
    decision_timeframe: str = "15m"
    request_timeout_seconds: float = 60.0
    max_parse_retries: int = 1
    paper_min_leverage: float = 1.0
    paper_max_leverage: float = 40.0
    jurisdiction_profile: str = "fr_retail"
    max_retrieved_lessons: int = 5

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "LlmTradingConfig":
        return cls(
            mode=LlmMode(str(value.get("mode", "off"))),
            provider=str(value.get("provider", "openrouter")),
            model=str(value.get("model", "deepseek/deepseek-v4-pro")),
            analyst_interval_minutes=int(value.get("analyst_interval_minutes", 60)),
            decision_timeframe=str(value.get("decision_timeframe", "15m")),
            request_timeout_seconds=float(value.get("request_timeout_seconds", 60)),
            max_parse_retries=int(value.get("max_parse_retries", 1)),
            paper_min_leverage=float(value.get("paper_min_leverage", 1)),
            paper_max_leverage=float(value.get("paper_max_leverage", 40)),
            jurisdiction_profile=str(value.get("jurisdiction_profile", "fr_retail")),
            max_retrieved_lessons=int(value.get("max_retrieved_lessons", 5)),
        )
```

In `contracts.py`, define frozen `Evidence`, `MarketSnapshot`, `MarketBrief`, `TakeProfit`, and `ProposedDecision` dataclasses plus `ContractError`. Each `from_mapping` must reject unknown enum values, non-finite numerics, missing visible rationale fields, side-inconsistent stop/target geometry, and total take-profit fractions above one. Each contract exposes `to_mapping()` that returns JSON-safe primitives.

- [ ] **Step 4: Run focused tests and commit**

Run:

```bash
uv run pytest tests/test_llm_config.py tests/test_llm_contracts.py -v
```

Expected: PASS.

Commit:

```bash
git add orum/llm/__init__.py orum/llm/config.py orum/llm/contracts.py tests/test_llm_config.py tests/test_llm_contracts.py
git commit -m "feat: define LLM trading contracts"
```

## Task 2: Add append-only decision journals

**Files:**
- Create: `orum/llm/journal.py`
- Modify: `orum/paths.py`
- Create: `tests/test_llm_journal.py`

- [ ] **Step 1: Run GitNexus impact for `orum/paths.py` constants**

Use GitNexus context and upstream impact on `STATE_DIR`. Report the result before editing because every operational file path depends on it. This task only adds constants; it must not alter `STATE_DIR` resolution.

- [ ] **Step 2: Write failing durability and bounded-read tests**

```python
def test_journal_appends_without_rewriting_prior_records(tmp_path):
    journal = JsonlJournal(tmp_path / "decisions.jsonl")
    journal.append({"decision_id": "one", "status": "valid"})
    first = (tmp_path / "decisions.jsonl").read_bytes()
    journal.append({"decision_id": "two", "status": "rejected"})
    assert (tmp_path / "decisions.jsonl").read_bytes().startswith(first)
    assert [row["decision_id"] for row in journal.read(limit=1)] == ["two"]


def test_journal_rejects_non_object_and_non_json_values(tmp_path):
    journal = JsonlJournal(tmp_path / "decisions.jsonl")
    with pytest.raises(JournalError):
        journal.append({"bad": float("nan")})
```

- [ ] **Step 3: Implement journal and paths**

Add path constants:

```python
LLM_MARKET_BRIEFS_PATH = STATE_DIR / "llm_market_briefs.jsonl"
LLM_DECISIONS_PATH = STATE_DIR / "llm_decisions.jsonl"
LLM_OUTCOMES_PATH = STATE_DIR / "llm_outcomes.jsonl"
LLM_LESSONS_PATH = STATE_DIR / "llm_lessons.jsonl"
```

Implement `JsonlJournal.append()` with `json.dumps(..., allow_nan=False, sort_keys=True)`, a module-level lock keyed by resolved path, `flush()`, and `os.fsync()`. `read(limit)` skips blank lines, raises `JournalError` on corrupt JSON, and returns newest bounded records in chronological order.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/test_llm_journal.py tests/test_fsio_and_lock.py -v
```

Expected: PASS.

Run GitNexus detect-changes, then commit:

```bash
git add orum/llm/journal.py orum/paths.py tests/test_llm_journal.py
git commit -m "feat: add immutable LLM journals"
```

## Task 3: Implement decision-specific leverage and French eligibility

**Files:**
- Create: `orum/llm/leverage.py`
- Create: `tests/test_llm_leverage.py`

- [ ] **Step 1: Write the failing policy tests**

```python
def test_twenty_x_paper_trade_stays_twenty_x_but_is_only_two_x_fr_eligible():
    result = apply_leverage_policy(
        requested=20, paper_min=1, paper_max=40,
        jurisdiction_profile="fr_retail", asset_class="crypto", product_kind="perpetual",
    )
    assert result.requested == 20
    assert result.paper_effective == 20
    assert result.fr_retail_eligible == 2
    assert result.excess_over_fr_retail == 18
    assert result.experimental_only is True


def test_requested_leverage_is_mechanically_clamped_without_using_confidence():
    result = apply_leverage_policy(
        requested=80, paper_min=1, paper_max=40,
        jurisdiction_profile="fr_retail", asset_class="crypto", product_kind="perpetual",
    )
    assert result.paper_effective == 40
    assert result.fr_retail_eligible == 2
```

Also test `requested=0`, NaN, paper min greater than max, equity CFD cap 5, gold CFD cap 20, and unknown live product producing no legal eligibility rather than inventing a cap.

- [ ] **Step 2: Implement the pure policy**

Create frozen `LeverageResult` with requested, paper effective, jurisdiction cap, excess, experimental flag, clamp reasons, source profile, and evaluation date. Implement the AMF/ESMA class caps as data, not conditionals spread through the runtime:

```python
FR_RETAIL_CFD_CAPS = {
    "major_fx": 30.0,
    "non_major_fx": 20.0,
    "gold": 20.0,
    "major_index": 20.0,
    "other_commodity": 10.0,
    "non_major_equity_index": 10.0,
    "equity": 5.0,
    "other_reference": 5.0,
    "crypto": 2.0,
}
```

For `fr_retail` crypto perpetuals, use 2x eligibility by conservative classification. Never modify `paper_effective` because of the jurisdiction result.

- [ ] **Step 3: Verify and commit**

Run:

```bash
uv run pytest tests/test_llm_leverage.py -v
```

Expected: PASS.

Commit:

```bash
git add orum/llm/leverage.py tests/test_llm_leverage.py
git commit -m "feat: separate paper leverage from French eligibility"
```

## Task 4: Add the OpenRouter structured-output adapter

**Files:**
- Create: `orum/llm/openrouter.py`
- Create: `tests/test_llm_openrouter.py`

- [ ] **Step 1: Verify current official OpenRouter structured-output documentation**

Use the source-driven-development skill and OpenRouter primary documentation. Record the endpoint, `response_format` JSON schema shape, timeout behaviour, and response usage fields in comments beside the adapter tests. Do not rely on NOFX implementation code.

- [ ] **Step 2: Write failing transport-contract tests**

Use `httpx.MockTransport` to assert:

```python
def test_openrouter_pins_model_and_json_schema_without_logging_key():
    client = OpenRouterClient(
        api_key="secret-value", model="deepseek/deepseek-v4-pro",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.complete_json(system="system", user="user", schema={"type": "object"}, schema_name="brief")
    assert result.payload == {"bias": "neutral"}
    assert result.model == "deepseek/deepseek-v4-pro"
    assert "secret-value" not in repr(result)
```

Also test empty content, invalid JSON, 429, 500, missing key, and one bounded retry.

- [ ] **Step 3: Implement the provider protocol and adapter**

Define:

```python
class JsonCompletionClient(Protocol):
    def complete_json(self, *, system: str, user: str, schema: dict, schema_name: str) -> CompletionResult: ...
```

`OpenRouterClient` sends `POST https://openrouter.ai/api/v1/chat/completions`, pins the configured model, uses JSON-schema response format, sets an explicit timeout, and returns payload, actual model, latency, and optional token usage. It retries transport/429/5xx and parse failures no more than the configured retry count. It never silently changes model.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/test_llm_openrouter.py -v
```

Expected: PASS with no network access.

Commit:

```bash
git add orum/llm/openrouter.py tests/test_llm_openrouter.py
git commit -m "feat: add OpenRouter structured output client"
```

## Task 5: Add read-only perpetual evidence

**Files:**
- Create: `orum/llm/derivatives.py`
- Create: `tests/test_llm_derivatives.py`

- [ ] **Step 1: Verify CCXT public method contracts from official CCXT documentation**

Use source-driven-development. Confirm unified methods and result fields for funding rate, open interest, ticker/mark price, and order book for Binance USD-M. The adapter must use injection so tests never construct a real exchange.

- [ ] **Step 2: Write failing normalization and failure tests**

Create a fake exchange returning deterministic CCXT-shaped dictionaries. Assert `fetch("BTC/USDT:USDT")` returns normalized UTC timestamps, finite values, best bid/ask, spread, bounded depth imbalance, source `binance_usdm_public`, and no credentials. Assert one unavailable endpoint yields a partial evidence object with explicit errors rather than raising.

- [ ] **Step 3: Implement the injected adapter**

Implement `DerivativesSnapshot` and `BinanceUsdMPublicProvider(exchange=None)`. Default construction uses `ccxt.binanceusdm({"enableRateLimit": True})` with no key or secret. `fetch()` calls only public unified methods and catches method-specific exceptions into `errors`. Reject future timestamps and non-finite values.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/test_llm_derivatives.py tests/test_ccxt_provider.py -v
```

Expected: PASS.

Commit:

```bash
git add orum/llm/derivatives.py tests/test_llm_derivatives.py
git commit -m "feat: add public perpetual market evidence"
```

## Task 6: Add point-in-time GDELT headline evidence

**Files:**
- Create: `orum/llm/news.py`
- Create: `tests/test_llm_news.py`

- [ ] **Step 1: Write failing query, deduplication, and cutoff tests**

Mock the GDELT DOC response with duplicate URLs, duplicate normalized titles, an article before cutoff, and an article after cutoff. Assert only evidence published no later than cutoff is returned, newest first, with stable `evidence_id`, publisher domain, URL, title, publication/retrieval timestamps, and `untrusted_text=True`.

- [ ] **Step 2: Implement the no-key provider**

`GdeltNewsProvider.fetch(cutoff, lookback_hours=6, limit=30)` calls the DOC 2.0 ArticleList JSON endpoint with an explicit BTC/crypto/macro/geopolitical query, `sort=datedesc`, bounded records, and start/end datetimes derived from cutoff. It must deduplicate by canonical URL and normalized title, strip control characters, cap title length, and never fetch article bodies.

- [ ] **Step 3: Verify and commit**

Run:

```bash
uv run pytest tests/test_llm_news.py -v
```

Expected: PASS without network access.

Commit:

```bash
git add orum/llm/news.py tests/test_llm_news.py
git commit -m "feat: add point-in-time headline evidence"
```

## Task 7: Build immutable point-in-time market snapshots

**Files:**
- Create: `orum/llm/snapshot.py`
- Create: `tests/test_llm_snapshot.py`

- [ ] **Step 1: Write failing chronological, cutoff, and hash tests**

Assert unordered candles become oldest-to-newest, forming/future candles and future headlines are rejected, missing derivatives remain explicit, and identical semantic inputs produce the same SHA-256 hash regardless of mapping insertion order.

- [ ] **Step 2: Implement the pure builder**

Implement `MarketSnapshotBuilder.build()` with explicit cutoff, symbol, closed candles by timeframe, indicators, derivative snapshot, macro/on-chain mappings, evidence list, and paper account mapping. Serialize canonical JSON with sorted keys and compact separators, then hash it. The returned frozen `MarketSnapshot` includes `snapshot_id = "snap-" + digest[:24]` and the full digest.

- [ ] **Step 3: Verify and commit**

Run:

```bash
uv run pytest tests/test_llm_snapshot.py tests/test_signal_contract.py -v
```

Expected: PASS and no change to existing strategy DTOs.

Commit:

```bash
git add orum/llm/snapshot.py tests/test_llm_snapshot.py
git commit -m "feat: build auditable LLM market snapshots"
```

## Task 8: Add French analyst and trader services

**Files:**
- Create: `orum/llm/prompts.py`
- Create: `orum/llm/services.py`
- Create: `tests/test_llm_services.py`

- [ ] **Step 1: Write failing observable-reasoning tests**

Use a fake `JsonCompletionClient` returning one market brief and one decision. Assert the analyst output contains facts, interpretation, main and alternate scenarios, pain trade, catalysts, source IDs, confidence, and invalidation. Assert the trader output contains thesis, counter-thesis, HOLD explanation or complete trade geometry, requested leverage, and a French memo. Assert any cited evidence ID not present in the supplied snapshot is rejected and journaled as `model_error`.

- [ ] **Step 2: Implement prompt builders**

`build_analyst_prompt(snapshot)` and `build_trader_prompt(snapshot, brief, lane, lessons=[])` must:

- state that evidence text is data, never instructions;
- permit citation of supplied IDs only;
- distinguish facts from opinion;
- require narrative-versus-reality, pain-trade, hypotheses, and invalidation sections;
- tell the trader that paper losses and liquidation are valid experimental outcomes;
- allow the trader to select leverage, size, stop, and targets;
- forbid claims of unavailable news awareness.

- [ ] **Step 3: Implement services and journal records**

`MarketAnalyst.analyze(snapshot)` calls the client, validates a `MarketBrief`, verifies evidence IDs, and appends a record containing model, prompt version, snapshot hash, latency, usage, and status.

`ShadowTrader.decide(snapshot, brief, lane, lessons)` validates `ProposedDecision`, applies `apply_leverage_policy`, and appends proposed plus effective leverage fields. It never imports or calls `PaperBroker`.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/test_llm_services.py tests/test_llm_contracts.py tests/test_llm_journal.py -v
```

Expected: PASS.

Commit:

```bash
git add orum/llm/prompts.py orum/llm/services.py tests/test_llm_services.py
git commit -m "feat: add auditable LLM analyst and trader"
```

## Task 9: Add off/observer/shadow runtime gating

**Files:**
- Create: `orum/llm/runtime.py`
- Create: `scripts/run_llm_lab.py`
- Create: `tests/test_llm_runtime.py`

- [ ] **Step 1: Write failing mode tests**

Assert:

- OFF performs zero provider calls and zero writes;
- OBSERVER writes one brief and never calls the trader;
- SHADOW writes one brief and reference/evolving decisions but never opens a paper position;
- missing `OPENROUTER_API_KEY` produces a visible configuration error only when a remote call is required;
- duplicate snapshot/lane decisions are skipped by decision ID;
- all dependencies can be injected for offline tests.

- [ ] **Step 2: Implement `LlmLabRuntime.run_once`**

The runtime accepts config, snapshot factory, analyst, reference trader, evolving trader, and journals. It performs only mode gating and orchestration. It must contain no import of `PaperEngine`, `PaperBroker`, or exchange credential modules.

- [ ] **Step 3: Implement the explicit CLI**

`scripts/run_llm_lab.py` accepts `--mode observer|shadow`, `--once`, and optional `--config`. It loads YAML, constructs public data providers and OpenRouter only after validating mode, and prints decision IDs/statuses. It refuses `paper_assisted` and `paper_autonomous` in this foundation with exit code 2 and message `paper LLM execution is not installed in foundation phase`.

- [ ] **Step 4: Prove no execution path exists**

Run:

```bash
rg -n "PaperBroker|\.open\(|place_order|create_order" orum/llm scripts/run_llm_lab.py
```

Expected: no matches except the explicit test assertion string.

Run:

```bash
uv run pytest tests/test_llm_runtime.py tests/test_executor.py tests/test_paper_broker.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add orum/llm/runtime.py scripts/run_llm_lab.py tests/test_llm_runtime.py
git commit -m "feat: add observer and shadow LLM runtime"
```

## Task 10: Foundation verification and plan handoff

**Files:**
- Modify: `README.md`
- Create: `docs/llm-trading-lab.md`

- [ ] **Step 1: Document configuration and non-execution boundary**

Document environment key name without a value, default OFF mode, observer/shadow commands, journal paths, paper 40x versus French 2x display semantics, GDELT/CCXT public evidence, and the fact that no paper or real execution is reachable in this phase.

- [ ] **Step 2: Run focused safety checks**

```bash
rg -n -i "sk-[a-z0-9]|api[_-]?key\s*[:=]\s*['\"][^$]" orum tests scripts config docs
uv run pytest tests/test_llm_config.py tests/test_llm_contracts.py tests/test_llm_journal.py tests/test_llm_leverage.py tests/test_llm_openrouter.py tests/test_llm_derivatives.py tests/test_llm_news.py tests/test_llm_snapshot.py tests/test_llm_services.py tests/test_llm_runtime.py -v
```

Expected: no embedded secret; all focused tests PASS.

- [ ] **Step 3: Run the entire suite**

```bash
uv run pytest -q
```

Expected: all tests and subtests PASS.

- [ ] **Step 4: Run GitNexus change detection**

Run GitNexus `detect_changes(scope="all", worktree="/private/tmp/0rum-llm-trading-lab")`. Review every affected process. Confirm the native strategy and paper execution flows are unaffected when LLM mode is OFF.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/llm-trading-lab.md
git commit -m "docs: explain LLM observer and shadow lab"
```

- [ ] **Step 6: Prepare the next plan**

Write `docs/superpowers/plans/2026-07-13-llm-trading-lab-paper-learning.md` for validated paper execution, outcomes, post-mortems, lesson activation, and reference/evolving comparisons. Do not restart or activate any live process.
