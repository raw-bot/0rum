const pct = (value, digits = 2) => `${(Number(value || 0) * 100).toFixed(digits)}%`;
const usd = (value, digits = 2) => Number(value || 0).toLocaleString(undefined, {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: digits,
});
const signedPct = (value) => {
  const number = Number(value || 0);
  const sign = number > 0 ? "+" : "";
  return `${sign}${(number * 100).toFixed(2)}%`;
};
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const layoutStorageKey = "0rum-dashboard-window-layout-v1";

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function formatTime(ts) {
  if (!ts) return "--";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return String(ts);
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDuration(seconds) {
  const safe = Math.max(0, Number(seconds || 0));
  const minutes = Math.floor(safe / 60);
  const hours = Math.floor(minutes / 60);
  if (hours) return `${hours}h ${minutes % 60}m`;
  return `${minutes}m`;
}

function renderTrades(trades) {
  const list = document.getElementById("trades");
  list.innerHTML = "";
  if (!trades.length) {
    list.innerHTML = '<div class="trade"><span>No paper trades yet</span><span></span><span></span></div>';
    return;
  }
  for (const trade of trades.slice(0, 8)) {
    const pnl = Number(trade.pnl_pct || 0);
    const legacy = trade.signal_id ? "" : " · legacy sample";
    const row = document.createElement("div");
    row.className = "trade";
    row.innerHTML = `
      <span>${formatTime(trade.ts)} · v${trade.strategy_version || "--"}${legacy}</span>
      <span>${Number(trade.entry_price || 0).toLocaleString()} → ${Number(trade.exit_price || 0).toLocaleString()}</span>
      <strong class="${pnl >= 0 ? "gain" : "loss"}">${signedPct(pnl)}</strong>
    `;
    list.appendChild(row);
  }
}

function renderDecisions(decisions) {
  const rail = document.getElementById("decisionRail");
  rail.innerHTML = "";
  if (!decisions.length) {
    rail.innerHTML = '<div class="decision empty">No decisions recorded yet</div>';
    return;
  }
  for (const decision of decisions.slice(0, 8)) {
    const item = document.createElement("div");
    const changed = decision.decision === "changed";
    item.className = changed ? "decision changed" : "decision hold";
    item.innerHTML = `
      <div class="decision-dot"></div>
      <div>
        <strong>${changed ? `Changed ${decision.variable || "strategy"}` : "No change"}</strong>
        <p>${decision.mode || "fallback"} engine · ${decision.reason || "No reason recorded"}</p>
      </div>
      <span>${Number(decision.score || 0).toFixed(3)}</span>
    `;
    rail.appendChild(item);
  }
}

function renderCandles(candles) {
  const svg = document.getElementById("candles");
  svg.innerHTML = "";
  if (!candles.length) return;

  const width = 320;
  const height = 112;
  const padding = 12;
  const highs = candles.map((candle) => Number(candle.high));
  const lows = candles.map((candle) => Number(candle.low));
  const max = Math.max(...highs);
  const min = Math.min(...lows);
  const span = Math.max(max - min, 1);
  const step = (width - padding * 2) / Math.max(candles.length - 1, 1);
  const candleWidth = clamp(step * 0.48, 5, 14);
  const y = (price) => height - padding - ((Number(price) - min) / span) * (height - padding * 2);

  for (const [index, candle] of candles.entries()) {
    const x = padding + index * step;
    const open = y(candle.open);
    const close = y(candle.close);
    const high = y(candle.high);
    const low = y(candle.low);
    const up = Number(candle.close) >= Number(candle.open);
    const wick = document.createElementNS("http://www.w3.org/2000/svg", "line");
    wick.setAttribute("x1", x);
    wick.setAttribute("x2", x);
    wick.setAttribute("y1", high);
    wick.setAttribute("y2", low);
    wick.setAttribute("class", up ? "candle up" : "candle down");
    svg.appendChild(wick);

    const body = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    body.setAttribute("x", x - candleWidth / 2);
    body.setAttribute("y", Math.min(open, close));
    body.setAttribute("width", candleWidth);
    body.setAttribute("height", Math.max(Math.abs(close - open), 3));
    body.setAttribute("rx", 2);
    body.setAttribute("class", up ? "candle-body up" : "candle-body down");
    svg.appendChild(body);
  }
}

function renderPortfolio(portfolio) {
  const pnl = Number(portfolio.pnl_usd || 0);
  const pnlPct = Number(portfolio.pnl_pct || 0);
  setText("portfolioBalance", usd(portfolio.balance_usd || portfolio.starting_balance_usd || 10000, 2));
  setText("portfolioBase", `${usd(portfolio.starting_balance_usd || 10000, 0)} base`);
  setText("portfolioPnl", `${pnl >= 0 ? "+" : ""}${usd(pnl, 2)} · ${signedPct(pnlPct)}`);

  const fill = document.getElementById("portfolioFill");
  const magnitude = clamp(Math.abs(pnlPct) / 0.03, 0, 1) * 50;
  fill.style.width = `${magnitude}%`;
  fill.style.left = pnl >= 0 ? "50%" : `${50 - magnitude}%`;
  fill.className = pnl >= 0 ? "pnl-fill gain-fill" : "pnl-fill loss-fill";
}

function renderOpenPosition(position) {
  const active = Boolean(position?.active);
  const card = document.querySelector('[data-window="position"]');
  card.classList.toggle("is-flat", !active);
  setText("positionState", active ? `${position.direction || "long"} · ${position.asset || "BTC/USDT"}` : "No open position");

  if (!active) {
    setText("positionPnl", "$0.00 · 0.00%");
    document.getElementById("positionPnl").className = "position-pnl";
    setText("positionEntry", "--");
    setText("positionNow", "--");
    setText("positionStop", "--");
    setText("positionTake", "--");
    setText("positionSize", "--");
    setText("positionHeld", "--");
    for (const id of ["positionStopMarker", "positionEntryMarker", "positionCurrentMarker", "positionTakeMarker"]) {
      document.getElementById(id).style.left = "50%";
    }
    return;
  }

  const pnlUsd = Number(position.unrealized_pnl_usd || 0);
  const pnlPct = Number(position.unrealized_pnl_pct || 0);
  setText("positionPnl", `${pnlUsd >= 0 ? "+" : ""}${usd(pnlUsd, 2)} · ${signedPct(pnlPct)}`);
  document.getElementById("positionPnl").className = `position-pnl ${pnlUsd >= 0 ? "gain" : "loss"}`;
  setText("positionEntry", Number(position.entry_price || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  setText("positionNow", Number(position.current_price || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  setText("positionStop", Number(position.stop_price || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  setText("positionTake", Number(position.take_profit_price || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  setText("positionSize", usd(position.notional_usd || 0, 0));
  setText("positionHeld", formatDuration(position.held_seconds));

  const stop = Number(position.stop_price || 0);
  const take = Number(position.take_profit_price || 0);
  const range = Math.max(take - stop, 1);
  const marker = (price) => `${clamp(((Number(price || 0) - stop) / range) * 100, 0, 100)}%`;
  document.getElementById("positionStopMarker").style.left = "0%";
  document.getElementById("positionEntryMarker").style.left = marker(position.entry_price);
  document.getElementById("positionCurrentMarker").style.left = marker(position.current_price);
  document.getElementById("positionTakeMarker").style.left = "100%";
}

function renderEquityCurve(points) {
  const svg = document.getElementById("equityCurve");
  svg.innerHTML = "";
  if (!points.length) return;

  const width = 640;
  const height = 180;
  const padding = 18;
  const values = points.map((point) => Number(point.equity));
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = Math.max(max - min, 0.0001);
  const step = (width - padding * 2) / Math.max(points.length - 1, 1);
  const y = (value) => height - padding - ((Number(value) - min) / span) * (height - padding * 2);
  const coords = points.map((point, index) => [padding + index * step, y(point.equity)]);
  const pathData = coords.map(([x, yy], index) => `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${yy.toFixed(2)}`).join(" ");
  const areaData = `${pathData} L ${coords.at(-1)[0].toFixed(2)} ${height - padding} L ${padding} ${height - padding} Z`;

  const baseline = document.createElementNS("http://www.w3.org/2000/svg", "line");
  baseline.setAttribute("x1", padding);
  baseline.setAttribute("x2", width - padding);
  baseline.setAttribute("y1", y(1));
  baseline.setAttribute("y2", y(1));
  baseline.setAttribute("class", "equity-baseline");
  svg.appendChild(baseline);

  const area = document.createElementNS("http://www.w3.org/2000/svg", "path");
  area.setAttribute("d", areaData);
  area.setAttribute("class", "equity-area");
  svg.appendChild(area);

  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", pathData);
  path.setAttribute("class", values.at(-1) >= 1 ? "equity-path up" : "equity-path down");
  svg.appendChild(path);
}

function renderActivity(events) {
  const list = document.getElementById("activityLog");
  list.innerHTML = "";
  if (!events.length) {
    list.innerHTML = '<div class="activity-item"><span>--</span><div><b>No activity yet</b><p>Start the worker to see data, signals, decisions, and paper trades.</p></div><strong>idle</strong></div>';
    return;
  }
  for (const event of events.slice(0, 10)) {
    const item = document.createElement("div");
    item.className = `activity-item ${event.kind || ""}`;
    const numeric = typeof event.value === "number" && Number.isFinite(event.value);
    const value = numeric
      ? `<strong class="${event.value >= 0 ? "gain" : "loss"}">${event.kind === "trade" ? signedPct(event.value) : Number(event.value).toFixed(3)}</strong>`
      : "<strong>live</strong>";
    item.innerHTML = `
      <span>${formatTime(event.ts)}</span>
      <div>
        <b>${event.title || "Event"}</b>
        <p>${event.detail || "No detail recorded"}</p>
      </div>
      ${value}
    `;
    list.appendChild(item);
  }
}

function readLayout() {
  try {
    return JSON.parse(localStorage.getItem(layoutStorageKey) || "{}");
  } catch {
    return {};
  }
}

function writeLayout(layout) {
  localStorage.setItem(layoutStorageKey, JSON.stringify(layout));
}

function applyWindowPosition(panel, position) {
  const x = Number(position?.x || 0);
  const y = Number(position?.y || 0);
  panel.style.transform = `translate(${x}px, ${y}px)`;
}

function setupMovableWindows() {
  const layout = readLayout();
  let active = null;

  const beginDrag = (event, panel) => {
    event.preventDefault();
    const id = panel.dataset.window;
    const current = readLayout()[id] || { x: 0, y: 0 };
    active = {
      id,
      panel,
      startX: event.clientX,
      startY: event.clientY,
      originX: Number(current.x || 0),
      originY: Number(current.y || 0),
    };
    panel.classList.add("dragging");
  };

  const moveDrag = (event) => {
    if (!active) return;
    const next = {
      x: Math.round(active.originX + event.clientX - active.startX),
      y: Math.round(active.originY + event.clientY - active.startY),
    };
    applyWindowPosition(active.panel, next);
    const nextLayout = readLayout();
    nextLayout[active.id] = next;
    writeLayout(nextLayout);
  };

  const endDrag = () => {
    if (!active) return;
    active.panel.classList.remove("dragging");
    active = null;
  };

  for (const panel of document.querySelectorAll("[data-window]")) {
    applyWindowPosition(panel, layout[panel.dataset.window]);
    const handle = panel.querySelector(".panel-menu");

    handle.addEventListener("pointerdown", (event) => {
      beginDrag(event, panel);
      handle.setPointerCapture(event.pointerId);
    });

    handle.addEventListener("pointermove", moveDrag);
    handle.addEventListener("pointerup", endDrag);
    handle.addEventListener("pointercancel", endDrag);

    handle.addEventListener("mousedown", (event) => beginDrag(event, panel));
  }

  document.addEventListener("mousemove", moveDrag);
  document.addEventListener("mouseup", endDrag);

  document.getElementById("resetLayout").addEventListener("click", () => {
    localStorage.removeItem(layoutStorageKey);
    for (const panel of document.querySelectorAll("[data-window]")) {
      applyWindowPosition(panel, { x: 0, y: 0 });
    }
  });
}

async function loadState() {
  const response = await fetch("/api/state", { cache: "no-store" });
  const state = await response.json();
  const goal = state.goal || {};
  const strategy = state.strategy || {};
  const heartbeat = state.heartbeat || {};
  const entry = strategy.entry || {};
  const guardrail = state.guardrail || {};
  const reflection = state.reflection || {};
  const hypothesis = state.latest_hypothesis || {};
  const portfolio = state.portfolio || {};
  const engine = state.engine || {};
  const openPosition = state.open_position || {};

  setText("asset", state.asset || "BTC/USDT");
  setText("guardrail", `${guardrail.label || "Paper mode"}`);
  const guardrailPill = document.getElementById("guardrail");
  guardrailPill.style.background =
    guardrail.status === "kill" ? "#cf3151" :
    guardrail.status === "review" ? "#050505" :
    guardrail.status === "caution" ? "#f2df8f" : "#050505";
  guardrailPill.style.color = guardrail.status === "caution" ? "#10100f" : "#ffffff";

  setText("pnl", signedPct(state.pnl_compound));
  renderPortfolio(portfolio);
  renderOpenPosition(openPosition);
  setText("lastPrice", Number(state.last_price || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  setText("score", Number(state.score || 0).toFixed(3));
  setText("drawdown", pct(state.drawdown));
  setText("softDD", pct(goal.soft_drawdown, 0));
  setText("maxDD", pct(goal.max_drawdown, 0));
  setText("killDD", pct(goal.emergency_stop_drawdown, 0));
  setText("tradeCount", state.trade_count || 0);
  setText("engineStatus", `${engine.label || "Fallback only"} · ${engine.detail || "0rum inactive"}`);
  setText("rsi", Number(heartbeat.rsi || 0).toFixed(1));
  setText("priceSource", heartbeat.price_source || "--");
  setText("chartSource", heartbeat.price_source || "--");
  setText("newsSource", heartbeat.news_source || "--");
  setText("marketRegime", heartbeat.market_regime?.label || "--");
  setText("decisionAction", (heartbeat.decision_action || "--").replaceAll("_", " "));
  setText("decisionReason", heartbeat.decision_reason || "Waiting for the worker.");
  setText("updatedAt", heartbeat.ts ? new Date(heartbeat.ts).toLocaleTimeString() : "--");
  setText("strategyVersion", strategy.version || "01");
  setText("threshold", entry.threshold ?? "--");
  setText("riskUnit", strategy.position_size_r ?? "--");
  setText("stopLoss", strategy.stop_loss_pct ? `${strategy.stop_loss_pct}%` : "--");

  const scoreWidth = clamp((Number(state.score || 0) + 1) / 2, 0, 1) * 100;
  document.getElementById("scoreFill").style.width = `${scoreWidth}%`;
  const ddWidth = clamp(Number(state.drawdown || 0) / Number(goal.emergency_stop_drawdown || 0.06), 0, 1) * 100;
  document.getElementById("ddFill").style.width = `${ddWidth}%`;
  const reflectionWidth = Number(reflection.every || 0)
    ? clamp((Number(reflection.progress || 0) || Number(reflection.every || 0)) / Number(reflection.every), 0, 1) * 100
    : 0;
  document.getElementById("reflectionFill").style.width = `${reflectionWidth}%`;

  renderTrades(state.latest_trades || []);
  renderDecisions(state.decisions || []);
  renderActivity(state.activity || []);
  renderCandles(state.candles || []);
  renderEquityCurve(state.equity_curve || []);
  const equity = (state.equity_curve || []).at(-1)?.equity || 1;
  setText("equityValue", `${Number(equity).toFixed(4)}x`);
  setText("hypothesisTitle", hypothesis.changed ? `Changed ${hypothesis.variable}` : "No change applied");
  setText("hypothesisReason", hypothesis.reason || "Waiting for the first reflection.");
}

document.getElementById("reflectButton").addEventListener("click", async () => {
  const button = document.getElementById("reflectButton");
  button.disabled = true;
  button.textContent = "Reflecting...";
  try {
    await fetch("/api/reflect", { method: "POST" });
    await loadState();
  } finally {
    button.disabled = false;
    button.textContent = "Run 0rum reflection";
  }
});

setupMovableWindows();
loadState();
setInterval(loadState, 2500);
