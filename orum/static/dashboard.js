"use strict";
// 0rum Ops Console — renders /api/state into a dense operational view with a
// candlestick price chart (real BTC), trade markers, volume, RSI, zoom/pan,
// and win/drawdown/exposure gauges. Every render is an honest live snapshot.

const $ = (id) => document.getElementById(id);
const num = (v, d = 2) => Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const usd = (v, d = 2) => "$" + num(v, d);
const pct = (v, d = 2) => `${(Number(v || 0) * 100).toFixed(d)}%`;
const signed = (v, fn) => (Number(v) > 0 ? "+" : "") + fn(v);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const cls = (v) => (Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "");
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function hhmmss(ts) {
  if (!ts) return "--:--:--";
  const d = new Date(ts);
  if (isNaN(d)) return String(ts).slice(11, 19) || "--:--:--";
  return d.toTimeString().slice(0, 8);
}
function ago(ts) {
  if (!ts) return "—";
  const s = (Date.now() - new Date(ts).getTime()) / 1000;
  if (isNaN(s)) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

/* ---- indicators computed client-side (parity with 0rum) ------------- */
function emaSeries(vals, period) {
  const out = new Array(vals.length).fill(null);
  if (vals.length < period) return out;
  const k = 2 / (period + 1);
  let prev = vals.slice(0, period).reduce((a, b) => a + b, 0) / period;
  out[period - 1] = prev;
  for (let i = period; i < vals.length; i++) { prev = (vals[i] - prev) * k + prev; out[i] = prev; }
  return out;
}
function rsiSeries(vals, period = 14) {
  const out = new Array(vals.length).fill(null);
  if (vals.length <= period) return out;
  let g = 0, l = 0;
  for (let i = 1; i <= period; i++) { const d = vals[i] - vals[i - 1]; g += Math.max(d, 0); l += Math.max(-d, 0); }
  let ag = g / period, al = l / period;
  const rsi = (a, b) => (a + b > 0 ? (100 * a) / (a + b) : 0);
  out[period] = rsi(ag, al);
  for (let i = period + 1; i < vals.length; i++) {
    const d = vals[i] - vals[i - 1];
    ag = (ag * (period - 1) + Math.max(d, 0)) / period;
    al = (al * (period - 1) + Math.max(-d, 0)) / period;
    out[i] = rsi(ag, al);
  }
  return out;
}

// ATR (Wilder) — for the SSL Hybrid continuation band, parity with AKM15.
function atrSeries(highs, lows, closes, period = 14) {
  const n = closes.length, tr = new Array(n).fill(null), out = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    tr[i] = i === 0 ? highs[i] - lows[i]
      : Math.max(highs[i] - lows[i], Math.abs(highs[i] - closes[i - 1]), Math.abs(lows[i] - closes[i - 1]));
  }
  if (n < period) return out;
  let prev = 0; for (let i = 0; i < period; i++) prev += tr[i]; prev /= period;
  out[period - 1] = prev;
  for (let i = period; i < n; i++) { prev = (prev * (period - 1) + tr[i]) / period; out[i] = prev; }
  return out;
}
// AK MACD: macd = EMA(fast)-EMA(slow), signal = EMA(macd, sig). Same as AKM15.
function macdParts(closes, fast = 12, slow = 26, sig = 9) {
  const ef = emaSeries(closes, fast), es = emaSeries(closes, slow);
  const macd = closes.map((_, i) => (ef[i] != null && es[i] != null) ? ef[i] - es[i] : null);
  const signal = new Array(closes.length).fill(null);
  const k = 2 / (sig + 1);
  let prev = null, count = 0, seed = 0;
  for (let i = 0; i < macd.length; i++) {
    if (macd[i] == null) continue;
    if (count < sig) { seed += macd[i]; count++; if (count === sig) { prev = seed / sig; signal[i] = prev; } continue; }
    prev = (macd[i] - prev) * k + prev; signal[i] = prev;
  }
  return { macd, signal };
}

/* ====================================================================== */
/* Price chart — candles + volume + AK MACD + SSL baseline + markers       */
/* ====================================================================== */
const CH = { W: 1000, H: 390, padL: 56, padR: 12, top: 10, pxB: 250, volT: 258, volB: 300, rsiT: 308, rsiB: 378 };
const P = { series: [], baseline: [], atr: [], baseColor: [], macd: [], macdSig: [], dotGreen: [], markers: [], signals: [], filter: "both", view: null, follow: true, drag: null, init: false };

function setPriceData(s) {
  let series = (s.price_series || []).filter((p) => p.close > 0);
  // Only extend with a live "now" point when the feed is fresh (<5 min old);
  // a stale feed would create a multi-day gap that piles markers at the edge.
  if (s.last_price > 0 && series.length && Date.now() - Number(series[series.length - 1].ts) < 5 * 60 * 1000) {
    const lp = Number(s.last_price);
    series = series.concat([{ ts: Date.now(), open: lp, high: lp, low: lp, close: lp, volume: 0 }]);
  }
  const prevLen = P.series.length;
  P.series = series;
  const closes = series.map((p) => p.close);
  const highs = series.map((p) => p.high), lows = series.map((p) => p.low);
  // TV-parity overlays: SSL Hybrid baseline (EMA30 +/- ATR*0.2) + AK MACD (12,26,9)
  P.baseline = emaSeries(closes, 30);
  P.atr = atrSeries(highs, lows, closes, 14);
  P.baseColor = closes.map((c, i) => {
    const b = P.baseline[i], a = P.atr[i];
    if (b == null || a == null) return null;
    if (c > b + a * 0.2) return "blue";
    if (c < b - a * 0.2) return "red";
    return "gray";
  });
  const mp = macdParts(closes, 12, 26, 9);
  P.macd = mp.macd; P.macdSig = mp.signal;
  P.dotGreen = P.macd.map((v, i) => (v != null && i > 0 && P.macd[i - 1] != null) ? v > P.macd[i - 1] : null);
  P.markers = s.trade_markers || [];
  P.signals = s.signals || [];
  const n = series.length;
  if (!P.view || prevLen < 2) {
    const span = Math.min(n, 160);
    P.view = { start: Math.max(0, n - span), end: n - 1 };
    P.follow = true;
  } else {
    const span = P.view.end - P.view.start + 1;
    if (P.follow) P.view = { start: Math.max(0, n - span), end: n - 1 };
    else { const start = clamp(P.view.start, 0, n - 1); P.view = { start, end: start + span - 1 }; } // preserve span + any overscroll
  }
}

function tsToIndexFloat(ts) {
  const s = P.series;
  if (!s.length) return 0;
  if (ts <= s[0].ts) return 0;
  if (ts >= s[s.length - 1].ts) return s.length - 1;
  let lo = 0, hi = s.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (s[m].ts < ts) lo = m; else hi = m; }
  const span = s[hi].ts - s[lo].ts || 1;
  return lo + (ts - s[lo].ts) / span;
}

