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
let currentChartScale = "1D";

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function formatTime(ts) {
  if (!ts) return "--:--:--";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return String(ts);
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function renderChart(candles, trades) {
  const svg = document.getElementById("liveChart");
  if (!svg || !candles.length) return;
  svg.innerHTML = "";

  const width = svg.clientWidth || 800;
  const height = svg.clientHeight || 280;
  const paddingY = 30;
  const paddingLeft = 10;
  const paddingRight = 60;

  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  const closes = candles.map(c => Number(c.close));
  const timestamps = candles.map(c => Number(c.ts) || new Date(c.ts).getTime());
  
  const maxPrice = Math.max(...closes);
  const minPrice = Math.min(...closes);
  const span = Math.max(maxPrice - minPrice, 0.0001) * 1.1; 
  const yOffset = span * 0.05;
  
  const startTime = timestamps[0];
  const endTime = timestamps[timestamps.length - 1];
  const timeSpan = Math.max(endTime - startTime, 1);

  const getX = (ts) => paddingLeft + ((ts - startTime) / timeSpan) * (width - paddingLeft - paddingRight);
  const getY = (price) => height - paddingY - ((Number(price) - minPrice + yOffset) / span) * (height - paddingY * 2);

  // 0. Draw Grid & Axes (Dynamic Y-Axis on Right)
  const numGridLines = 5;
  const decimals = span < 10 ? 2 : 0;
  
  for (let i = 0; i < numGridLines; i++) {
    const py = paddingY + i * ((height - paddingY * 2) / (numGridLines - 1));
    const priceAtY = minPrice - yOffset + ((height - paddingY - py) / (height - paddingY * 2)) * span;
    
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", paddingLeft);
    line.setAttribute("x2", width - paddingRight);
    line.setAttribute("y1", py);
    line.setAttribute("y2", py);
    line.setAttribute("stroke", "rgba(0,0,0,0.05)");
    svg.appendChild(line);

    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", width - paddingRight + 8);
    text.setAttribute("y", py + 4);
    text.setAttribute("fill", "var(--text-muted)");
    text.setAttribute("font-size", "11px");
    text.textContent = priceAtY.toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals
    });
    svg.appendChild(text);
  }

  // Time labels
  const numTicks = 5;
  for (let i = 0; i < numTicks; i++) {
    if (candles.length < 2) break;
    const ts = startTime + (i / (numTicks - 1)) * timeSpan;
    const px = getX(ts);
    
    const tick = document.createElementNS("http://www.w3.org/2000/svg", "line");
    tick.setAttribute("x1", px);
    tick.setAttribute("x2", px);
    tick.setAttribute("y1", height - paddingY);
    tick.setAttribute("y2", height - paddingY + 5);
    tick.setAttribute("stroke", "rgba(0,0,0,0.1)");
    svg.appendChild(tick);

    const timeText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    timeText.setAttribute("x", px);
    timeText.setAttribute("y", height - 5);
    timeText.setAttribute("fill", "var(--text-muted)");
    timeText.setAttribute("font-size", "11px");
    
    let anchor = "middle";
    if (i === 0) anchor = "start";
    if (i === numTicks - 1) anchor = "end";
    timeText.setAttribute("text-anchor", anchor);
    
    const date = new Date(ts);
    let timeStr = date.toLocaleTimeString(undefined, {hour:"2-digit", minute:"2-digit"});
    if (timeSpan > 86400000 * 1.5) { // more than ~1.5 days
       timeStr = date.toLocaleDateString(undefined, {month:"short", day:"numeric"});
    }
    timeText.textContent = timeStr;
    svg.appendChild(timeText);
  }

  // 1. Prediction Line (Simple Moving Average simulation)
  const smaPeriod = 5;
  let predCoords = [];
  for (let i = 0; i < closes.length; i++) {
    let startIdx = Math.max(0, i - smaPeriod + 1);
    let slice = closes.slice(startIdx, i + 1);
    let avg = slice.reduce((a, b) => a + b, 0) / slice.length;
    predCoords.push([getX(timestamps[i]), getY(avg)]);
  }
  const predPathData = predCoords.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
  
  const predPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  predPath.setAttribute("d", predPathData);
  predPath.setAttribute("class", "chart-path-pred");
  svg.appendChild(predPath);

  // 2. Main Price Line (Orange)
  const priceCoords = closes.map((price, i) => [getX(timestamps[i]), getY(price)]);
  const pricePathData = priceCoords.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
  
  const pricePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  pricePath.setAttribute("d", pricePathData);
  pricePath.setAttribute("class", "chart-path-price");
  svg.appendChild(pricePath);

  // 3. Trade Markers
  if (trades && trades.length) {
    trades.forEach(trade => {
      const tradeTs = new Date(trade.ts).getTime();
      // Only draw if trade is within chart bounds
      if (tradeTs >= startTime && tradeTs <= endTime) {
        const cx = getX(tradeTs);
        const cy = getY(trade.entry_price || trade.exit_price || closes[closes.length-1]);
        const isGain = Number(trade.pnl_pct || 0) >= 0;
        
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", cx);
        circle.setAttribute("cy", cy);
        circle.setAttribute("r", "6");
        circle.setAttribute("class", isGain ? "chart-marker-buy" : "chart-marker-sell");
        svg.appendChild(circle);
      }
    });
  }
}

