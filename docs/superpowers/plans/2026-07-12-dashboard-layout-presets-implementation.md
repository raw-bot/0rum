# Dashboard Layout Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three compact layout preset icons before the dashboard clock and preserve one automatically saved personal GridStack layout.

**Architecture:** Keep the feature entirely client-side. HTML owns three semantic SVG buttons, CSS reuses the existing 0rum control tokens, and one isolated JavaScript controller owns immutable GridStack coordinates, active-mode persistence, v3 migration, and programmatic-change suppression.

**Tech Stack:** Static HTML/CSS/JavaScript, GridStack v10, browser `localStorage`, Python `unittest` contract tests, in-app browser verification.

---

### Task 1: Pin the toolbar and persistence contracts with failing tests

**Files:**
- Modify: `tests/test_dashboard_terminal.py`
- Test: `tests/test_dashboard_terminal.py`

- [ ] **Step 1: Add the failing static contract test**

Add this method to `DashboardTerminalContractTests`:

```python
def test_layout_presets_are_compact_accessible_and_persistent(self):
    html = (ROOT / "orum/static/dashboard.html").read_text()
    css = (ROOT / "orum/static/dashboard.css").read_text()
    js = (ROOT / "orum/static/dashboard.js").read_text()

    self.assertLess(html.index('id="layout-presets"'), html.index('id="clock"'))
    for preset, label in (
        ("column", "Une colonne"),
        ("two-column", "Deux colonnes"),
        ("aligned-wall", "Mur aligné"),
    ):
        self.assertIn(f'data-layout-preset="{preset}"', html)
        self.assertIn(f'aria-label="{label}"', html)
    self.assertEqual(html.count('class="layout-preset-btn"'), 3)
    self.assertIn(".layout-preset-btn", css)
    self.assertIn(":focus-visible", css)
    self.assertIn("DASHBOARD_LAYOUT_PRESETS", js)
    self.assertIn('orum-dash-layout-v4-personal', js)
    self.assertIn('orum-dash-layout-v4-active', js)
    self.assertIn('orum-dash-layout-v3', js)
    self.assertIn("applyingLayoutPreset", js)
```

- [ ] **Step 2: Add the failing preset coverage test**

```python
def test_each_layout_preset_mentions_every_grid_item(self):
    html = (ROOT / "orum/static/dashboard.html").read_text()
    js = (ROOT / "orum/static/dashboard.js").read_text()
    grid_ids = re.findall(r'gs-id="([^"]+)"', html)
    self.assertEqual(len(grid_ids), 13)
    for grid_id in grid_ids:
        self.assertGreaterEqual(js.count(f'["{grid_id}",'), 3)
```

Add `import re` at the top of the test module.

- [ ] **Step 3: Run the tests and verify RED**

Run:

```bash
uv run python -m unittest \
  tests.test_dashboard_terminal.DashboardTerminalContractTests.test_layout_presets_are_compact_accessible_and_persistent \
  tests.test_dashboard_terminal.DashboardTerminalContractTests.test_each_layout_preset_mentions_every_grid_item
```

Expected: both tests fail because the toolbar and preset definitions do not yet exist.

### Task 2: Add the exact 0rum toolbar controls

**Files:**
- Modify: `orum/static/dashboard.html`
- Modify: `orum/static/dashboard.css`
- Test: `tests/test_dashboard_terminal.py`

- [ ] **Step 1: Insert semantic controls before the clock**

In `#topbar`, immediately after `#topchips`, add:

```html
<nav id="layout-presets" class="layout-presets" aria-label="Dispositions du dashboard">
  <button type="button" class="layout-preset-btn" data-layout-preset="column"
          title="Une colonne" aria-label="Une colonne" aria-pressed="false">
    <svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor">
      <rect x="2" y="2" width="12" height="3"/><rect x="2" y="6.5" width="12" height="3"/><rect x="2" y="11" width="12" height="3"/>
    </svg>
  </button>
  <button type="button" class="layout-preset-btn" data-layout-preset="two-column"
          title="Deux colonnes" aria-label="Deux colonnes" aria-pressed="false">
    <svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor">
      <rect x="1.5" y="2" width="5.5" height="5"/><rect x="9" y="2" width="5.5" height="5"/><rect x="1.5" y="9" width="5.5" height="5"/><rect x="9" y="9" width="5.5" height="5"/>
    </svg>
  </button>
  <button type="button" class="layout-preset-btn" data-layout-preset="aligned-wall"
          title="Mur aligné" aria-label="Mur aligné" aria-pressed="false">
    <svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor">
      <rect x="1" y="2" width="3.5" height="5"/><rect x="6.2" y="2" width="3.5" height="5"/><rect x="11.5" y="2" width="3.5" height="5"/><rect x="1" y="9" width="3.5" height="5"/><rect x="6.2" y="9" width="3.5" height="5"/><rect x="11.5" y="9" width="3.5" height="5"/>
    </svg>
  </button>
  <span id="layout-custom-saved" class="layout-custom-saved" title="Disposition personnelle sauvegardée" aria-label="Disposition personnelle sauvegardée"></span>
</nav>
```