function drawPriceChart() {
  const svg = $("price-card") && $("price-card").querySelector("svg");
  if (!svg || P.series.length < 2 || !P.view) return;
  const { W, padL, padR, top, pxB, volT, volB, rsiT, rsiB } = CH;
  const { start, end } = P.view;
  const visN = end - start + 1;
  const plotW = W - padL - padR;
  const xAt = (i) => padL + ((i - start) / Math.max(1, visN - 1)) * plotW;
  const vis = P.series.slice(start, end + 1);

  let pmin = Infinity, pmax = -Infinity, vmax = 0;
  for (const c of vis) { pmin = Math.min(pmin, c.low); pmax = Math.max(pmax, c.high); vmax = Math.max(vmax, c.volume); }
  const t0 = P.series[clamp(start, 0, P.series.length - 1)].ts, t1 = P.series[clamp(end, 0, P.series.length - 1)].ts;
  const mk = P.markers.filter((m) => m.entry_ts >= t0 && m.entry_ts <= t1);
  mk.forEach((m) => { pmin = Math.min(pmin, m.entry_price, m.exit_price || m.entry_price); pmax = Math.max(pmax, m.entry_price, m.exit_price || m.entry_price); });
  P.signals.forEach((sg) => { if (sg.ts >= t0 && sg.ts <= t1 && sg.price > 0) { pmin = Math.min(pmin, sg.price); pmax = Math.max(pmax, sg.price); } });
  const padY = (pmax - pmin) * 0.06 || 1; pmin -= padY; pmax += padY;
  const YP = (p) => top + (1 - (p - pmin) / (pmax - pmin || 1)) * (pxB - top);
  const YV = (v) => volB - (v / (vmax || 1)) * (volB - volT);
  // MACD pane scale: symmetric around zero over the visible range.
  let mabs = 1e-9;
  for (let i = start; i <= end; i++) {
    if (P.macd[i] != null) mabs = Math.max(mabs, Math.abs(P.macd[i]));
    if (P.macdSig[i] != null) mabs = Math.max(mabs, Math.abs(P.macdSig[i]));
  }
  const macdMid = (rsiT + rsiB) / 2;
  const YM = (v) => macdMid - (v / mabs) * (((rsiB - rsiT) / 2) * 0.92);
  const cw = Math.max(1, (plotW / visN) * 0.66);

  let g = "";
  // arrowheads for the IN->OUT links (coloured by win/loss via CSS)
  g += `<defs>
    <marker id="arr-win" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" refX="6" refY="5" orient="auto"><path class="arrhead win" d="M0,0 L10,5 L0,10 Z"/></marker>
    <marker id="arr-loss" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" refX="6" refY="5" orient="auto"><path class="arrhead loss" d="M0,0 L10,5 L0,10 Z"/></marker>
  </defs>`;
  // price gridlines + labels
  for (let i = 0; i <= 4; i++) {
    const v = pmin + (i / 4) * (pmax - pmin), y = YP(v).toFixed(1);
    g += `<line class="grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}"/><text class="axis-txt" x="${padL - 6}" y="${y}" text-anchor="end" dominant-baseline="middle">${Math.round(v).toLocaleString()}</text>`;
  }
  // time labels
  for (let i = 0; i <= 5; i++) {
    const idx = Math.round(start + (i / 5) * (visN - 1)), c = P.series[clamp(idx, 0, P.series.length - 1)];
    g += `<text class="axis-txt" x="${xAt(idx).toFixed(1)}" y="${(pxB + 12).toFixed(1)}" text-anchor="middle">${new Date(c.ts).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit" })}</text>`;
  }
  // panel separators + strip labels
  g += `<line class="panel-sep" x1="${padL}" y1="${volB}" x2="${W - padR}" y2="${volB}"/>`;
  g += `<text class="strip-lbl" x="${padL - 6}" y="${volT + 8}" text-anchor="end">VOL</text>`;
  g += `<line class="grid-line" x1="${padL}" y1="${YM(0).toFixed(1)}" x2="${W - padR}" y2="${YM(0).toFixed(1)}"/>`;
  g += `<text class="strip-lbl" x="${padL - 6}" y="${rsiT + 8}" text-anchor="end">MACD</text>`;

  // candles + volume
  for (let k = 0; k < vis.length; k++) {
    const c = vis[k], x = xAt(start + k), up = c.close >= c.open, kls = up ? "up" : "dn";
    g += `<line class="wick-${kls}" x1="${x.toFixed(1)}" y1="${YP(c.high).toFixed(1)}" x2="${x.toFixed(1)}" y2="${YP(c.low).toFixed(1)}"/>`;
    const yo = YP(c.open), yc = YP(c.close), bh = Math.max(1, Math.abs(yo - yc));
    g += `<rect class="candle-${kls}" x="${(x - cw / 2).toFixed(1)}" y="${Math.min(yo, yc).toFixed(1)}" width="${cw.toFixed(1)}" height="${bh.toFixed(1)}"/>`;
    if (c.volume > 0) g += `<rect class="vol-${kls}" x="${(x - cw / 2).toFixed(1)}" y="${YV(c.volume).toFixed(1)}" width="${cw.toFixed(1)}" height="${(volB - YV(c.volume)).toFixed(1)}"/>`;
  }

  // EMA overlays (drawn over visible slice)
  const emaPath = (arr) => {
    let d = "", on = false;
    for (let i = start; i <= end; i++) { if (arr[i] == null) continue; d += `${on ? "L" : "M"}${xAt(i).toFixed(1)},${YP(arr[i]).toFixed(1)} `; on = true; }
    return d;
  };
  // SSL Hybrid baseline (EMA30 +/- ATR*0.2) coloured blue/red/gray like TV
  const SSL = { blue: "#4aa3ff", red: "#ff5a6a", gray: "#8a93a3" };
  for (let i = start + 1; i <= end; i++) {
    if (P.baseline[i] == null || P.baseline[i - 1] == null) continue;
    const col = SSL[P.baseColor[i]] || SSL.gray;
    g += `<line x1="${xAt(i - 1).toFixed(1)}" y1="${YP(P.baseline[i - 1]).toFixed(1)}" x2="${xAt(i).toFixed(1)}" y2="${YP(P.baseline[i]).toFixed(1)}" stroke="${col}" stroke-width="2" fill="none"/>`;
  }
  // AK MACD: signal line + slope-coloured dots (green up / red down) like TV
  let sp = "", son = false;
  for (let i = start; i <= end; i++) { if (P.macdSig[i] == null) continue; sp += `${son ? "L" : "M"}${xAt(i).toFixed(1)},${YM(P.macdSig[i]).toFixed(1)} `; son = true; }
  g += `<path d="${sp}" stroke="#e0a060" stroke-width="1" fill="none" opacity="0.85"/>`;
  for (let i = start; i <= end; i++) {
    if (P.macd[i] == null) continue;
    const col = P.dotGreen[i] === false ? "#ff5a6a" : "#3fd08a";
    g += `<circle cx="${xAt(i).toFixed(1)}" cy="${YM(P.macd[i]).toFixed(1)}" r="1.7" fill="${col}"/>`;
  }

  // executed trades (IN -> OUT) — what 0rum actually did
  if (P.filter !== "signals") {
    mk.forEach((m) => {
      const ex = xAt(tsToIndexFloat(m.entry_ts)), ey = YP(m.entry_price);
      if (m.exit_price > 0 && m.exit_ts && m.exit_ts >= t0 && m.exit_ts <= t1) {
        const xx = xAt(tsToIndexFloat(m.exit_ts)), xy = YP(m.exit_price);
        g += `<line class="mk-link ${m.win ? "win" : "loss"}" x1="${ex.toFixed(1)}" y1="${ey.toFixed(1)}" x2="${xx.toFixed(1)}" y2="${xy.toFixed(1)}" marker-end="url(#arr-${m.win ? "win" : "loss"})"/>`;
        const mxp = ((ex + xx) / 2).toFixed(1), myp = ((ey + xy) / 2 - 5).toFixed(1);
        const pnlTxt = Math.abs(m.net_pnl_usd) >= 0.01 ? signed(m.net_pnl_usd, (v) => usd(v)) : signed(m.pnl_pct, (v) => pct(v));
        g += `<text class="mk-pnl ${m.win ? "win" : "loss"}" x="${mxp}" y="${myp}" text-anchor="middle">${esc(pnlTxt)}</text>`;
        g += `<rect class="${m.win ? "mk-exit-win" : "mk-exit-loss"}" x="${(xx - 6).toFixed(1)}" y="${(xy - 6).toFixed(1)}" width="12" height="12" rx="1.5"><title>OUT ${usd(m.exit_price)} · ${m.exit_reason || ""} · ${signed(m.pnl_pct, (v) => pct(v))}</title></rect>`;
        g += `<text class="mk-label out" x="${xx.toFixed(1)}" y="${(xy - 11).toFixed(1)}" text-anchor="middle">OUT</text>`;
      }
      g += `<path class="mk-entry" d="M${ex.toFixed(1)},${(ey - 10).toFixed(1)} L${(ex - 8).toFixed(1)},${(ey + 6).toFixed(1)} L${(ex + 8).toFixed(1)},${(ey + 6).toFixed(1)} Z"><title>IN ${usd(m.entry_price)} · ${m.side}</title></path>`;
      g += `<text class="mk-label in" x="${ex.toFixed(1)}" y="${(ey + 19).toFixed(1)}" text-anchor="middle">IN ${m.side === "short" ? "S" : "L"}</text>`;
    });
  }

  // Pine signal layer. A closed executed proposal already shows as IN/OUT via
  // trade markers (skipped here). An executed BUY whose position is still OPEN
  // has no closed trade yet -> draw it as a live IN (an execution, shown under
  // the Trades filter, not TV Signals).
  P.signals.forEach((sig) => {
    if (sig.ts < t0 || sig.ts > t1) return;
    if (sig.status === "executed") return; // already drawn as IN/OUT
    const x = xAt(tsToIndexFloat(sig.ts)), y = YP(sig.price);
    if (sig.status === "opened") {
      if (P.filter === "signals") return; // an execution belongs to Trades
      g += `<path class="mk-entry" d="M${x.toFixed(1)},${(y - 6).toFixed(1)} L${(x - 5).toFixed(1)},${(y + 4).toFixed(1)} L${(x + 5).toFixed(1)},${(y + 4).toFixed(1)} Z"><title>IN ${usd(sig.price)} · position open</title></path>`;
      g += `<circle class="mk-open-ring" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="7"/>`;
      g += `<text class="mk-label in" x="${x.toFixed(1)}" y="${(y + 15).toFixed(1)}" text-anchor="middle">IN ${sig.event === "SELL" ? "S" : "L"}</text>`;
      return;
    }
    if (P.filter === "trades") return; // remaining buckets are TV signals
    const isExit = sig.event === "EXIT";
    if (sig.status === "rejected" || sig.status === "duplicate") {
      const tip = `${sig.status}${sig.reason ? ": " + sig.reason : ""} · ${sig.event} ${usd(sig.price)}`;
      g += `<circle class="sig-refused" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3"><title>${esc(tip)}</title></circle>`;
    } else if (isExit) {
      const ay = y - 16;
      g += `<path class="sig-exit" d="M${x.toFixed(1)},${(ay + 10).toFixed(1)} L${(x - 5).toFixed(1)},${ay.toFixed(1)} L${(x + 5).toFixed(1)},${ay.toFixed(1)} Z"><title>TV EXIT (received) · ${usd(sig.price)}</title></path>`;
      g += `<text class="mk-label tv-exit" x="${x.toFixed(1)}" y="${(ay - 3).toFixed(1)}" text-anchor="middle">TV EXIT</text>`;
    } else {
      const lbl = sig.event === "SELL" ? "SELL" : "BUY";
      const ay = y + 16;
      g += `<path class="sig-buy" d="M${x.toFixed(1)},${(ay - 10).toFixed(1)} L${(x - 5).toFixed(1)},${ay.toFixed(1)} L${(x + 5).toFixed(1)},${ay.toFixed(1)} Z"><title>TV ${lbl} (received) · ${usd(sig.price)}</title></path>`;
      g += `<text class="mk-label tv-buy" x="${x.toFixed(1)}" y="${(ay + 11).toFixed(1)}" text-anchor="middle">TV ${lbl}</text>`;
    }
  });

  g += `<line class="crosshair" y1="${top}" y2="${rsiB}"/>`;
  svg.innerHTML = g;

  const last = P.series[P.series.length - 1].close, first = vis[0].close, chg = (last - first) / first;
  const hint = $("px-hint");
  if (hint) hint.textContent = `${num(last, 0)} · ${signed(chg, (v) => pct(v))} · ${visN} candles · ${mk.length} trades`;
}

function renderPrice(s) {
  const card = $("price-card");
  setPriceData(s);
  if (P.series.length < 2) { card.innerHTML = `<h2>Trade Signals · Pine → 0rum</h2><div class="flat">waiting for price history…</div>`; P.init = false; return; }
  if (!P.init) {
    const fbtn = (f, t) => `<button data-f="${f}" class="${P.filter === f ? "on" : ""}">${t}</button>`;
    card.innerHTML = `
      <h2>Trade Signals · Pine → 0rum <span class="hint" id="px-hint"></span>
        <span class="sig-filter">${fbtn("trades", "Trades")}${fbtn("signals", "TV Signals")}${fbtn("both", "Both")}</span>
        <span class="zoom-hint">scroll = zoom · drag = pan</span></h2>
      <div class="chartwrap">
        <svg class="pricechart" viewBox="0 0 ${CH.W} ${CH.H}" preserveAspectRatio="none"></svg>
        <div id="chart-tip"></div>
      </div>
      <div class="chart-legend">
        <span><i style="border-color:#4aa3ff"></i>SSL baseline (blue/red/gray)</span>
        <span><i class="tri"></i>IN/OUT (executed)</span>
        <span><i class="tri hollow"></i>TV BUY/EXIT (received)</span>
        <span><i class="dot"></i>rejected</span>
        <span>VOL · AK MACD (12,26,9)</span>
      </div>`;
    bindPriceInteractions(card.querySelector("svg"), card.querySelector(".chartwrap"));
    card.querySelectorAll(".sig-filter button").forEach((b) => {
      b.addEventListener("click", () => {
        P.filter = b.dataset.f;
        card.querySelectorAll(".sig-filter button").forEach((x) => x.classList.toggle("on", x.dataset.f === P.filter));
        drawPriceChart();
      });
    });
    P.init = true;
  }
  drawPriceChart();
}

function bindPriceInteractions(svg, wrap) {
  const tip = $("chart-tip");
  const vxToData = (clientX) => { const r = svg.getBoundingClientRect(); return { r, vx: ((clientX - r.left) / r.width) * CH.W }; };

  svg.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    if (!P.view) return;
    const n = P.series.length, { start, end } = P.view, span = end - start + 1;
    const { vx } = vxToData(ev.clientX);
    const frac = clamp((vx - CH.padL) / (CH.W - CH.padL - CH.padR), 0, 1);
    const anchor = start + frac * (span - 1);
    const factor = ev.deltaY < 0 ? 0.82 : 1.22;
    let newSpan = clamp(Math.round(span * factor), 20, n);
    let ns = Math.round(anchor - frac * (newSpan - 1));
    ns = clamp(ns, 0, n - 1); // allow the last candle to sit anywhere (scroll past the end)
    P.view = { start: ns, end: ns + newSpan - 1 };
    P.follow = P.view.end === n - 1; // follow only when pinned to the newest, not when overscrolled
    drawPriceChart();
  }, { passive: false });

  svg.addEventListener("mousedown", (ev) => { P.drag = { x: ev.clientX, start: P.view.start, end: P.view.end }; svg.classList.add("grabbing"); });
  window.addEventListener("mouseup", () => { if (P.drag) { P.drag = null; svg.classList.remove("grabbing"); } });
  svg.addEventListener("mousemove", (ev) => {
    if (!P.view) return;
    const { r, vx } = vxToData(ev.clientX);
    if (P.drag) {
      const n = P.series.length, span = P.drag.end - P.drag.start + 1, plotW = CH.W - CH.padL - CH.padR;
      const dIdx = Math.round(((ev.clientX - P.drag.x) / r.width * CH.W) / (plotW / span));
      let ns = clamp(P.drag.start - dIdx, 0, n - 1); // pan past the end (whitespace to the right)
      P.view = { start: ns, end: ns + span - 1 };
      P.follow = P.view.end === n - 1;
      drawPriceChart();
      return;
    }
    // crosshair + tooltip
    const cross = svg.querySelector(".crosshair");
    const { start, end } = P.view, visN = end - start + 1, plotW = CH.W - CH.padL - CH.padR;
    const frac = clamp((vx - CH.padL) / plotW, 0, 1);
    const idx = clamp(Math.round(start + frac * (visN - 1)), 0, P.series.length - 1);
    const c = P.series[idx], x = CH.padL + ((idx - start) / Math.max(1, visN - 1)) * plotW;
    if (cross) { cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.style.opacity = 1; }
    const wr = wrap.getBoundingClientRect();
    tip.style.opacity = 1;
    tip.style.left = clamp(ev.clientX - wr.left, 60, wr.width - 60) + "px";
    tip.style.top = "26px";
    const up = c.close >= c.open;
    tip.innerHTML = `<span class="t">${new Date(c.ts).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span><br>O ${num(c.open, 0)} H ${num(c.high, 0)} L ${num(c.low, 0)} <span class="px">C ${num(c.close, 0)}</span> <span style="color:${up ? "#2ecc71" : "#ff5765"}">${up ? "▲" : "▼"}</span>`;
  });
  svg.addEventListener("mouseleave", () => { const cross = svg.querySelector(".crosshair"); if (cross) cross.style.opacity = 0; tip.style.opacity = 0; });
}