function renderActivity(events) {
  const list = document.getElementById("activityLog");
  if (!list) return;
  list.innerHTML = "";
  
  if (!events.length) {
    list.innerHTML = '<div class="activity-row"><span>--</span><span class="activity-desc">No recent activity</span><span></span></div>';
    return;
  }
  
  events.slice(0, 6).forEach(event => {
    const row = document.createElement("div");
    row.className = "activity-row";
    
    const isTrade = event.kind === "trade";
    let valStr = "";
    let valClass = "";
    if (isTrade) {
      valStr = signedPct(event.value);
      valClass = event.value >= 0 ? "gain" : "loss";
    } else if (typeof event.value === 'number') {
      valStr = Number(event.value).toFixed(3);
    }

    row.innerHTML = `
      <div class="activity-time">${formatTime(event.ts)}</div>
      <div class="activity-desc">${event.title || event.detail || "Event"}</div>
      <div class="activity-val ${valClass}">${valStr}</div>
    `;
    list.appendChild(row);
  });
}

async function loadState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    const state = await response.json();
    
    const portfolio = state.portfolio || {};
    const pnl = Number(portfolio.pnl_usd || 0);
    const pnlPct = Number(portfolio.pnl_pct || 0);
    
    const startBal = portfolio.starting_balance_usd || 10000;
    setText("portfolioBalance", usd(portfolio.balance_usd || startBal, 2));
    setText("portfolioSub", `Start: ${usd(startBal, 0)} | PnL: ${pnl >= 0 ? "+" : ""}${usd(pnl, 2)}`);
    setText("portfolioPnl", signedPct(pnlPct));
    const pnlEl = document.getElementById("portfolioPnl");
    if(pnlEl) pnlEl.className = `hero-value ${pnl >= 0 ? "gain" : "loss"}`;

    const guardrail = state.guardrail || {};
    setText("guardrail", guardrail.label || "Paper Mode");

    setText("asset", state.asset || "BTC/USDT");
    setText("lastPrice", Number(state.last_price || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }));
    
    const heartbeat = state.heartbeat || {};
    setText("rsi", Number(heartbeat.rsi || 0).toFixed(2));
    
    // Position details
    const position = state.open_position || {};
    const active = Boolean(position.active);
    const panel = document.getElementById("positionDetailsPanel");
    if (active) {
      setText("positionState", "Active Position");
      if(panel) panel.style.display = "flex";
      setText("positionEntry", Number(position.entry_price || 0).toLocaleString());
      const uPnl = Number(position.unrealized_pnl_usd || 0);
      setText("positionPnl", `${uPnl >= 0 ? "+" : ""}${usd(uPnl, 2)}`);
      setText("positionStop", Number(position.stop_price || 0).toLocaleString());
    } else {
      setText("positionState", "Waiting for Signal...");
      if(panel) panel.style.display = "none";
    }

    // AI Progress
    const reflection = state.reflection || {};
    const every = Number(reflection.every || 10);
    const prog = Number(reflection.progress || 0);
    setText("watcherTradesLeft", `${prog} / ${every} trades until learning`);
    const fillWidth = clamp((prog / Math.max(1, every)) * 100, 0, 100);
    const fill = document.getElementById("reflectionFill");
    if(fill) fill.style.width = `${fillWidth}%`;

    // KPIs
    setText("winRate", "0.00%");
    if(state.latest_trades && state.latest_trades.length) {
        const wins = state.latest_trades.filter(t => t.pnl_pct > 0).length;
        setText("winRate", pct(wins / state.latest_trades.length, 1));
    }
    setText("tradeCount", state.trade_count || 0);
    setText("drawdown", pct(state.drawdown, 2));
    setText("marketRegime", heartbeat.market_regime?.label || "Neutral");

    // Fetch real market data for the chart from Binance public API
    let interval = "5m";
    let limit = 120;
    if (currentChartScale === "1H") { interval = "1m"; limit = 60; }
    else if (currentChartScale === "1D") { interval = "15m"; limit = 96; }
    else if (currentChartScale === "1W") { interval = "2h"; limit = 84; }
    else if (currentChartScale === "1M") { interval = "1d"; limit = 30; }

    let chartCandles = state.candles || [];
    try {
      const pair = (state.asset || "BTC/USDT").replace("/", "");
      const res = await fetch(`https://api.binance.com/api/v3/klines?symbol=${pair}&interval=${interval}&limit=${limit}`);
      if (res.ok) {
        const klines = await res.json();
        chartCandles = klines.map(k => ({
          ts: k[6], // close time
          close: Number(k[4]) // close price
        }));
      }
    } catch (e) {
      console.warn("Could not fetch real klines, falling back to local state", e);
    }

    renderChart(chartCandles, state.latest_trades || []);
    renderActivity(state.activity || []);

  } catch (err) {
    console.error("Error loading dashboard state:", err);
  }
}

const btn = document.getElementById("reflectButton");
if (btn) {
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "Reflecting...";
    try {
      await fetch("/api/reflect", { method: "POST" });
      await loadState();
    } finally {
      btn.disabled = false;
      btn.textContent = "Run Hermes Reflection Now";
    }
  });
}

document.querySelectorAll(".scale-btn").forEach(btn => {
  btn.addEventListener("click", (e) => {
    document.querySelectorAll(".scale-btn").forEach(b => b.classList.remove("active"));
    e.target.classList.add("active");
    currentChartScale = e.target.dataset.scale;
    loadState(); // reload chart instantly
  });
});

loadState();
setInterval(loadState, 2500);
window.addEventListener('resize', loadState);
