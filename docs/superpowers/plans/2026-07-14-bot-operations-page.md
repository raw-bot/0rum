# Bot Operations Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the unified paper bot and LLM paper operational surface to a read-only `/bot` page, reject non-French LLM narratives, and render OpenRouter rate limits in French.

**Architecture:** The dashboard server continues to own one bounded `/api/state` snapshot. `GET /` remains the markets console and links to `GET /bot`; `/bot` receives a dedicated static page and script that render only bot/LLM operations from that snapshot. The LLM service rejects CJK text before a brief or decision can be validated or executed; the dashboard maps that and OpenRouter 429 errors to safe French operator messages.

**Tech Stack:** Python 3 standard-library `http.server`, `pytest`, `httpx` adapter, vanilla JavaScript, existing `dashboard.css`.

---

## File structure

- Modify `orum/llm/services.py`: enforce the model-output language boundary before contracts are accepted.
- Create `orum/llm/language.py`: pure, testable CJK detection and a stable `response_not_french` error.
- Modify `orum/dashboard.py`: map bounded LLM runtime/timeline errors to French operator text and serve `/bot` plus its asset.
- Modify `orum/static/dashboard.html`: add the `BOT` link beside the layout presets and remove the LLM card.
- Modify `orum/static/dashboard.js`: remove `renderLlmLab` and its render call from the market console.
- Create `orum/static/bot.html`: read-only Bot page with a return link to the markets console.
- Create `orum/static/bot.js`: render unified-paper health/positions and the existing LLM bounded state from `/api/state`.
- Modify `orum/static/dashboard.css`: add compact reusable Bot-page classes; retain existing LLM visual classes.
- Modify `tests/test_llm_services.py`: cover refusal of CJK brief and decision payloads.
- Modify `tests/test_dashboard_llm.py`: cover French error mapping and confirm the old LLM card is absent from the markets page.
- Create `tests/test_dashboard_bot_page.py`: cover static route dispatch and Bot-page navigation/read-only surface.

### Task 1: Refuse non-French model narratives before paper execution

**Files:**
- Create: `orum/llm/language.py`
- Modify: `orum/llm/services.py:34-105, 130-210`
- Modify: `tests/test_llm_services.py`

- [ ] **Step 1: Write the failing language-boundary tests**

Add a CJK variant of the existing brief and decision fixtures. Verify the analyst journals a `model_error`, the error contains the stable operator-safe code, and a CJK decision never reaches `apply_leverage_policy` or a valid decision journal record.

```python
def test_analyst_rejects_cjk_narrative_before_a_valid_brief_is_recorded(tmp_path):
    snapshot = _snapshot()
    analyst = MarketAnalyst(
        client=FakeCompletionClient(_brief(memo_fr="价格处于下跌趋势")),
        journal=JsonlJournal(tmp_path / "briefs.jsonl"),
    )

    with pytest.raises(LlmServiceError, match="response_not_french"):
        analyst.analyze(snapshot)

    record = analyst.journal.read()[0]
    assert record["status"] == "model_error"
    assert record["brief"] is None
```

Add an equivalent `ShadowTrader` test with `memo_fr="保持空头"` and assert its only record has `status == "model_error"`.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_llm_services.py -k 'cjk or french'
```

Expected: FAIL because the model payload is currently accepted.

- [ ] **Step 3: Add the pure language guard**

Create `orum/llm/language.py` with one narrow public function. It rejects Han/Kana/Hangul ranges but makes no attempt to translate or judge French grammar.

```python
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


class ModelLanguageError(ValueError):
    """Raised when a model returns a non-French narrative."""


_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")