/* ---- stats: donut + gauges ------------------------------------------- */
function ring(frac, color, center, label, sub) {
  const r = 40, c = 2 * Math.PI * r, f = clamp(Number(frac) || 0, 0, 1);
  return `<div class="gauge"><svg viewBox="0 0 100 100">
    <circle class="ring-bg" cx="50" cy="50" r="${r}" stroke-width="9"/>
    <circle class="ring-fg" cx="50" cy="50" r="${r}" stroke-width="9" stroke="${color}" stroke-dasharray="${(f * c).toFixed(1)} ${c.toFixed(1)}"/>
    <text class="donut-center" x="50" y="48" text-anchor="middle" fill="${color}" font-size="20">${center}</text>
    <text x="50" y="64" text-anchor="middle" fill="#7b8696" font-size="9">${sub || ""}</text>
  </svg><div class="glabel">${label}</div></div>`;
}
function renderStats(s) {
  const win = Number(s.win_rate || 0);
  const dd = Number(s.drawdown || 0), maxDD = (s.goal && s.goal.max_drawdown) || 0.05;
  const p = s.portfolio || {}, op = s.open_position || {};
  const exposure = op.active && p.balance_usd ? clamp(Number(op.notional_usd) / Number(p.balance_usd), 0, 1) : 0;
  $("stats-card").innerHTML = `
    <h2>Performance <span class="hint">${s.trade_count || 0} trades</span></h2>
    <div class="body"><div class="stats-grid">
      ${ring(win, "#2ecc71", pct(win, 0), "win rate", `${Math.round(win * (s.trade_count || 0))}/${s.trade_count || 0}`)}
      ${ring(maxDD ? dd / maxDD : 0, dd >= maxDD ? "#ff5765" : "#ffb454", pct(dd, 1), "drawdown", `max ${pct(maxDD, 0)}`)}
      ${ring(exposure, "#4aa3ff", pct(exposure, 0), "exposure", op.active ? "in position" : "flat")}
    </div></div>`;
}

/* ---- top chips -------------------------------------------------------- */
function renderTop(s) {
  const gg = s.guardrail || {};
  const gMap = { normal: "ok", caution: "warn", review: "warn", kill: "bad" };
  const cr = s.champion_reaudit || {};
  const crMap = { conforming: "ok", drift_detected: "bad", insufficient_data: "warn", not_yet_audited: "" };
  const src = s.signal_source || "native";
  const chips = [
    `<span class="chip info"><span class="k">asset</span><b>${esc(s.asset)}</b></span>`,
    `<span class="chip"><span class="k">mode</span><b>${esc(s.mode || "paper")}</b></span>`,
    `<span class="chip ${src === "tradingview_external" ? "info" : ""}"><span class="k">signals</span><b>${src === "tradingview_external" ? "TV EXTERNAL" : "NATIVE"}</b></span>`,
    `<span class="chip ${gMap[gg.status] || ""}"><span class="k">guardrail</span><b>${esc(gg.label || gg.status || "—")}</b></span>`,
    `<span class="chip ${crMap[cr.status] || ""}" title="${esc(cr.detail || "")}"><span class="k">champion</span><b>${esc(cr.label || "—")}</b></span>`,
  ];
  // Market watch chips (goal.watch_assets): price + window change per asset,
  // runtime asset excluded (already shown as the first chip). Display-only.
  for (const m of (s.markets || [])) {
    if (m.is_runtime) continue;
    const up = Number(m.change_pct || 0) >= 0;
    const px = Number(m.last_close || 0);
    chips.push(
      `<span class="chip ${up ? "ok" : "bad"}"><span class="k">${esc(m.asset)}</span>` +
      `<b>${px ? px.toLocaleString("en-US", { maximumFractionDigits: px >= 100 ? 0 : 2 }) : "—"}</b>` +
      ` ${up ? "▲" : "▼"}${Math.abs(Number(m.change_pct || 0) * 100).toFixed(1)}%</span>`
    );
  }
  $("topchips").innerHTML = chips.join("");
}

/* ---- markets: une courbe par actif suivi (BTC / ETH / OR) ------------- */
const MK_NAMES = { "BTC/USDT": "BTC", "ETH/USDT": "ETH", "PAXG/USDT": "OR (PAXG)" };
const MARKET_CARDS = [["BTC/USDT", "market-btc-card"], ["ETH/USDT", "market-eth-card"], ["PAXG/USDT", "market-paxg-card"]];

function marketCardInner(sig) {
  const cs = (sig.candles || []).filter((c) => c && c.close > 0);
  const px = cs.length ? cs[cs.length - 1].close : 0;
  const first = cs.length ? cs[0].close : 0;
  const chg = first ? (px / first - 1) : 0;
  const up = chg >= 0;
  const col = up ? "#2ecc71" : "#ff5765";
  let svg = `<div class="flat">${esc(sig.note || "no data")}</div>`;
  if (cs.length >= 2) {
    // Bougies + volume + flèches d'entrée/sortie (SVG pur). viewBox large pour
    // limiter la distorsion horizontale de preserveAspectRatio="none".
    const w = 1200, hP = 200, hV = 40, h = hP + hV + 6, pad = 3;
    const lo = Math.min(...cs.map((c) => c.low)), hi = Math.max(...cs.map((c) => c.high));
    const vMax = Math.max(...cs.map((c) => c.volume || 0), 1);
    const slot = (w - 2 * pad) / cs.length, bw = Math.max(slot * 0.6, 1);
    const sx = (i) => pad + i * slot + slot / 2;
    const sy = (v) => pad + (1 - (v - lo) / (hi - lo || 1)) * (hP - 2 * pad);
    let g = "";
    cs.forEach((c, i) => {
      const cUp = c.close >= c.open, cc = cUp ? "#2ecc71" : "#ff5765";
      const x = sx(i), yO = sy(c.open), yC = sy(c.close);
      g += `<line x1="${x.toFixed(1)}" y1="${sy(c.high).toFixed(1)}" x2="${x.toFixed(1)}" y2="${sy(c.low).toFixed(1)}" stroke="${cc}" stroke-width="1" opacity="0.85"/>`;
      g += `<rect x="${(x - bw / 2).toFixed(1)}" y="${Math.min(yO, yC).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(Math.abs(yC - yO), 1).toFixed(1)}" fill="${cc}"/>`;
      const vh = ((c.volume || 0) / vMax) * (hV - 2);
      g += `<rect x="${(x - bw / 2).toFixed(1)}" y="${(h - vh - 1).toFixed(1)}" width="${bw.toFixed(1)}" height="${vh.toFixed(1)}" fill="${cc}" opacity="0.3"/>`;
    });
    const yLast = sy(cs[cs.length - 1].close);
    g += `<line x1="${pad}" y1="${yLast.toFixed(1)}" x2="${w - pad}" y2="${yLast.toFixed(1)}" stroke="${col}" stroke-width="1" stroke-dasharray="6,4" opacity="0.6"/>`;
    // Labels signaux explicites : InL / InS (entrées) · TP / SL (sorties)
    const byTs = new Map(cs.map((c, i) => [c.ts, i]));
    // Ligne entrée -> sortie pour chaque trade (verte si TP, rouge si SL)
    (sig.trades || []).forEach((t) => {
      const i0 = byTs.get(t.entry_ts), i1 = byTs.get(t.exit_ts);
      if (i0 == null || i1 == null) return;
      const lc = t.result === "TP" ? "#2ecc71" : "#ff5765";
      g += `<line x1="${sx(i0).toFixed(1)}" y1="${sy(t.entry_price).toFixed(1)}" x2="${sx(i1).toFixed(1)}" y2="${sy(t.exit_price).toFixed(1)}" stroke="${lc}" stroke-width="1.5" stroke-dasharray="5,3" opacity="0.85"/>`;
    });
    (sig.markers || []).forEach((m) => {
      const i = byTs.get(m.ts);
      if (i == null) return;
      const x = sx(i), long = m.side === "long";
      const lab = m.label || (m.kind === "entry" ? (long ? "InL" : "InS") : "×");
      const green = lab === "InL" || lab === "TP";
      const mc = green ? "#2ecc71" : "#ff5765";
      const yAnchor = long ? sy(cs[i].low) : sy(cs[i].high);   // long: sous le bas · short: au-dessus du haut
      const yTip = long ? yAnchor + 3 : yAnchor - 3;
      const yEnd = long ? yAnchor + 13 : yAnchor - 13;
      const yText = long ? yEnd + 12 : yEnd - 4;
      g += `<line x1="${x.toFixed(1)}" y1="${yTip.toFixed(1)}" x2="${x.toFixed(1)}" y2="${yEnd.toFixed(1)}" stroke="${mc}" stroke-width="1.6"/>`;
      g += `<text x="${x.toFixed(1)}" y="${yText.toFixed(1)}" fill="${mc}" font-size="14" font-family="monospace" font-weight="bold" text-anchor="middle">${lab}</text>`;
    });
    // REAL paper fills (sig.real_markers): filled dots, snapped to the nearest
    // candle (a fill's cycle time rarely equals a bar open). Visually distinct
    // from the thin backtest overlay above — the two must not be confused.
    (sig.real_markers || []).forEach((m) => {
      if (m.price == null || !cs.length) return;
      let bi = -1, bd = Infinity;
      for (let k = 0; k < cs.length; k++) { const d = Math.abs((cs[k].ts || 0) - (m.ts || 0)); if (d < bd) { bd = d; bi = k; } }
      if (bi < 0) return;
      const x = sx(bi), y = sy(m.price);
      const entry = m.kind === "entry";
      const mc = entry ? "#38bdf8" : (m.label === "TP" ? "#2ecc71" : "#ff5765");
      const lab = (m.label || (entry ? "IN" : "×")) + (m.strategy_id ? " " + m.strategy_id : "");
      g += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4.5" fill="${mc}" stroke="#0b0e14" stroke-width="1.2"/>`;
      g += `<text x="${(x + 7).toFixed(1)}" y="${(y + 4).toFixed(1)}" fill="${mc}" font-size="12" font-family="monospace" font-weight="bold">${esc(lab)}</text>`;
    });
    const fmt = (v) => v >= 100 ? Math.round(v).toLocaleString("en-US") : v.toFixed(2);
    g += `<text x="${pad + 4}" y="16" fill="#8b93a7" font-size="13" font-family="monospace">${fmt(hi)}</text>`;
    g += `<text x="${pad + 4}" y="${(hP - 6).toFixed(1)}" fill="#8b93a7" font-size="13" font-family="monospace">${fmt(lo)}</text>`;
    svg = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%;height:240px;display:block">${g}</svg>`;
  }
  const badge = sig.is_runtime ? `<span class="chip info" style="margin-left:6px">EN TRADE</span>` : "";
  return `<h2 style="display:flex;align-items:baseline;gap:8px">${esc(MK_NAMES[sig.asset] || sig.asset)}${badge}
      <span class="hint">${esc(sig.engine || "")} · ${esc(sig.timeframe || "")}</span>
      <span style="margin-left:auto;font-size:1.15em"><b>${px ? px.toLocaleString("en-US", { maximumFractionDigits: px >= 100 ? 0 : 2 }) : "—"}</b></span>
      <span style="color:${col}">${up ? "▲" : "▼"}${Math.abs(chg * 100).toFixed(2)}%</span></h2>
    <div class="hint" style="padding:1px 6px 3px"><b style="color:#2ecc71">InL</b> entrée long · <b style="color:#ff5765">InS</b> entrée short · <b style="color:#2ecc71">TP</b>/<b style="color:#ff5765">SL</b> sortie · <b style="color:#38bdf8">●</b> fill réel — ${esc(sig.note || "")}</div>
    <div class="body">${svg}</div>`;
}

function renderMarkets(s) {
  const sigs = s.market_signals || {};
  for (const [asset, cardId] of MARKET_CARDS) {
    const card = $(cardId);
    if (!card) continue;
    const sig = sigs[asset];
    card.innerHTML = sig
      ? marketCardInner(sig)
      : `<h2>${esc(MK_NAMES[asset] || asset)}</h2><div class="flat">waiting for market data…</div>`;
  }
}