- [ ] **Step 2: Add compact styles using existing tokens**

Add beside the top-bar rules:

```css
.layout-presets { display:flex; align-items:center; gap:3px; flex:0 0 auto; }
.layout-preset-btn {
  width:27px; height:25px; padding:4px; display:grid; place-items:center;
  border:1px solid var(--line); background:#25313c; color:var(--muted);
  border-radius:2px; cursor:pointer;
}
.layout-preset-btn svg { width:14px; height:14px; stroke-width:1; }
.layout-preset-btn:hover { color:var(--text); border-color:#607485; }
.layout-preset-btn:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.layout-preset-btn.active { color:var(--good); border-color:var(--good-dim); background:rgba(66,211,146,.07); }
.layout-preset-btn:disabled { opacity:.4; cursor:default; }
.layout-custom-saved { width:5px; height:5px; margin-left:2px; border-radius:50%; background:transparent; }
.layout-custom-saved.active { background:var(--accent); }
```

- [ ] **Step 3: Bump static asset versions**

Change both `dashboard.css?v=28` and `dashboard.js?v=28` to `v=29` so the restarted dashboard cannot serve cached pre-feature assets.

- [ ] **Step 4: Run the toolbar test**

Run the first Task 1 test. Expected: toolbar assertions pass; JavaScript persistence assertions remain red until Task 3.

### Task 3: Implement presets, migration, and personal autosave

**Files:**
- Modify: `orum/static/dashboard.js:1178-1210`
- Test: `tests/test_dashboard_terminal.py`

- [ ] **Step 1: Define all immutable layouts above `initGridLayout`**

Add constants for `column`, `two-column`, and `aligned-wall`. Each contains all 13 IDs and exact GridStack geometry:

```javascript
const DASHBOARD_LAYOUT_PRESETS = {
  column: [
    ["market-btc",0,0,12,8],["market-btc-utbot",0,8,12,8],["market-eth",0,16,12,8],["market-paxg",0,24,12,8],
    ["position",0,32,12,3],["stats",0,35,12,3],["research",0,38,12,5],["equity",0,43,12,3],
    ["trades",0,46,12,4],["strategy",0,50,12,4],["leverage",0,54,12,4],["external",0,58,12,4],["log",0,62,12,4],
  ],
  "two-column": [
    ["market-btc",0,0,6,8],["market-btc-utbot",6,0,6,8],["market-eth",0,8,6,8],["market-paxg",6,8,6,8],
    ["position",0,16,6,3],["stats",6,16,6,3],["research",0,19,6,5],["equity",6,19,6,5],
    ["strategy",0,24,6,4],["leverage",6,24,6,4],["trades",0,28,6,4],["external",6,28,6,4],["log",0,32,12,4],
  ],
  "aligned-wall": [
    ["market-btc",0,0,6,7],["market-btc-utbot",6,0,6,7],["market-eth",0,7,6,7],["market-paxg",6,7,6,7],
    ["position",0,14,4,4],["stats",4,14,4,4],["equity",8,14,4,4],["research",0,18,8,5],
    ["trades",8,18,4,5],["strategy",0,23,4,4],["leverage",4,23,4,4],["external",8,23,4,4],["log",0,27,12,4],
  ],
};
Object.keys(DASHBOARD_LAYOUT_PRESETS).forEach(name => {
  DASHBOARD_LAYOUT_PRESETS[name] = DASHBOARD_LAYOUT_PRESETS[name].map(([id,x,y,w,h]) => ({id,x,y,w,h}));
});
```

- [ ] **Step 2: Replace the v3-only persistence controller**

Use these keys and state:

```javascript
const PERSONAL_LAYOUT_KEY = "orum-dash-layout-v4-personal";
const ACTIVE_LAYOUT_KEY = "orum-dash-layout-v4-active";
const LEGACY_LAYOUT_KEY = "orum-dash-layout-v3";
let applyingLayoutPreset = false;
```

Add these functions inside `initGridLayout`:

```javascript
const renderLayoutPresetState = mode => {
  document.querySelectorAll("[data-layout-preset]").forEach(button => {
    const active = button.dataset.layoutPreset === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  const dot = $("layout-custom-saved");
  if (dot) dot.classList.toggle("active", mode === "custom");
};
const rerenderVisibleMarkets = () => {
  if (!lastTerminalState) return;
  TERMINAL_ASSETS.forEach(asset => {
    if (terminalVisibility[asset]) renderMarketTerminal(asset, lastTerminalState);
  });
};
const applyLayoutPreset = name => {
  const preset = DASHBOARD_LAYOUT_PRESETS[name];
  if (!preset) return;
  applyingLayoutPreset = true;
  grid.load(preset, false);
  localStorage.setItem(ACTIVE_LAYOUT_KEY, name);
  renderLayoutPresetState(name);
  requestAnimationFrame(() => {
    applyingLayoutPreset = false;
    rerenderVisibleMarkets();
  });
};
const savePersonalLayout = () => {
  if (applyingLayoutPreset) return;
  try {
    localStorage.setItem(PERSONAL_LAYOUT_KEY, JSON.stringify(grid.save(false)));
    localStorage.setItem(ACTIVE_LAYOUT_KEY, "custom");
    renderLayoutPresetState("custom");
  } catch (error) {
    console.warn("layout persistence failed:", error);
  }
};
```

- [ ] **Step 3: Preserve the existing operator layout**

At initialization:

```javascript
if (!localStorage.getItem(PERSONAL_LAYOUT_KEY)) {
  const legacy = localStorage.getItem(LEGACY_LAYOUT_KEY);
  if (legacy) localStorage.setItem(PERSONAL_LAYOUT_KEY, legacy);
}
const active = localStorage.getItem(ACTIVE_LAYOUT_KEY) ||
  (localStorage.getItem(PERSONAL_LAYOUT_KEY) ? "custom" : "two-column");
```

Restore the chosen mode with:

```javascript
if (active === "custom") {
  try {
    const personal = JSON.parse(localStorage.getItem(PERSONAL_LAYOUT_KEY) || "null");
    if (!Array.isArray(personal)) throw new Error("invalid personal layout");
    applyingLayoutPreset = true;
    grid.load(personal, false);
    applyingLayoutPreset = false;
    renderLayoutPresetState("custom");
  } catch (error) {
    localStorage.removeItem(PERSONAL_LAYOUT_KEY);
    applyLayoutPreset("two-column");
  }
} else {
  applyLayoutPreset(DASHBOARD_LAYOUT_PRESETS[active] ? active : "two-column");
}
```

- [ ] **Step 4: Bind interactions without overwriting custom state**

Bind the controls and events with:

```javascript
document.querySelectorAll("[data-layout-preset]").forEach(button => {
  button.addEventListener("click", () => applyLayoutPreset(button.dataset.layoutPreset));
});
grid.on("change", savePersonalLayout);
grid.on("dragstop", savePersonalLayout);
grid.on("resizestop", () => {
  savePersonalLayout();
  rerenderVisibleMarkets();
});
```

If GridStack is absent or initialization throws, run:

```javascript
document.querySelectorAll("[data-layout-preset]").forEach(button => {
  button.disabled = true;
});
```

- [ ] **Step 5: Update reset behavior**

Replace the reset handler with:

```javascript
const btn = $("layout-reset-btn");
if (btn) btn.addEventListener("click", () => {
  localStorage.removeItem(PERSONAL_LAYOUT_KEY);
  localStorage.removeItem(ACTIVE_LAYOUT_KEY);
  localStorage.removeItem("orum-terminal-visibility");
  applyLayoutPreset("two-column");
});
```

- [ ] **Step 6: Run focused verification**

```bash
uv run python -m unittest tests.test_dashboard_terminal
node --check orum/static/dashboard.js
```

Expected: all dashboard terminal tests pass and Node reports no syntax error.

### Task 4: Review impact and verify the live UI

**Files:**
- Verify: `orum/static/dashboard.html`
- Verify: `orum/static/dashboard.css`
- Verify: `orum/static/dashboard.js`
- Verify: `tests/test_dashboard_terminal.py`

- [ ] **Step 1: Run GitNexus impact before editing shared renderer paths**

Inspect the layout initialization and top-bar consumers. Any HIGH or CRITICAL result is reported before the edit; the implementation must not alter `renderMarketTerminal` or worker controls.

- [ ] **Step 2: Run complete verification**

```bash
uv run python -m unittest discover -s tests
node --check orum/static/dashboard.js
```

Expected: the complete suite passes.

- [ ] **Step 3: Restart only the dashboard LaunchAgent**

After tests pass, reload `com.0rum.dashboard`. Do not restart the paper engine, worker, or watcher.

- [ ] **Step 4: Browser-check every state**

At `http://127.0.0.1:8787/`, verify:

- three compact controls appear immediately before the clock;
- each preset aligns all visible cards without overlap;
- the two-column preset places BTC H4 and BTC M15/H1 side by side;
- manually resizing one card activates custom state;
- reloading restores the custom layout;
- market visibility, chart zoom, pan, and close controls still work.

## Dirty-worktree constraint

The three static dashboard files and the dashboard test file already contain
user-owned uncommitted work. Do not create implementation commits that would
silently bundle those unrelated changes. Keep the plan/spec commits isolated
and report the exact modified files after verification.