def ensure_no_cjk_narrative(value: object) -> None:
    if isinstance(value, str):
        if _CJK.search(value):
            raise ModelLanguageError("response_not_french")
        return
    if isinstance(value, Mapping):
        for item in value.values():
            ensure_no_cjk_narrative(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            ensure_no_cjk_narrative(item)
```

Do not inspect opaque request IDs, hashes, evidence IDs, model IDs, symbols, lane names, timestamps, or numbers. In `services.py`, call it on a copy containing only the model-owned narrative values:

```python
ensure_no_cjk_narrative({
    "brief": {
        key: raw_payload.get(key)
        for key in ("regime", "facts", "evidence_freshness", "narrative_vs_price",
                    "interpretation", "pain_trade", "main_scenario", "alternate_scenarios",
                    "catalysts", "invalidation", "memo_fr")
    }
})
```

For a decision, inspect `horizon`, `thesis`, `counter_thesis`, `risk_rationale`, `invalidation`, and `memo_fr`. Convert `ModelLanguageError` into `LlmServiceError("response_not_french")` inside each existing `try` block so the current audit path records the rejected raw payload.

- [ ] **Step 4: Run the focused language and regression tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_llm_services.py tests/test_llm_runtime.py tests/test_llm_paper_agent.py
```

Expected: PASS; CJK payloads are journaled as `model_error`, no valid brief/decision is produced, and existing French fixtures still pass.

- [ ] **Step 5: Commit the language boundary**

```bash
git add orum/llm/language.py orum/llm/services.py tests/test_llm_services.py
git commit -m "fix: reject non-French LLM narratives"
```

### Task 2: Expose French, safe LLM operator errors

**Files:**
- Modify: `orum/dashboard.py:175-214, 215-400`
- Modify: `tests/test_dashboard_llm.py`

- [ ] **Step 1: Write failing snapshot-error tests**

Add a runtime fixture with `last_error: "OpenRouter HTTP 429: Provider returned error"` and a decision journal record with `error: "response_not_french"`. Assert the snapshot contains only French messages.

```python
assert state["runtime"]["last_error"] == "Limite temporaire OpenRouter (quota ou cadence)"
assert state["timeline"][0]["error"] == "Réponse du modèle refusée : le texte doit être en français"
```

- [ ] **Step 2: Run the focused dashboard test and verify it fails**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_dashboard_llm.py -k 'operator_error'
```

Expected: FAIL because raw provider/error strings are currently returned.

- [ ] **Step 3: Implement one bounded error mapper**

Add this helper above `_llm_timeline_item` in `orum/dashboard.py` and use it for `runtime["last_error"]` and timeline `error` fields:

```python
def _llm_operator_error(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return ""
    if "OpenRouter HTTP 429" in text:
        return "Limite temporaire OpenRouter (quota ou cadence)"
    if "response_not_french" in text:
        return "Réponse du modèle refusée : le texte doit être en français"
    return text[:300]
```

Keep the raw error in the append-only journal; the snapshot is the operator-safe projection. Do not add credentials, headers, provider envelopes, or response payloads to the API.

- [ ] **Step 4: Run the focused dashboard tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_dashboard_llm.py tests/test_dashboard_state.py
```

Expected: PASS; existing bounded-state assertions remain true and the two known errors have French operator text.

- [ ] **Step 5: Commit the operator-error projection**

```bash
git add orum/dashboard.py tests/test_dashboard_llm.py
git commit -m "fix: present LLM runtime errors in French"
```

### Task 3: Add the `/bot` route and move the LLM card off the markets console

**Files:**
- Modify: `orum/dashboard.py:1700-1725`
- Modify: `orum/static/dashboard.html:16-48, 78-112`
- Modify: `orum/static/dashboard.js:1110-1255, 1280-1288`
- Create: `orum/static/bot.html`
- Create: `orum/static/bot.js`
- Modify: `orum/static/dashboard.css`
- Create: `tests/test_dashboard_bot_page.py`
- Modify: `tests/test_dashboard_llm.py`

- [ ] **Step 1: Write failing route and static-surface tests**

Create a request-capturing handler helper in `tests/test_dashboard_bot_page.py` that calls `DashboardHandler.do_GET` without binding a network port. Assert `/bot` and `/assets/bot.js` produce a `200` response with the correct content type; assert an unknown asset still produces JSON `404`.

```python
def test_bot_page_and_script_are_served():
    status, body, content_type = _get("/bot")
    assert status == 200
    assert content_type == "text/html; charset=utf-8"
    assert b'id="bot-root"' in body

    status, body, content_type = _get("/assets/bot.js")
    assert status == 200
    assert content_type == "application/javascript"
    assert b'fetch("/api/state"' in body
```

Update `test_llm_dashboard_static_surface_is_read_only_responsive_and_escapes_model_text` to assert the markets HTML contains `href="/bot"`, no longer contains `llm-lab-card`, and dashboard JS no longer calls `renderLlmLab`.

- [ ] **Step 2: Run the new static tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_dashboard_bot_page.py tests/test_dashboard_llm.py -k 'bot or static_surface'
```

Expected: FAIL because `/bot`, `bot.js`, and the topbar link do not exist.

- [ ] **Step 3: Add static route dispatch and navigation**

Extend the existing `DashboardHandler.do_GET` branches only; do not create a second server or API.

```python
elif path == "/bot":
    self._send(200, (STATIC_DIR / "bot.html").read_bytes(), "text/html; charset=utf-8")
elif path == "/assets/bot.js":
    self._send(200, (STATIC_DIR / "bot.js").read_bytes(), "application/javascript")
```

In `dashboard.html`, add the link after `#layout-presets`:

```html
<a class="topbar-link" href="/bot" aria-label="Ouvrir les opérations du bot">BOT</a>
```

Remove the `gs-id="llm-lab"` item. Delete `renderLlmLab` and its call from `dashboard.js`; no markets DOM code may reference `llm-lab-card` afterward.

- [ ] **Step 4: Build the read-only Bot page**

Create `bot.html` with the shared stylesheet and one root:

```html
<header id="topbar">
  <div class="brand">0RUM<span class="brand-sub">opérations bot</span></div>
  <a class="topbar-link" href="/">MARCHÉS</a>
  <div id="bot-clock" class="clock">--:--:--</div>
</header>
<main id="bot-root" class="bot-page" aria-live="polite"></main>
<script src="/assets/bot.js"></script>
```

Create `bot.js` as a standalone reader. It must define its own `esc`, `usd`, `pct`, `ago`, and `laneName` helpers, fetch only `/api/state`, and render these sections from existing fields:

```javascript
function renderBot(s) {
  const paper = s.paper || {};
  const worker = s.worker || {};
  const lab = s.llm_lab || {};
  $("bot-root").innerHTML = `
    <section class="bot-section" id="bot-unified"></section>
    <section class="bot-section" id="bot-llm-runtime"></section>
    <section class="bot-section" id="bot-llm-lanes"></section>
    <section class="bot-section" id="bot-llm-audit"></section>`;
  renderUnifiedPaper(paper, worker, s.legacy_audit || {});
  renderLlmRuntime(lab.runtime || {});
  renderLlmLanes(lab.accounts || {});
  renderLlmAudit(lab);
}
```

`renderUnifiedPaper` uses `paper.open_positions`, `paper.balance_usd`,
`paper.equity_usd`, and `worker`; `renderLlmLanes` uses the two account objects
and their bounded `positions`; `renderLlmAudit` uses only `opinion`,
`timeline.slice(0, 8)`, `outcomes.slice(0, 6)`, `postmortems.slice(0, 1)`, and
`lessons.slice(0, 6)`. Render all model-originated text through `esc`.

Add `.topbar-link`, `.bot-page`, `.bot-section`, `.bot-grid`, and responsive
rules to `dashboard.css`. Reuse the existing `.llm-*` classes instead of adding
a second LLM visual language. Do not add POST requests or buttons that mutate
the worker, LLM service, accounts, or journals.

- [ ] **Step 5: Run route, browser-surface, and dashboard regressions**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_dashboard_bot_page.py tests/test_dashboard_llm.py tests/test_dashboard_state.py tests/test_dashboard_terminal.py
node --check orum/static/dashboard.js
node --check orum/static/bot.js
```

Expected: PASS; `/bot` is served, the market dashboard has no LLM card, both pages use the same `/api/state`, and no Bot-page mutation endpoint exists.

- [ ] **Step 6: Commit the separate operations page**

```bash
git add orum/dashboard.py orum/static/dashboard.html orum/static/dashboard.js \
  orum/static/dashboard.css orum/static/bot.html orum/static/bot.js \
  tests/test_dashboard_bot_page.py tests/test_dashboard_llm.py
git commit -m "feat: add dedicated bot operations page"
```

### Task 4: Verify the complete feature in the isolated worktree

**Files:**
- Modify: none expected

- [ ] **Step 1: Run the full automated suite**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q orum scripts tests
/bin/bash -n scripts/install_llm_paper_agent.sh
plutil -lint config/com.0rum.llm-paper.plist
```

Expected: all tests pass; compilation and static configuration checks exit 0.

- [ ] **Step 2: Run the dashboard locally in the worktree and inspect routes**

Run in one terminal:

```bash
ORUM_STATE_DIR="$PWD/state" .venv/bin/python -m orum.dashboard --host 127.0.0.1 --port 8877
```

Run in another terminal:

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8877/
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8877/bot
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8877/api/state
```

Expected: three `200` responses. Do not start, stop, or restart the live
LaunchAgents during this verification.

- [ ] **Step 3: Review scope before the final commit**

Run:

```bash
git diff --check
git status --short
```

Expected: only the files listed in Tasks 1–3 are changed. If another user file
appears, leave it unmodified and out of the feature commit.

- [ ] **Step 4: Run GitNexus change detection and report the affected processes**

Run:

```text
gitnexus_detect_changes(repo="0rum-trading", scope="all", worktree="/private/tmp/0rum-bot-operations-page")
```

Expected: the change list is limited to the LLM service boundary and dashboard
route/static rendering. Investigate and resolve any unexpected HIGH or CRITICAL
impact before integration.

## Plan self-review

- Spec coverage: Tasks 1 and 2 implement French-only model output and French
  OpenRouter 429 messages; Task 3 implements `/bot`, topbar navigation,
  read-only unified/LLM operations, and removal of the markets LLM card; Task
  4 verifies routing and regression safety.
- No placeholders: file paths, test assertions, commands, route branches, and
  error strings are explicit. No model translation or OpenRouter retry-policy
  change is planned.
- Consistency: `response_not_french` is the only language rejection code across
  the service, journal projection, dashboard and tests. `/bot` reuses
  `/api/state` and never receives a POST route.