/* ---- KPIs ------------------------------------------------------------- */
function renderKpis(s) {
  const p = s.paper || {};
  const dd = Number(s.drawdown || 0);
  const ddMax = (s.goal && s.goal.max_drawdown) || 0.05;
  const unrealized = Number(p.equity_usd || 0) - Number(p.balance_usd || 0);
  const tiles = [
    { label: "Equity paper", val: usd(p.equity_usd), sub: `cash ${usd(p.balance_usd, 0)}` },
    { label: "P&L", val: signed(p.pnl_usd, usd), sub: signed(p.pnl_pct, pct), cls: cls(p.pnl_usd) },
    { label: "Non réalisé", val: signed(unrealized, usd), sub: `${p.open_count || 0} position(s)`, cls: cls(unrealized) },
    { label: "Drawdown", val: pct(dd), sub: `max ${pct(ddMax)}`, cls: dd >= ddMax ? "neg" : "" },
    { label: "Win rate", val: pct(s.win_rate, 1), sub: `${s.trade_count || 0} trades` },
    { label: "Mise à jour", val: p.updated_at ? hhmmss(p.updated_at) : "—", sub: `start ${usd(p.starting_balance_usd, 0)}` },
  ];
  $("kpis").innerHTML = tiles
    .map((t) => `<div class="kpi ${t.cls || ""}"><div class="label">${t.label}</div><div class="val">${t.val}</div><div class="sub">${t.sub}</div></div>`)
    .join("");
}

/* ---- open position ---------------------------------------------------- */
function renderPosition(s) {
  const card = $("position-card");
  const p = s.open_position || {};
  if (!p.active) { card.style.display = "none"; return; }  // hide when flat; reappears filled when a position opens
  card.style.display = "";
  const entry = Number(p.entry_price), cur = Number(p.current_price), stop = Number(p.stop_price), tp = Number(p.take_profit_price);
  const range = tp - stop, pos = range > 0 ? clamp((cur - stop) / range, 0, 1) * 100 : 50;
  const heldM = Math.round((p.held_seconds || 0) / 60);
  card.innerHTML = `
    <h2>Open position <span class="hint">${esc(p.direction || "long")} · ${esc(p.asset || "")}</span></h2>
    <div class="body">
      <div class="kv">
        <div class="k">entry</div><div class="v">${usd(entry)}</div>
        <div class="k">current</div><div class="v ${cls(cur - entry)}">${usd(cur)}</div>
        <div class="k">unrealized</div><div class="v ${cls(p.unrealized_pnl_usd)}">${signed(p.unrealized_pnl_usd, usd)} (${signed(p.unrealized_pnl_pct, pct)})</div>
        <div class="k">size</div><div class="v">${usd(p.notional_usd, 0)} · ${num(p.qty_base, 4)}</div>
        <div class="k">held</div><div class="v">${heldM}m</div>
      </div>
      <div class="pricebar"><div class="fill" style="width:100%"></div><div class="mark" style="left:${pos}%"></div></div>
      <div class="pricebar-legend"><span>SL ${usd(stop, 0)}</span><span>price</span><span>TP ${usd(tp, 0)}</span></div>
    </div>`;
}

/* ---- equity sparkline ------------------------------------------------- */
function renderEquity(s) {
  const card = $("equity-card");
  const pts = (s.equity_curve || []).map((p) => Number(p.equity));
  let svg = `<div class="flat">no closed trades yet</div>`;
  if (pts.length >= 2) {
    const w = 460, h = 90, pad = 4, min = Math.min(...pts, 1), max = Math.max(...pts, 1);
    const sx = (i) => pad + (i / (pts.length - 1)) * (w - 2 * pad);
    const sy = (v) => pad + (1 - (v - min) / (max - min || 1)) * (h - 2 * pad);
    const line = pts.map((v, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(" ");
    const area = `${line} L${sx(pts.length - 1).toFixed(1)},${h - pad} L${pad},${h - pad} Z`;
    svg = `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="eqgrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6ee7d7"/><stop offset="1" stop-color="#6ee7d7" stop-opacity="0"/></linearGradient></defs><line class="base" x1="${pad}" y1="${sy(1).toFixed(1)}" x2="${w - pad}" y2="${sy(1).toFixed(1)}"/><path class="area" d="${area}"/><path class="line" d="${line}"/></svg>`;
  }
  card.innerHTML = `<h2>Equity curve <span class="hint">${(s.equity_curve || []).length} pts · compound ${signed(s.pnl_compound, (v) => pct(v))}</span></h2><div class="body">${svg}</div>`;
}

/* ---- strategy --------------------------------------------------------- */
function condLine(c) {
  if (!c) return "";
  const ind = c.indicator + (c.params && c.params.period ? `(${c.params.period})` : "");
  const field = c.field && c.field !== "value" ? `.${c.field}` : "";
  const rhs = c.value_str != null ? c.value_str : c.compare_indicator ? `${c.compare_indicator}(${(c.compare_params || {}).period || ""})` : c.value2 != null ? `${c.value}..${c.value2}` : c.value;
  return `<div class="cond">${esc(ind + field)} <span style="color:var(--warn)">${esc(c.operator)}</span> ${esc(rhs)}</div>`;
}
function renderStrategy(s) {
  const st = s.strategy || {};
  const entry = st.entry && st.entry.conditions ? st.entry : null;
  const exit = st.exit && st.exit.conditions ? st.exit : null;
  const riskBlock = st.risk || {};
  const risk = (k, d) => (riskBlock[k] != null ? riskBlock[k] : d);
  const entryHtml = entry ? `<div class="cond"><span class="logic">ENTRY · ${esc(entry.logic)}</span></div>` + entry.conditions.map(condLine).join("") : `<div class="cond"><span class="logic">ENTRY</span> ${esc(JSON.stringify(st.entry || {}))}</div>`;
  const exitHtml = exit ? `<div class="cond"><span class="logic">EXIT · ${esc(exit.logic)}</span></div>` + exit.conditions.map(condLine).join("") : "";
  $("strategy-card").innerHTML = `
    <h2>Strategy <span class="hint">v${esc(st.version || "?")}</span></h2>
    <div class="body">${entryHtml}${exitHtml}
      <div class="kv" style="margin-top:10px">
        <div class="k">stop / tp</div><div class="v">${num(risk("stop_loss_pct", 2), 1)}% / ${num(risk("take_profit_pct", 3), 1)}%</div>
        <div class="k">size (R)</div><div class="v">${num(risk("position_size_r", 0.5), 2)}%</div>
        <div class="k">max hold</div><div class="v">${risk("max_hold_candles", 30)} candles</div>
      </div></div>`;
}

/* ---- portfolio risk card (writes state/portfolio.yaml — the file the LIVE
   paper engine reloads at the start of every cycle, so a slider change takes
   effect at the next cycle without any restart). Same visual structure as the
   original leverage card: muted label, big readout, slider, graduated scale. */
let _pfInited = false;
let _pfBusy = false; // true while dragging or saving — pauses external sync
const _pfFmtPct = (v) => (Number(v) * 100).toFixed(2).replace(/\.?0+$/, "") + "%";
function renderLeverage(s) {
  const card = $("leverage-card");
  if (!card) return;
  const pc = s.portfolio_config || {};
  const strategies = pc.strategies || [];
  const bounds = pc.risk_bounds || { min: 0.005, max: 0.02 };
  if (!strategies.length) {
    card.innerHTML = `<h2>Risk · Portfolio</h2><div class="body muted">portfolio.yaml not found</div>`;
    return;
  }
  const active = strategies.filter((st) => st.entry_enabled);
  const inactive = strategies.filter((st) => !st.entry_enabled);
  if (!_pfInited) {
    const riskScale = [0, 1 / 3, 2 / 3, 1]
      .map((t) => `<span>${_pfFmtPct(bounds.min + t * (bounds.max - bounds.min))}</span>`).join("");
    const label = (text) =>
      `<div class="muted" style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;margin-top:14px">${text}</div>`;
    const levSet = pc.max_leverage != null;
    const levBlock = `
        ${label("portfolio — max leverage (notional cap × equity)")}
        <div class="lev-readout"><span id="pf-lev-val">${levSet ? Number(pc.max_leverage).toFixed(1) : "off"}</span><span class="lev-x" id="pf-lev-unit">${levSet ? "×" : ""}</span></div>
        <input id="pf-lev" class="pf-range" type="range" min="1" max="5" step="0.5" value="${levSet ? Number(pc.max_leverage) : 5}" />
        <div class="lev-scale"><span>1×</span><span>2×</span><span>3×</span><span>4×</span><span>5×</span></div>`;
    const rows = active.map((st) => {
      const riskPct = Number(st.risk_pct || 0) * 100;
      let html = `
        ${label(`${esc(st.id)} — risk / trade`)}
        <div class="lev-readout"><span id="pf-risk-val-${st.id}">${riskPct.toFixed(2)}</span><span class="lev-x">%</span></div>
        <input id="pf-risk-${st.id}" data-sid="${st.id}" class="pf-range pf-risk" type="range"
               min="${(bounds.min * 100).toFixed(2)}" max="${(bounds.max * 100).toFixed(2)}" step="0.25" value="${riskPct}" />
        <div class="lev-scale">${riskScale}</div>`;
      if (st.reward_risk_ratio != null) {
        html += `
        ${label(`${esc(st.id)} — take-profit (reward : risk)`)}
        <div class="lev-readout"><span id="pf-rr-val-${st.id}">${Number(st.reward_risk_ratio).toFixed(1)}</span><span class="lev-x">R</span></div>
        <input id="pf-rr-${st.id}" data-sid="${st.id}" class="pf-range pf-rr" type="range" min="0.5" max="5" step="0.5" value="${Number(st.reward_risk_ratio)}" />
        <div class="lev-scale"><span>0.5</span><span>1.5</span><span>2.5</span><span>3.5</span><span>5</span></div>`;
      }
      return html;
    }).join("");
    const offLine = inactive.length
      ? label("entries off — " + inactive.map((st) => `${esc(st.id)} ${_pfFmtPct(st.risk_pct || 0)}`).join(" · "))
      : "";
    card.innerHTML = `
      <h2>Risk · Portfolio <span class="hint">applied next engine cycle</span></h2>
      <div class="body lev-body">${levBlock}${rows}${offLine}<div id="pf-status" class="muted lev-status">&nbsp;</div></div>`;
    const post = async (body, after) => {
      const status = $("pf-status");
      status.textContent = "saving…";
      try {
        const r = await fetch("/api/portfolio/risk", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const j = await r.json();
        if (j.ok) { after(j); status.textContent = "applied ✓ — effective next engine cycle"; }
        else { status.textContent = "error: " + (j.error || r.status); }
      } catch (e) { status.textContent = "error: " + e; }
      finally { _pfBusy = false; setTimeout(() => { if (!_pfBusy) status.textContent = " "; }, 5000); }
    };
    const levEl = $("pf-lev");
    if (levEl) {
      levEl.addEventListener("input", () => { _pfBusy = true; $("pf-lev-val").textContent = Number(levEl.value).toFixed(1); $("pf-lev-unit").textContent = "×"; });
      levEl.addEventListener("change", () => post(
        { max_leverage: Number(levEl.value) },
        (j) => { levEl.value = j.max_leverage; $("pf-lev-val").textContent = Number(j.max_leverage).toFixed(1); $("pf-lev-unit").textContent = "×"; },
      ));
    }
    card.querySelectorAll(".pf-risk").forEach((el) => {
      el.addEventListener("input", () => { _pfBusy = true; $(`pf-risk-val-${el.dataset.sid}`).textContent = Number(el.value).toFixed(2); });
      el.addEventListener("change", () => post(
        { strategy_id: el.dataset.sid, risk_pct: Number(el.value) / 100 },
        (j) => { el.value = j.risk_pct * 100; $(`pf-risk-val-${el.dataset.sid}`).textContent = (j.risk_pct * 100).toFixed(2); },
      ));
    });
    card.querySelectorAll(".pf-rr").forEach((el) => {
      el.addEventListener("input", () => { _pfBusy = true; $(`pf-rr-val-${el.dataset.sid}`).textContent = Number(el.value).toFixed(1); });
      el.addEventListener("change", () => post(
        { strategy_id: el.dataset.sid, reward_risk_ratio: Number(el.value) },
        (j) => { el.value = j.reward_risk_ratio; $(`pf-rr-val-${el.dataset.sid}`).textContent = Number(j.reward_risk_ratio).toFixed(1); },
      ));
    });
    _pfInited = true;
    return;
  }
  // Later polls: reflect external edits to portfolio.yaml only when idle, so a
  // drag or in-flight save is never clobbered by the background refresh.
  if (_pfBusy) return;
  const levEl = $("pf-lev");
  if (levEl && pc.max_leverage != null && document.activeElement !== levEl) {
    levEl.value = Number(pc.max_leverage);
    $("pf-lev-val").textContent = Number(pc.max_leverage).toFixed(1);
    $("pf-lev-unit").textContent = "×";
  }
  active.forEach((st) => {
    const riskEl = $(`pf-risk-${st.id}`);
    if (riskEl && document.activeElement !== riskEl) {
      riskEl.value = Number(st.risk_pct || 0) * 100;
      $(`pf-risk-val-${st.id}`).textContent = (Number(st.risk_pct || 0) * 100).toFixed(2);
    }
    const rrEl = $(`pf-rr-${st.id}`);
    if (rrEl && st.reward_risk_ratio != null && document.activeElement !== rrEl) {
      rrEl.value = Number(st.reward_risk_ratio);
      $(`pf-rr-val-${st.id}`).textContent = Number(st.reward_risk_ratio).toFixed(1);
    }
  });
}

/* ---- trades table (entry / gain-loss) --------------------------------- */
function renderTrades(s) {
  const trades = s.latest_trades || [];
  const rows = trades.length
    ? trades.map((t) => {
        const net = Number(t.net_pnl_usd || 0);
        return `<tr><td class="ts">${hhmmss(t.ts)}</td><td>${esc(t.direction || "long")}</td><td>${usd(t.entry_price)}</td><td>${usd(t.notional_usd, 0)}</td><td class="${cls(net)}">${signed(net, usd)}</td></tr>`;
      }).join("")
    : `<tr><td colspan="5" class="flat">no trades yet</td></tr>`;
  $("trades-card").innerHTML = `<h2>Trades <span class="hint">${trades.length} recent · newest first</span></h2><div class="body"><table class="mini-table"><thead><tr><th>time</th><th>side</th><th>entry</th><th>stake</th><th>gain/loss</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

/* ---- external (TradingView) panel ------------------------------------ */
const EXT_TAG = { executed: "good", rejected: "bad", received: "info", duplicate: "warn", malformed: "warn", no_table: "" };
function renderExternal(s) {
  const e = s.external || { counts: {}, recent: [], mode: "native" };
  const c = e.counts || {};
  const counts = ["received", "executed", "rejected", "duplicate", "malformed"]
    .map((k) => `<div class="count ${k}"><div class="n">${c[k] || 0}</div><div class="t">${k}</div></div>`).join("");
  const rows = (e.recent || []).length
    ? e.recent.map((r) => `<div class="row"><span class="ts">${hhmmss(r.ts)}</span><span class="msg">${esc(r.detail)}${r.check ? ` <span style="color:var(--dim)">[${esc(r.check)}]</span>` : ""}</span><span class="tag ${EXT_TAG[r.status] || ""}">${esc(r.status)}</span></div>`).join("")
    : `<div class="flat">no external signals${e.mode !== "tradingview_external" ? " · mode is NATIVE" : " yet"}</div>`;
  $("external-card").innerHTML = `<h2>TradingView signals <span class="hint">${esc(e.mode)} · ${e.total || 0} total</span></h2><div class="body"><div class="counts">${counts}</div><div class="feed">${rows}</div></div>`;
}

/* ---- raw log feed ----------------------------------------------------- */
const LOG_TAG = {
  trade_closed: "info", position_opened: "info", external_signal_executed: "good",
  external_signal_rejected: "bad", external_signal_malformed: "warn", external_signal_duplicate: "warn",
  guardrail_changed: "warn", worker_failure: "bad", position_quarantined: "warn", price_source_changed: "",
};
function renderLogs(s) {
  const logs = s.logs || [];
  const rows = logs.length
    ? logs.map((l) => `<div class="row log"><span class="ts">${hhmmss(l.ts)}</span><span class="kind ${LOG_TAG[l.kind] || ""}">${esc(l.kind)}</span><span class="msg">${esc(l.detail)}</span></div>`).join("")
    : `<div class="flat">no events logged</div>`;
  $("log-card").innerHTML = `<h2>Event log <span class="hint">${logs.length} recent · newest first</span></h2><div class="body"><div class="feed">${rows}</div></div>`;
}

/* ====================================================================== */
/* Portfolio recherche (paper Kelly — scripts/portfolio_shadow.py)        */
/* ====================================================================== */
const RP_LABEL = { donchian_btc: "Donchian BTC", donchian_eth: "Donchian ETH", gold_cot: "Or · fenêtre COT" };
function renderResearchPortfolio(s) {
  const rp = s.research_portfolio;
  const legacy = s.legacy_audit || {};
  const card = $("research-portfolio-card");
  if (!card) return;
  const legacyRows = (legacy.recent || []).slice(0, 5).map(trade =>
    `<tr><td>${hhmmss(trade.ts)}</td><td>${esc(trade.direction || "long")}</td><td>${num(trade.entry_price,0)} → ${num(trade.exit_price,0)}</td><td class="${cls(trade.net_pnl_usd)}">${signed(trade.net_pnl_usd, usd)}</td><td>${esc(trade.exit_reason || "—")}</td></tr>`
  ).join("") || `<tr><td colspan="5" class="flat">aucun trade legacy sur 7 jours</td></tr>`;
  const legacyHtml = `<div class="legacy-audit"><div class="legacy-audit-head"><b>Historique legacy · non autoritatif</b><span>${legacy.trade_count_7d || 0} trades / 7 j · ${signed(legacy.net_pnl_usd_7d || 0, usd)}</span></div><div class="scenario-disclaimer">${esc(legacy.warning || "Exclu du portefeuille unifié.")}</div><table class="mini-table"><thead><tr><th>heure</th><th>sens</th><th>entrée → sortie</th><th>P&L</th><th>raison</th></tr></thead><tbody>${legacyRows}</tbody></table></div>`;
  if (!rp || !Object.keys(rp.engines || {}).length) {
    card.innerHTML = `<h2>Audits séparés <span class="hint">le legacy ne modifie jamais l'équité unifiée</span></h2><div class="body">${legacyHtml}<div class="flat">portfolio recherche indisponible</div></div>`;
    return;
  }
  const age = rp.poll_age_seconds;
  const ageTxt = age == null ? "jamais" : age < 90 ? `${Math.round(age)}s` : age < 5400 ? `${Math.round(age / 60)}min` : `${(age / 3600).toFixed(1)}h — <b style="color:#ff5765">poll en retard</b>`;
  const ddPct = ((rp.drawdown || 0) * 100).toFixed(1);
  const ddWarn = rp.drawdown > 0.4 ? "#ff5765" : rp.drawdown > 0.25 ? "#ffb454" : "#8aa0b8";
  const mtm = rp.equity_mtm ?? rp.equity ?? 1;
  const mtmCol = mtm >= (rp.equity ?? 1) ? "#2ecc71" : "#ff5765";
  const mtmTxt = `<span style="color:${mtmCol}">MtM x${mtm.toFixed(4)}</span>${rp.open_positions ? ` · ${rp.open_positions} pos. ouverte${rp.open_positions > 1 ? "s" : ""}` : ""}`;
  const tiles = Object.entries(rp.engines).map(([name, e]) => {
    const act = e.action || "?";
    const col = act === "hold" || act === "enter" ? "#2ecc71" : act.startsWith("exit") ? "#ffb454" : "#8aa0b8";
    let detail = "";
    if (name === "gold_cot") {
      detail = `COT idx <b>${e.cot_index ?? "?"}</b> · gate ≤20 (dist ${e.dist_gate ?? "?"})`;
    } else if (act === "flat") {
      detail = `entrée à +${e.dist_entry_pct ?? "?"}% (hi20 ${e.hi20 ? num(e.hi20, 0) : "?"})`;
    } else {
      detail = `sortie à −${e.dist_exit_pct ?? "?"}% (lo10 ${e.lo10 ? num(e.lo10, 0) : "?"})`;
    }
    const p = e.position || {};
    const posLine = p.open
      ? `<div class="hint">pos <b>ouverte</b> @ ${num(p.entry_px, 0)} → <b style="color:${p.unrealized_pct >= 0 ? "#2ecc71" : "#ff5765"}">${p.unrealized_pct >= 0 ? "+" : ""}${(p.unrealized_pct * 100).toFixed(2)}%</b> latent (${p.unrealized_equity >= 0 ? "+" : ""}${(p.unrealized_equity * 100).toFixed(2)}% éq.)</div>`
      : "";
    return `<div class="tile"><div class="k">${RP_LABEL[name] || esc(name)} <span style="color:${col}">● ${esc(act)}</span></div>
      <div class="v">${e.price ? num(e.price, 0) : "—"}</div>
      <div class="hint">${detail} · score ${e.score ?? "—"}</div>${posLine}</div>`;
  }).join("");
  const rows = (rp.trades || []).length
    ? rp.trades.map((t) => `<tr><td>${hhmmss(t.ts)}</td><td>${esc(RP_LABEL[t.engine] || t.engine)}</td><td>${(t.r ?? 0) > 0 ? "+" : ""}${(t.r ?? 0).toFixed(2)}R</td><td style="color:${(t.pnl_pct ?? 0) >= 0 ? "#2ecc71" : "#ff5765"}">${(t.pnl_pct ?? 0) >= 0 ? "+" : ""}${(t.pnl_pct ?? 0).toFixed(2)}%</td><td>x${(t.equity ?? 1).toFixed(4)}</td></tr>`).join("")
    : `<tr><td colspan="5" class="flat">aucun trade clôturé — le paper attend son premier signal</td></tr>`;
  card.innerHTML = `<h2>Portfolio recherche <span class="hint">${esc(rp.policy)} · équité x${(rp.equity ?? 1).toFixed(4)} · ${mtmTxt} · <span style="color:${ddWarn}">DD ${ddPct}%</span> / kill ${((rp.kill_dd || 0.6) * 100).toFixed(0)}% · poll ${ageTxt}</span></h2>
    <div class="body">
      ${legacyHtml}
      <div class="kpi-row" style="margin-bottom:10px">${tiles}</div>
      <table class="mini-table"><thead><tr><th>heure</th><th>moteur</th><th>R</th><th>P&L</th><th>équité</th></tr></thead><tbody>${rows}</tbody></table>
    </div>`;
}

/* ====================================================================== */
/* Market terminal V5 — thin SVG, comparable 1h views, display-only fan   */
/* ====================================================================== */
let proChart = null; // compatibility with the old resize guard
const TERMINAL_ASSETS = ["BTC/USDT", "BTC/USDT::btc_utbot_m15_h1", "ETH/USDT", "PAXG/USDT"];
const TERMINAL_CARD_IDS = { "BTC/USDT": "market-btc-card", "BTC/USDT::btc_utbot_m15_h1": "market-btc-utbot-card", "ETH/USDT": "market-eth-card", "PAXG/USDT": "market-paxg-card" };
const terminalZoom = { "BTC/USDT": 120, "BTC/USDT::btc_utbot_m15_h1": 120, "ETH/USDT": 120, "PAXG/USDT": 120 };
const terminalPan = { "BTC/USDT": 0, "BTC/USDT::btc_utbot_m15_h1": 0, "ETH/USDT": 0, "PAXG/USDT": 0 };
let terminalVisibility = { "BTC/USDT": true, "BTC/USDT::btc_utbot_m15_h1": true, "ETH/USDT": false, "PAXG/USDT": false };
try { terminalVisibility = { ...terminalVisibility, ...JSON.parse(localStorage.getItem("orum-terminal-visibility") || "{}") }; } catch (e) {}
let lastTerminalState = null;
let terminalResizeObserver = null;
const terminalResizeTimers = {};

function nearestCandleIndex(candles, ts) {
  let best = -1, distance = Infinity;
  for (let i = 0; i < candles.length; i++) {
    const d = Math.abs(Number(candles[i].ts) - Number(ts));
    if (d < distance) { best = i; distance = d; }
  }
  return best;
}

function buildScenarioFan(candles, horizon = 24, pathCount = 14) {
  if (candles.length < 2) return [];
  const recent = candles.slice(-15);
  const ranges = recent.map(c => Math.max(Number(c.high) - Number(c.low), Math.abs(Number(c.close) - Number(c.open))));
  const atr = Math.max(ranges.reduce((a, b) => a + b, 0) / ranges.length, Number(candles.at(-1).close) * .0008);
  const diffs = recent.slice(1).map((c, i) => Number(c.close) - Number(recent[i].close));
  const drift = clamp(diffs.reduce((a, b) => a + b, 0) / Math.max(1, diffs.length), -atr * .18, atr * .18);
  const last = Number(candles.at(-1).close);
  return Array.from({ length: pathCount }, (_, pathIndex) => {
    const z = (pathIndex - (pathCount - 1) / 2) / Math.max(1, (pathCount - 1) / 2);
    const values = [last];
    for (let step = 1; step <= horizon; step++) {
      const wave = Math.sin(step * .82 + pathIndex * 1.37) * atr * .055;
      const spread = z * atr * (.055 + step * .011);
      values.push(values.at(-1) + drift * .24 + spread + wave);
    }
    return values;
  });
}

function buildCalibratedFan(report, lastPrice) {
  if (!report || !report.horizons || !(lastPrice > 0)) return [];
  const names = ["p10", "p25", "p50", "p75", "p90"], milestones = [0, 6, 12, 24];
  return names.map(name => {
    const anchors = milestones.map(hour => hour === 0 ? lastPrice
      : lastPrice * (1 + Number((((report.horizons || {})[String(hour)] || {}).quantiles || {})[name])));
    if (anchors.slice(1).some(value => !Number.isFinite(value))) return null;
    const path = [];
    for (let hour = 0; hour <= 24; hour++) {
      const right = milestones.findIndex(value => value >= hour), hi = Math.max(1, right), lo = hi - 1;
      const weight = (hour - milestones[lo]) / (milestones[hi] - milestones[lo] || 1);
      path.push(anchors[lo] * (1 - weight) + anchors[hi] * weight);
    }
    return path;
  }).filter(Boolean);
}

function buildForecastHistoryPath(history, candles) {
  if (!candles.length) return [];
  const firstTs = Number(candles[0].ts), lastTs = Number(candles.at(-1).ts);
  return (history || []).map(record => ({
    ts: new Date(record.target_ts).getTime(),
    price: Number(record.predicted_price),
  })).filter(point => Number.isFinite(point.ts) && point.ts >= firstTs && point.ts <= lastTs && point.price > 0)
    .sort((a, b) => a.ts - b.ts);
}

function pairDisplayEvents(events) {
  const open = {}, trades = [];
  events.slice().sort((a, b) => Number(a.ts) - Number(b.ts)).forEach(event => {
    const side = event.side || "long";
    if (event.kind === "entry") open[side] = event;
    if (event.kind === "exit" && open[side]) {
      const entry = open[side]; delete open[side];
      trades.push({ entry_ts: entry.ts, entry_price: entry.price, exit_ts: event.ts, exit_price: event.price,
        side, result: event.label });
    }
  });
  return trades;
}

function renderTimelineEvents(events, trades, candles, scale) {
  if (!candles.length) return "";
  const first = Number(candles[0].ts), last = Number(candles.at(-1).ts);
  const visible = events.filter(e => Number(e.ts) >= first && Number(e.ts) <= last && Number(e.price) > 0);
  let svg = "";
  trades.forEach(trade => {
    if (Number(trade.entry_ts) < first || Number(trade.exit_ts) > last) return;
    const i0 = nearestCandleIndex(candles, trade.entry_ts), i1 = nearestCandleIndex(candles, trade.exit_ts);
    if (i0 < 0 || i1 < 0) return;
    const win = String(trade.result || "").toUpperCase() === "TP" ||
      ((trade.side || "long") === "long" ? Number(trade.exit_price) >= Number(trade.entry_price) : Number(trade.exit_price) <= Number(trade.entry_price));
    const tradeClass = win ? "win" : "loss";
    const x0 = scale.x(i0), y0 = scale.y(trade.entry_price), x1 = scale.x(i1), y1 = scale.y(trade.exit_price);
    svg += `<path class="trade-link ${tradeClass}" d="M${x0.toFixed(1)},${y0.toFixed(1)} L${x1.toFixed(1)},${y1.toFixed(1)}"/>`;
    svg += `<circle class="trade-endpoint ${tradeClass}" cx="${x0.toFixed(1)}" cy="${y0.toFixed(1)}" r="3.1"/><circle class="trade-endpoint ${tradeClass}" cx="${x1.toFixed(1)}" cy="${y1.toFixed(1)}" r="3.1"/>`;
  });
  visible.forEach((event, order) => {
    const index = nearestCandleIndex(candles, event.ts), x = scale.x(index), py = scale.y(event.price);
    const raw = String(event.label || (event.kind === "entry" ? "IN" : "OUT")).toUpperCase();
    const kind = event.real ? (raw === "SL" ? "sl" : raw === "TP" ? "tp" : raw === "OUT" ? "out" : "in") : "model";
    const labelY = Math.max(19, py - 18 - (order % 3) * 12);
    svg += `<line class="event-stem ${kind}" x1="${x.toFixed(1)}" y1="${py.toFixed(1)}" x2="${x.toFixed(1)}" y2="${scale.timelineY}"/>`;
    svg += `<circle class="event-halo ${kind}" cx="${x.toFixed(1)}" cy="${py.toFixed(1)}" r="7"/><circle class="event-anchor ${kind}" cx="${x.toFixed(1)}" cy="${py.toFixed(1)}" r="4.2"/>`;
    svg += `<circle class="event-timeline-dot ${kind}" cx="${x.toFixed(1)}" cy="${scale.timelineY}" r="3"/>`;
    svg += `<text class="event-label ${kind}" x="${x.toFixed(1)}" y="${labelY.toFixed(1)}" text-anchor="middle">${esc(raw)}</text>`;
  });
  return svg;
}

function terminalPolyline(values, x, y) {
  return values.map((value, index) => value == null ? null : `${x(index).toFixed(1)},${y(value).toFixed(1)}`).filter(Boolean).join(" ");
}

function persistTerminalVisibility() {
  localStorage.setItem("orum-terminal-visibility", JSON.stringify(terminalVisibility));
}

function applyTerminalVisibility(asset) {
  const card = $(TERMINAL_CARD_IDS[asset]);
  if (!card) return;
  const item = card.closest(".grid-stack-item");
  if (item) item.style.display = terminalVisibility[asset] ? "" : "none";
  const button = document.querySelector(`[data-toggle-asset="${asset}"]`);
  if (button) button.classList.toggle("active", !!terminalVisibility[asset]);
}

function toggleMarketCard(asset, force) {
  terminalVisibility[asset] = force == null ? !terminalVisibility[asset] : !!force;
  persistTerminalVisibility();
  applyTerminalVisibility(asset);
  if (window.__orumGrid && typeof window.__orumGrid.compact === "function") window.__orumGrid.compact();
  if (terminalVisibility[asset] && lastTerminalState) requestAnimationFrame(() => renderMarketTerminal(asset, lastTerminalState));
}
window.toggleMarketCard = toggleMarketCard;

function changeTerminalZoom(asset, direction) {
  const current = terminalZoom[asset] || 120;
  terminalZoom[asset] = direction === "reset" ? 120 : clamp(current + (direction === "out" ? 24 : -24), 36, 220);
  if (lastTerminalState) renderMarketTerminal(asset, lastTerminalState);
}

function bindTerminalPan(chart, asset, maxOffset, pixelsPerBar) {
  chart.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    event.preventDefault(); event.stopPropagation();
    const startX = event.clientX, startOffset = terminalPan[asset] || 0;
    chart.classList.add("panning");
    const move = pointerEvent => {
      const next = clamp(Math.round(startOffset + (pointerEvent.clientX - startX) / Math.max(2, pixelsPerBar)), 0, maxOffset);
      if (next !== terminalPan[asset]) {
        terminalPan[asset] = next;
        if (lastTerminalState) renderMarketTerminal(asset, lastTerminalState);
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
      chart.classList.remove("panning");
    };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up, { once: true });
  });
}

function bindTerminalResize(card, asset) {
  if (!window.ResizeObserver) return;
  if (!terminalResizeObserver) {
    terminalResizeObserver = new ResizeObserver(entries => entries.forEach(entry => {
      const observedAsset = entry.target.dataset.asset;
      clearTimeout(terminalResizeTimers[observedAsset]);
      terminalResizeTimers[observedAsset] = setTimeout(() => {
        if (terminalVisibility[observedAsset] && lastTerminalState) renderMarketTerminal(observedAsset, lastTerminalState);
      }, 80);
    }));
  }
  if (!card.dataset.resizeObserved) {
    card.dataset.resizeObserved = "1";
    terminalResizeObserver.observe(card);
  }
}

function renderMarketTerminal(asset, s) {
  const card = $(TERMINAL_CARD_IDS[asset]);
  if (!card || !terminalVisibility[asset]) return;
  try {
    const sig = (s.market_signals || {})[asset] || {};
    const requestedBars = terminalZoom[asset] || 120;
    const allCandles = (sig.candles || []).filter(c => Number(c.close) > 0);
    const maxOffset = Math.max(0, allCandles.length - requestedBars);
    terminalPan[asset] = clamp(terminalPan[asset] || 0, 0, maxOffset);
    const end = allCandles.length - terminalPan[asset];
    const candles = allCandles.slice(Math.max(0, end - requestedBars), end);
    const marketAsset = sig.asset || asset;
    const position = ((s.paper || {}).open_positions || []).find(
      p => sig.strategy_id ? p.strategy_id === sig.strategy_id : p.symbol === marketAsset
    );
    const title = marketAsset === "PAXG/USDT" ? "OR · PAXG / USDT" : marketAsset.replace("/", " / ");
    const displayTimeframe = sig.display_timeframe || "1h";
    card.innerHTML = `<div class="terminal-head"><div><span class="terminal-kicker">MARCHÉ · PAPER</span><strong>${esc(title)}</strong><span class="hint">${esc(sig.engine || "—")} ${esc(sig.timeframe || "—")} · vue ${esc(displayTimeframe)}</span></div><div class="terminal-controls"><button type="button" data-zoom="out" title="Zoom arrière">−</button><button type="button" data-zoom="in" title="Zoom avant">+</button><button type="button" data-zoom="reset" title="Réinitialiser le zoom">1:1</button><span class="hint">${candles.length} bougies${terminalPan[asset] ? ` · −${terminalPan[asset]} barres` : " · direct"}</span><button type="button" class="terminal-close" title="Fermer cette carte">×</button></div></div><div class="market-terminal"><div class="market-terminal-chart" role="img" aria-label="${esc(title)} : bougies ${esc(displayTimeframe)}, prévision historique +24 h, volumes, momentum, entrées, sorties et scénarios"></div><aside class="market-terminal-rail" aria-label="Indicateurs ${esc(title)}"></aside></div>`;
    card.querySelectorAll("[data-zoom]").forEach(button => button.addEventListener("click", event => {
      event.stopPropagation(); changeTerminalZoom(asset, button.dataset.zoom);
    }));
    card.querySelector(".terminal-close").addEventListener("click", event => { event.stopPropagation(); toggleMarketCard(asset, false); });
    const chart = card.querySelector(".market-terminal-chart"), rail = card.querySelector(".market-terminal-rail");
    chart.addEventListener("wheel", event => {
      event.preventDefault(); changeTerminalZoom(asset, event.deltaY > 0 ? "out" : "in");
    }, { passive: false });
    bindTerminalPan(chart, asset, maxOffset, Math.max(2, (chart.clientWidth || 960) * .78 / Math.max(1, candles.length)));
    bindTerminalResize(card, asset);
    if (!candles.length) {
      chart.innerHTML = `<div class="flat">données ${esc(displayTimeframe)} indisponibles · ${esc(sig.note || "nouvel essai au prochain cycle")}</div>`;
      rail.innerHTML = `<div class="rail-block"><div class="rail-label">État</div><div class="rail-value small">En attente</div></div>`;
      return;
    }

    const W = Math.max(560, Math.round(chart.clientWidth || 960));
    const H = Math.max(300, Math.round(chart.clientHeight || 500));
    const left = 46, fanRight = W - 64, showForecast = terminalPan[asset] === 0;
    const observedRight = showForecast ? left + (fanRight - left) * .82 : fanRight;
    const priceTop = 28, timelineY = H - 23, momBottom = timelineY - 18;
    const momTop = Math.max(priceTop + 105, momBottom - Math.max(42, H * .095));
    const volumeBottom = momTop - 18, volumeTop = Math.max(priceTop + 130, volumeBottom - Math.max(48, H * .105));
    const priceBottom = Math.max(priceTop + 95, volumeTop - 17);
    const calibratedFan = buildCalibratedFan(sig.calibrated_forecast, Number(candles.at(-1).close));
    const fan = showForecast ? (calibratedFan.length ? calibratedFan : buildScenarioFan(candles)) : [];
    const calibrated = calibratedFan.length > 0;
    const forecastHistory = buildForecastHistoryPath(sig.forecast_history_24h || [], candles);
    const realEvents = sig.real_markers || [], modelEvents = sig.markers || [];
    const firstTs = Number(candles[0].ts), lastTs = Number(candles.at(-1).ts);
    const eventPrices = realEvents.concat(modelEvents).filter(e => Number(e.ts) >= firstTs && Number(e.ts) <= lastTs).map(e => Number(e.price)).filter(Number.isFinite);
    const forecastHistoryPrices = forecastHistory.map(point => point.price);
    const levelPrices = position ? [position.stop_loss_price, position.take_profit_price].map(Number).filter(value => Number.isFinite(value) && value > 0) : [];
    const prices = candles.flatMap(c => [Number(c.low), Number(c.high)]).concat(fan.flat(), eventPrices, forecastHistoryPrices, levelPrices);
    let lo = Math.min(...prices), hi = Math.max(...prices), span = Math.max(hi - lo, Math.abs(hi) * .001);
    lo -= span * .08; hi += span * .08;
    const y = value => priceBottom - ((Number(value) - lo) / (hi - lo)) * (priceBottom - priceTop);
    const step = (observedRight - left) / candles.length;
    const x = index => left + step * (index + .5);
    const candleWidth = clamp(step * .58, 1.35, 4.2);
    const closes = candles.map(c => Number(c.close)), ema9 = emaSeries(closes, 9), ema21 = emaSeries(closes, 21);
    const macd = macdParts(closes), hist = closes.map((_, i) => macd.macd[i] != null && macd.signal[i] != null ? macd.macd[i] - macd.signal[i] : null);
    const maxVol = Math.max(...candles.map(c => Number(c.volume) || 0), 1);
    const maxMom = Math.max(...hist.filter(v => v != null).map(Math.abs), .0001);
    const my = value => (momTop + momBottom) / 2 - (Number(value) / maxMom) * ((momBottom - momTop) * .44);
    let svg = `<svg class="terminal-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">`;
    svg += `<text class="section-label" x="${left}" y="15">PRIX ${esc(displayTimeframe.toUpperCase())} · ÉCHELLE VISIBLE +8%</text>`;
    if (showForecast) svg += `<text class="section-label" x="${observedRight + 16}" y="15">${calibrated ? "PRÉVISION · CALIBRÉE" : "SCÉNARIOS · AFFICHAGE SEUL"}</text>`;
    for (let grid = 0; grid <= 4; grid++) {
      const gy = priceTop + (priceBottom - priceTop) * grid / 4, price = hi - (hi - lo) * grid / 4;
      svg += `<line class="grid-line" x1="${left}" y1="${gy}" x2="${fanRight}" y2="${gy}"/><text class="axis-label" x="${fanRight + 8}" y="${gy + 3}">${num(price, price > 1000 ? 0 : 2)}</text>`;
    }
    const timeStride = Math.max(8, Math.floor(candles.length / 7));
    for (let index = 0; index < candles.length; index += timeStride) {
      const gx = x(index), stamp = new Date(Number(candles[index].ts));
      svg += `<line class="grid-line" x1="${gx}" y1="${priceTop}" x2="${gx}" y2="${timelineY}"/><text class="axis-label" x="${gx}" y="${H-5}" text-anchor="middle">${String(stamp.getUTCDate()).padStart(2,"0")}/${String(stamp.getUTCMonth()+1).padStart(2,"0")} ${String(stamp.getUTCHours()).padStart(2,"0")}h</text>`;
    }
    svg += `<line class="axis-line" x1="${left}" y1="${timelineY}" x2="${fanRight}" y2="${timelineY}"/>`;
    candles.forEach((c, index) => {
      const cx = x(index), up = Number(c.close) >= Number(c.open), clsName = up ? "up" : "down";
      const top = Math.min(y(c.open), y(c.close)), bodyH = Math.max(1.1, Math.abs(y(c.close) - y(c.open)));
      const vh = (Number(c.volume) / maxVol) * (volumeBottom - volumeTop);
      svg += `<line class="wick-${clsName}" x1="${cx.toFixed(1)}" y1="${y(c.high).toFixed(1)}" x2="${cx.toFixed(1)}" y2="${y(c.low).toFixed(1)}"/>`;
      svg += `<rect class="candle-${clsName}" x="${(cx-candleWidth/2).toFixed(1)}" y="${top.toFixed(1)}" width="${candleWidth.toFixed(2)}" height="${bodyH.toFixed(1)}"/>`;
      svg += `<rect class="volume-${clsName}" x="${(cx-candleWidth/2).toFixed(1)}" y="${(volumeBottom-vh).toFixed(1)}" width="${candleWidth.toFixed(2)}" height="${vh.toFixed(1)}"/>`;
      if (hist[index] != null) {
        const hy = my(hist[index]), zero = my(0);
        svg += `<rect class="momentum-bar-${hist[index] >= 0 ? "pos" : "neg"}" x="${(cx-candleWidth/2).toFixed(1)}" y="${Math.min(hy,zero).toFixed(1)}" width="${candleWidth.toFixed(2)}" height="${Math.max(1,Math.abs(zero-hy)).toFixed(1)}"/>`;
      }
    });
    svg += `<polyline class="ema-fast" points="${terminalPolyline(ema9, x, y)}"/><polyline class="ema-slow" points="${terminalPolyline(ema21, x, y)}"/>`;
    if (forecastHistory.length > 1) {
      const points = forecastHistory.map(point => {
        const index = nearestCandleIndex(candles, point.ts);
        return `${x(index).toFixed(1)},${y(point.price).toFixed(1)}`;
      }).join(" ");
      svg += `<polyline class="forecast-history-line" points="${points}"/>`;
    }
    if (position && Number(position.stop_loss_price) > 0) {
      const level = Number(position.stop_loss_price);
      svg += `<line class="position-level stop" x1="${left}" y1="${y(level)}" x2="${observedRight}" y2="${y(level)}"/><text class="position-level-label stop" x="${left + 5}" y="${y(level)-5}">SL ${num(level,2)}</text>`;
    }
    if (position && Number(position.take_profit_price) > 0) {
      const level = Number(position.take_profit_price);
      svg += `<line class="position-level take" x1="${left}" y1="${y(level)}" x2="${observedRight}" y2="${y(level)}"/><text class="position-level-label take" x="${left + 5}" y="${y(level)-5}">TP ${num(level,2)}</text>`;
    }
    svg += `<line class="grid-line" x1="${left}" y1="${volumeBottom}" x2="${fanRight}" y2="${volumeBottom}"/><text class="section-label" x="${left}" y="${volumeTop-8}">VOLUME</text>`;
    svg += `<line class="grid-line" x1="${left}" y1="${my(0)}" x2="${observedRight}" y2="${my(0)}"/><text class="section-label" x="${left}" y="${momTop-7}">MOMENTUM MACD ${esc(displayTimeframe.toUpperCase())}</text>`;
    const fanX = index => observedRight + (fanRight-observedRight) * index / 24;
    const centralIndex = calibrated ? 2 : 7;
    const central = fan[centralIndex] || [];
    const observedJoinX = x(candles.length - 1), observedJoinY = y(candles.at(-1).close);
    if (showForecast) svg += `<line class="history-future-divider" x1="${fanX(0)}" y1="${priceTop}" x2="${fanX(0)}" y2="${timelineY}"/>`;
    if (central[0] != null) {
      svg += `<path class="scenario-join" d="M${observedJoinX.toFixed(1)},${observedJoinY.toFixed(1)} L${fanX(0).toFixed(1)},${y(central[0]).toFixed(1)}"/><circle class="scenario-join-node" cx="${fanX(0).toFixed(1)}" cy="${y(central[0]).toFixed(1)}" r="3.4"/>`;
    }
    fan.forEach((path, index) => svg += `<polyline class="scenario-path ${index === centralIndex ? "central" : ""}" points="${terminalPolyline(path, fanX, y)}"/>`);
    [6, 12, 18, 24].forEach((milestone, i) => {
      if (central[milestone] == null) return;
      const mx = fanX(milestone), py = y(central[milestone]);
      svg += `<line class="scenario-stem" x1="${mx}" y1="${py}" x2="${mx}" y2="${timelineY}"/><circle class="scenario-node" cx="${mx}" cy="${py}" r="3.1"/><text class="axis-label" x="${mx}" y="${Math.max(20,py-8)}" text-anchor="middle">+${milestone}h</text>`;
    });
    const eventScale = { x, y, timelineY };
    svg += renderTimelineEvents(modelEvents.concat(realEvents), (sig.trades || []).concat(pairDisplayEvents(realEvents)), candles, eventScale);
    svg += `</svg>`;
    chart.innerHTML = svg;

    const last = candles.at(-1), previous = candles.at(-2), move = Number(last.close) / Number(previous.close) - 1;
    const rsi = rsiSeries(closes).at(-1), atr = atrSeries(candles.map(c=>c.high), candles.map(c=>c.low), closes).at(-1);
    const unrealized = position ? Number(position.qty || 0) * (Number(last.close) - Number(position.entry_px || 0)) : 0;
    const exitPolicy = position ? ({
      structural_bracket: "bracket figé · contrôle M15",
      signal_or_stop: "sortie signal UT ou stop",
      donchian_signal: "sortie canal Donchian 10 j",
      cot_signal: "sortie signal COT",
      strategy_signal: "sortie signal stratégie",
    }[position.exit_policy] || position.exit_policy || "sortie non renseignée") : "";
    rail.innerHTML = `
      <div class="rail-block"><div class="rail-label">Dernier prix</div><div class="rail-value ${cls(move)}">${num(last.close, Number(last.close)>1000?2:3)}</div><div class="rail-sub">bougie ${hhmmss(last.ts)} UTC · ${signed(move, pct)}</div></div>
      <div class="rail-block"><div class="rail-label">Moteur actif sur cet actif</div><div class="rail-value small">${esc(sig.engine || "—")}</div><div class="rail-sub">signal ${esc(sig.timeframe || "—")} · affichage ${esc(sig.display_timeframe || "1h")}</div></div>
      <div class="rail-block"><div class="rail-label">Indicateurs ${esc(displayTimeframe)}</div><div class="rail-row"><span>RSI 14</span><span>${rsi == null ? "—" : num(rsi,1)}</span></div><div class="rail-row"><span>ATR 14</span><span>${atr == null ? "—" : num(atr,2)}</span></div><div class="rail-row"><span>MACD hist.</span><span class="${cls(hist.at(-1))}">${hist.at(-1)==null?"—":num(hist.at(-1),3)}</span></div><div class="rail-row"><span>volume</span><span>${num(last.volume,0)}</span></div></div>
      <div class="rail-block"><div class="rail-label">Position paper</div>${position ? `<div class="rail-status">OUVERTE · ${position.side === "short" ? "S" : "L"}</div><div class="rail-row"><span>entrée / actuel</span><span>${num(position.entry_px,2)} / ${num(last.close,2)}</span></div><div class="rail-row"><span>non réalisé</span><span class="${cls(unrealized)}">${signed(unrealized, usd)}</span></div><div class="rail-row"><span>SL appliqué</span><span>${Number(position.stop_loss_price)>0?num(position.stop_loss_price,2):"—"}</span></div><div class="rail-row"><span>TP appliqué</span><span>${Number(position.take_profit_price)>0?num(position.take_profit_price,2):"—"}</span></div><div class="rail-row"><span>politique</span><span>${esc(exitPolicy)}</span></div><div class="rail-sub">ouverte ${ago(position.opened_ts)} · contrôle ${esc(position.monitor_timeframe || position.timeframe || "signal")}</div>` : `<div class="rail-value small">Aucune position</div><div class="rail-sub">portefeuille commun · ${(s.paper||{}).open_count||0} ouverte(s) au total</div>`}</div>
      <div class="rail-block"><div class="rail-label">Prévision +6 / +12 / +24 h</div>${sig.calibrated_forecast && sig.calibrated_forecast.decision ? `<div class="rail-status">${esc((sig.calibrated_forecast.decision.action || "locked").toUpperCase())} · x${num(sig.calibrated_forecast.decision.multiplier ?? 1,2)}</div>` : ""}<div class="scenario-disclaimer">${esc(sig.scenario_note || "Éventail de stress visuel. Affichage seul, jamais envoyé au moteur.")}</div><div class="rail-sub">Historique prévision +24 h · ${(sig.forecast_history_24h||[]).length} points</div></div>`;
  } catch (err) {
    console.error(`renderMarketTerminal ${asset} crash:`, err);
    card.innerHTML = `<div class="flat neg">rendu ${esc(asset)} indisponible · ${esc(err.message)}</div>`;
  }
}

function renderProChart(s) {
  lastTerminalState = s;
  TERMINAL_ASSETS.forEach(asset => {
    applyTerminalVisibility(asset);
    if (terminalVisibility[asset]) renderMarketTerminal(asset, s);
  });
}

document.querySelectorAll("[data-toggle-asset]").forEach(button => {
  button.addEventListener("click", () => toggleMarketCard(button.dataset.toggleAsset));
});

/* ---- worker control + alert ------------------------------------------ */
let workerBusy = false;
function renderWorker(s) {
  const w = s.worker || {};
  const legacy = s.legacy_audit || {};
  const running = !!w.running;
  const stale = !!w.stale;
  const age = w.heartbeat_age_seconds != null ? ago(new Date(Date.now() - w.heartbeat_age_seconds * 1000).toISOString()) : "—";

  // Header pill (status) + action button, right next to the brand.
  const state = !running ? "off" : stale ? "stale" : "live";
  const label = !running ? "WORKER OFF" : stale ? "WORKER STALE" : "WORKER LIVE";
  $("worker-control").innerHTML =
    `<span class="wpill ${state}"><span class="dot ${state === "live" ? "live" : "stale"}"></span><b>PAPER ${label.replace("WORKER ", "")}</b><span class="k">${running ? age : "stopped"}</span></span>`;

  // Banner: loud only when something needs attention.
  const alert = $("worker-alert");
  if (legacy.process_running) {
    alert.style.display = "flex";
    alert.className = "worker-alert warn";
    alert.innerHTML = `<span class="ico">⚠</span><div class="txt"><b>Ancien moteur encore actif</b> — ses trades et candidats sont non autoritatifs et séparés du portefeuille unifié. <span>Son arrêt opérationnel exige une confirmation explicite.</span></div>`;
  } else if (!running) {
    alert.style.display = "flex";
    alert.className = "worker-alert";
    alert.innerHTML = `<span class="ico">×</span><div class="txt"><b>Portefeuille paper arrêté</b> — aucune donnée ni trade unifié ne sera produit. <span>Dernier cycle il y a ${age}.</span></div>`;
  } else if (stale) {
    alert.style.display = "flex";
    alert.className = "worker-alert warn";
    alert.innerHTML = `<span class="ico">!</span><div class="txt"><b>Portefeuille paper silencieux</b> — aucun cycle récent. <span>Vérifier com.0rum.paper avant toute action.</span></div>`;
  } else {
    alert.style.display = "none";
  }
}

async function postWorker(path) {
  const r = await fetch(path, { method: "POST" });
  let body = {};
  try { body = await r.json(); } catch (e) { /* non-JSON */ }
  if (!r.ok) {
    // A 404 here almost always means the dashboard SERVER is running old code
    // (endpoints added after it started) — surface it instead of failing silently.
    const hint = r.status === 404 ? " — redémarre le serveur dashboard (python -m orum.dashboard)" : "";
    throw new Error(`HTTP ${r.status}${body.error ? ": " + body.error : ""}${hint}`);
  }
  return body;
}

async function workerAction(action) {
  if (workerBusy) return;
  workerBusy = true;
  $("worker-status").textContent = action + "…";
  renderWorker(window.__lastState || {});
  try {
    if (action === "toggle") {
      const running = (window.__lastState || {}).worker && window.__lastState.worker.running;
      action = running ? "stop" : "start";
    }
    if (action === "restart") {
      await postWorker("/api/worker/stop");
      await new Promise((r) => setTimeout(r, 700));
      await postWorker("/api/worker/start");
    } else {
      await postWorker("/api/worker/" + action);
    }
    $("worker-status").textContent = action + " ✓";
  } catch (e) {
    $("worker-status").textContent = "✗ " + e.message;
  } finally {
    setTimeout(() => { workerBusy = false; tick(); $("worker-status").textContent = ""; }, 3000);
    tick();
  }
}
window.workerAction = workerAction;

/* ---- poll loop -------------------------------------------------------- */
async function tick() {
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const s = await r.json();
    window.__lastState = s;
    renderTop(s); renderWorker(s); renderKpis(s); renderProChart(s); renderStats(s);
    renderPosition(s); renderEquity(s); renderStrategy(s); renderLeverage(s); renderExternal(s); renderLogs(s); renderTrades(s); renderResearchPortfolio(s);
    $("clock").textContent = new Date().toTimeString().slice(0, 8);
    $("conn").textContent = "● live"; $("conn").classList.remove("down");
  } catch (err) {
    $("conn").textContent = "● disconnected"; $("conn").classList.add("down");
  }
}


$("reflect-btn").addEventListener("click", async () => {
  const btn = $("reflect-btn"), status = $("reflect-status");
  btn.disabled = true; status.textContent = "reflecting…";
  try {
    const r = await fetch("/api/reflect", { method: "POST" });
    const j = await r.json();
    status.textContent = j.ok ? "reflection done" : `error: ${j.error || r.status}`;
  } catch (e) { status.textContent = "error: " + e; }
  finally { setTimeout(() => { btn.disabled = false; status.textContent = ""; }, 4000); }
});

tick();
setInterval(tick, 3000);

/* ====================================================================== */
/* Fenêtres déplaçables / redimensionnables (GridStack, 2026-07-05)       */
/* Drag par le titre (h2) · resize bord bas/droit · layout en localStorage */
/* ====================================================================== */
const DASHBOARD_LAYOUT_PRESETS = {
  column: [
    ["market-btc",0,0,12,8],["market-btc-utbot",0,8,12,8],["market-eth",0,16,12,8],["market-paxg",0,24,12,8],
    ["position",0,32,12,3],["stats",0,35,12,3],["research",0,38,12,5],["equity",0,43,12,3],
    ["trades",0,46,12,4],["strategy",0,50,12,4],["leverage",0,54,12,9],["external",0,63,12,4],["log",0,67,12,4],
  ],
  "two-column": [
    ["market-btc",0,0,6,8],["market-btc-utbot",6,0,6,8],["market-eth",0,8,6,8],["market-paxg",6,8,6,8],
    ["position",0,16,6,3],["stats",6,16,6,3],["research",0,19,6,5],["equity",6,19,6,5],
    ["strategy",0,24,6,4],["leverage",6,24,6,9],["trades",0,28,6,4],["external",6,33,6,4],["log",0,37,12,4],
  ],
  "aligned-wall": [
    ["market-btc",0,0,6,7],["market-btc-utbot",6,0,6,7],["market-eth",0,7,6,7],["market-paxg",6,7,6,7],
    ["position",0,14,4,4],["stats",4,14,4,4],["equity",8,14,4,4],["research",0,18,8,5],
    ["trades",8,18,4,5],["strategy",0,23,4,4],["leverage",4,23,4,9],["external",8,23,4,4],["log",0,32,12,4],
  ],
};
Object.keys(DASHBOARD_LAYOUT_PRESETS).forEach(name => {
  DASHBOARD_LAYOUT_PRESETS[name] = DASHBOARD_LAYOUT_PRESETS[name].map(([id,x,y,w,h]) => ({ id, x, y, w, h }));
});
const DASHBOARD_LAYOUT_IDS = DASHBOARD_LAYOUT_PRESETS.column.map(item => item.id);
const PERSONAL_LAYOUT_KEY = "orum-dash-layout-v4-personal";
const ACTIVE_LAYOUT_KEY = "orum-dash-layout-v4-active";
const LEGACY_LAYOUT_KEY = "orum-dash-layout-v3";
let applyingLayoutPreset = false;

function safeStorageGet(key) {
  try { return localStorage.getItem(key); }
  catch (error) { console.warn("layout storage read failed:", error); return null; }
}
function safeStorageSet(key, value) {
  try { localStorage.setItem(key, value); return true; }
  catch (error) { console.warn("layout storage write failed:", error); return false; }
}
function safeStorageRemove(key) {
  try { localStorage.removeItem(key); return true; }
  catch (error) { console.warn("layout storage removal failed:", error); return false; }
}
function isValidDashboardLayout(layout) {
  if (!Array.isArray(layout) || layout.length !== DASHBOARD_LAYOUT_IDS.length) return false;
  const seen = new Set();
  const valid = layout.every(item => {
    if (!item || !DASHBOARD_LAYOUT_IDS.includes(item.id) || seen.has(item.id)) return false;
    seen.add(item.id);
    const values = [item.x, item.y, item.w, item.h].map(Number);
    if (!values.every(Number.isInteger)) return false;
    const [x, y, w, h] = values;
    return x >= 0 && y >= 0 && w > 0 && h > 0 && x + w <= 12;
  });
  return valid && seen.size === DASHBOARD_LAYOUT_IDS.length;
}

(function initGridLayout() {
  const presetButtons = document.querySelectorAll("[data-layout-preset]");
  const disablePresetButtons = () => presetButtons.forEach(button => { button.disabled = true; });
  if (!window.GridStack) {
    document.documentElement.classList.add("gridstack-fallback");
    disablePresetButtons();
    return; // CDN indisponible -> fallback: cartes empilées
  }
  try {
    const grid = GridStack.init({
      column: 12, cellHeight: 72, margin: 7, float: true,
      handle: "h2, .terminal-head", resizable: { handles: "se,e,s" },
    });
    window.__orumGrid = grid;

    const renderLayoutPresetState = mode => {
      presetButtons.forEach(button => {
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
      safeStorageSet(ACTIVE_LAYOUT_KEY, name);
      renderLayoutPresetState(name);
      requestAnimationFrame(() => {
        applyingLayoutPreset = false;
        rerenderVisibleMarkets();
      });
    };
    const savePersonalLayout = () => {
      if (applyingLayoutPreset) return;
      safeStorageSet(PERSONAL_LAYOUT_KEY, JSON.stringify(grid.save(false)));
      safeStorageSet(ACTIVE_LAYOUT_KEY, "custom");
      renderLayoutPresetState("custom");
    };

    presetButtons.forEach(button => {
      button.addEventListener("click", () => applyLayoutPreset(button.dataset.layoutPreset));
    });
    grid.on("change", savePersonalLayout);
    grid.on("dragstop", savePersonalLayout);
    grid.on("resizestop", () => {
      savePersonalLayout();
      rerenderVisibleMarkets();
    });

    try {
      if (!safeStorageGet(PERSONAL_LAYOUT_KEY)) {
        const legacy = safeStorageGet(LEGACY_LAYOUT_KEY);
        if (legacy) safeStorageSet(PERSONAL_LAYOUT_KEY, legacy);
      }
      const active = safeStorageGet(ACTIVE_LAYOUT_KEY) ||
        (safeStorageGet(PERSONAL_LAYOUT_KEY) ? "custom" : "two-column");
      if (active === "custom") {
        const personal = JSON.parse(safeStorageGet(PERSONAL_LAYOUT_KEY) || "null");
        if (!isValidDashboardLayout(personal)) throw new Error("invalid personal layout");
        applyingLayoutPreset = true;
        grid.load(personal, false);
        renderLayoutPresetState("custom");
        requestAnimationFrame(() => {
          applyingLayoutPreset = false;
          rerenderVisibleMarkets();
        });
      } else {
        applyLayoutPreset(DASHBOARD_LAYOUT_PRESETS[active] ? active : "two-column");
      }
    } catch (error) {
      safeStorageRemove(PERSONAL_LAYOUT_KEY);
      applyLayoutPreset("two-column");
    }

    const btn = $("layout-reset-btn");
    if (btn) btn.addEventListener("click", () => {
      safeStorageRemove(PERSONAL_LAYOUT_KEY);
      safeStorageRemove(ACTIVE_LAYOUT_KEY);
      safeStorageRemove("orum-terminal-visibility");
      Object.assign(terminalVisibility, {
        "BTC/USDT": true,
        "BTC/USDT::btc_utbot_m15_h1": true,
        "ETH/USDT": false,
        "PAXG/USDT": false,
      });
      TERMINAL_ASSETS.forEach(applyTerminalVisibility);
      applyLayoutPreset("two-column");
    });
  } catch (e) {
    document.documentElement.classList.add("gridstack-fallback");
    disablePresetButtons();
    console.warn("gridstack init failed:", e);
  }
})();
