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

/* ====================================================================== */
/* Price chart — candles + volume + RSI + markers + zoom/pan              */
/* ====================================================================== */
const CH = { W: 1000, H: 390, padL: 56, padR: 12, top: 10, pxB: 250, volT: 258, volB: 300, rsiT: 308, rsiB: 378 };
const P = { series: [], ema9: [], ema21: [], ema50: [], rsi: [], markers: [], view: null, follow: true, drag: null, init: false };

function setPriceData(s) {
  let series = (s.price_series || []).filter((p) => p.close > 0);
  if (s.last_price > 0 && series.length) {
    const lp = Number(s.last_price);
    series = series.concat([{ ts: Date.now(), open: lp, high: lp, low: lp, close: lp, volume: 0 }]);
  }
  const prevLen = P.series.length;
  P.series = series;
  const closes = series.map((p) => p.close);
  P.ema9 = emaSeries(closes, 9); P.ema21 = emaSeries(closes, 21); P.ema50 = emaSeries(closes, 50);
  P.rsi = rsiSeries(closes, 14);
  P.markers = s.trade_markers || [];
  const n = series.length;
  if (!P.view || prevLen < 2) {
    const span = Math.min(n, 160);
    P.view = { start: Math.max(0, n - span), end: n - 1 };
    P.follow = true;
  } else {
    const span = P.view.end - P.view.start + 1;
    if (P.follow) P.view = { start: Math.max(0, n - span), end: n - 1 };
    else P.view = { start: clamp(P.view.start, 0, n - 2), end: clamp(P.view.end, 1, n - 1) };
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
  const t0 = P.series[start].ts, t1 = P.series[end].ts;
  const mk = P.markers.filter((m) => m.entry_ts >= t0 && m.entry_ts <= t1);
  mk.forEach((m) => { pmin = Math.min(pmin, m.entry_price, m.exit_price || m.entry_price); pmax = Math.max(pmax, m.entry_price, m.exit_price || m.entry_price); });
  const padY = (pmax - pmin) * 0.06 || 1; pmin -= padY; pmax += padY;
  const YP = (p) => top + (1 - (p - pmin) / (pmax - pmin || 1)) * (pxB - top);
  const YV = (v) => volB - (v / (vmax || 1)) * (volB - volT);
  const YR = (v) => rsiT + (1 - v / 100) * (rsiB - rsiT);
  const cw = Math.max(1, (plotW / visN) * 0.66);

  let g = "";
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
  g += `<rect class="rsi-band" x="${padL}" y="${YR(70).toFixed(1)}" width="${plotW}" height="${(YR(30) - YR(70)).toFixed(1)}"/>`;
  g += `<text class="strip-lbl" x="${padL - 6}" y="${YR(70)}" text-anchor="end" dominant-baseline="middle">70</text><text class="strip-lbl" x="${padL - 6}" y="${YR(30)}" text-anchor="end" dominant-baseline="middle">30</text>`;

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
  g += `<path class="ema50" d="${emaPath(P.ema50)}"/><path class="ema21" d="${emaPath(P.ema21)}"/><path class="ema9" d="${emaPath(P.ema9)}"/>`;
  // RSI
  let rp = "", on = false;
  for (let i = start; i <= end; i++) { if (P.rsi[i] == null) continue; rp += `${on ? "L" : "M"}${xAt(i).toFixed(1)},${YR(P.rsi[i]).toFixed(1)} `; on = true; }
  g += `<path class="rsi-line" d="${rp}"/>`;

  // trade markers
  mk.forEach((m) => {
    const ex = xAt(tsToIndexFloat(m.entry_ts)), ey = YP(m.entry_price);
    if (m.exit_price > 0 && m.exit_ts && m.exit_ts >= t0 && m.exit_ts <= t1) {
      const xx = xAt(tsToIndexFloat(m.exit_ts)), xy = YP(m.exit_price);
      g += `<line class="mk-link" x1="${ex.toFixed(1)}" y1="${ey.toFixed(1)}" x2="${xx.toFixed(1)}" y2="${xy.toFixed(1)}"/>`;
      g += `<rect class="${m.win ? "mk-exit-win" : "mk-exit-loss"}" x="${(xx - 4).toFixed(1)}" y="${(xy - 4).toFixed(1)}" width="8" height="8"><title>exit ${usd(m.exit_price)} · ${m.exit_reason || ""} · ${signed(m.pnl_pct, (v) => pct(v))}</title></rect>`;
    }
    g += `<path class="mk-entry" d="M${ex.toFixed(1)},${(ey - 6).toFixed(1)} L${(ex - 5).toFixed(1)},${(ey + 4).toFixed(1)} L${(ex + 5).toFixed(1)},${(ey + 4).toFixed(1)} Z"><title>entry ${usd(m.entry_price)} · ${m.side}</title></path>`;
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
  if (P.series.length < 2) { card.innerHTML = `<h2>BTC price · trades</h2><div class="flat">waiting for price history…</div>`; P.init = false; return; }
  if (!P.init) {
    card.innerHTML = `
      <h2>BTC price · trades <span class="hint" id="px-hint"></span><span class="zoom-hint">scroll = zoom · drag = pan</span></h2>
      <div class="chartwrap">
        <svg class="pricechart" viewBox="0 0 ${CH.W} ${CH.H}" preserveAspectRatio="none"></svg>
        <div id="chart-tip"></div>
      </div>
      <div class="chart-legend">
        <span><i style="border-color:#2ecc71"></i>candles</span>
        <span><i style="border-color:#6ee7d7"></i>EMA9</span>
        <span><i style="border-color:#4aa3ff"></i>EMA21</span>
        <span><i style="border-color:#b07cff"></i>EMA50</span>
        <span><i class="tri"></i>entry</span>
        <span><i class="sq" style="background:#2ecc71"></i>win</span>
        <span><i class="sq" style="background:#ff5765"></i>loss</span>
        <span>VOL · RSI(14) strips</span>
      </div>`;
    bindPriceInteractions(card.querySelector("svg"), card.querySelector(".chartwrap"));
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
    ns = clamp(ns, 0, n - newSpan);
    P.view = { start: ns, end: ns + newSpan - 1 };
    P.follow = P.view.end >= n - 1;
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
      let ns = clamp(P.drag.start - dIdx, 0, n - span);
      P.view = { start: ns, end: ns + span - 1 };
      P.follow = P.view.end >= n - 1;
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
  const stale = s.worker && s.worker.stale;
  const hbAge = s.worker ? s.worker.heartbeat_age_seconds : null;
  const gg = s.guardrail || {};
  const gMap = { normal: "ok", caution: "warn", review: "warn", kill: "bad" };
  const src = s.signal_source || "native";
  const chips = [
    `<span class="chip"><span class="dot ${stale ? "stale" : "live"}"></span><b>${stale ? "WORKER STALE" : "WORKER LIVE"}</b>${hbAge != null ? `<span class="k">${ago(new Date(Date.now() - hbAge * 1000).toISOString())} ago</span>` : ""}</span>`,
    `<span class="chip info"><span class="k">asset</span><b>${esc(s.asset)}</b></span>`,
    `<span class="chip"><span class="k">mode</span><b>${esc(s.mode || "paper")}</b></span>`,
    `<span class="chip ${src === "tradingview_external" ? "info" : ""}"><span class="k">signals</span><b>${src === "tradingview_external" ? "TV EXTERNAL" : "NATIVE"}</b></span>`,
    `<span class="chip ${gMap[gg.status] || ""}"><span class="k">guardrail</span><b>${esc(gg.label || gg.status || "—")}</b></span>`,
  ];
  $("topchips").innerHTML = chips.join("");
}

/* ---- KPIs ------------------------------------------------------------- */
function renderKpis(s) {
  const p = s.portfolio || {};
  const dd = Number(s.drawdown || 0);
  const ddMax = (s.goal && s.goal.max_drawdown) || 0.05;
  const tiles = [
    { label: "Balance", val: usd(p.balance_usd), sub: `start ${usd(p.starting_balance_usd, 0)}` },
    { label: "P&L", val: signed(p.pnl_usd, usd), sub: signed(p.pnl_pct, pct), cls: cls(p.pnl_usd) },
    { label: "Drawdown", val: pct(dd), sub: `max ${pct(ddMax)}`, cls: dd >= ddMax ? "neg" : "" },
    { label: "Win rate", val: pct(s.win_rate, 1), sub: `${s.trade_count || 0} trades` },
    { label: "Avg trade", val: signed(s.avg_trade, (v) => pct(v)), sub: `best ${signed(s.best_trade, (v) => pct(v))}`, cls: cls(s.avg_trade) },
    { label: "Score", val: num(s.score, 3), sub: `worst ${signed(s.worst_trade, (v) => pct(v))}` },
  ];
  $("kpis").innerHTML = tiles
    .map((t) => `<div class="kpi ${t.cls || ""}"><div class="label">${t.label}</div><div class="val">${t.val}</div><div class="sub">${t.sub}</div></div>`)
    .join("");
}

/* ---- open position ---------------------------------------------------- */
function renderPosition(s) {
  const card = $("position-card");
  const p = s.open_position || {};
  if (!p.active) { card.innerHTML = `<h2>Open position <span class="hint">live</span></h2><div class="flat">— FLAT — no open position</div>`; return; }
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
  const risk = (k, d) => (st[k] != null ? st[k] : d);
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
/* Pro Chart (Lightweight Charts)                                         */
/* ====================================================================== */
let proChart = null;
let proCandleSeries = null;
let proVolumeSeries = null;
let proEma9Series = null;
let proEma21Series = null;
let proEma50Series = null;

function renderProChart(s) {
  const card = $("pro-chart-card");
  card.style.display = "block";

  if (!proChart && window.LightweightCharts) {
    const container = $("tv-chart");
    const chartOptions = {
      layout: { textColor: '#d7dee8', background: { type: 'solid', color: '#11161f' } },
      grid: { vertLines: { color: '#1a212c' }, horzLines: { color: '#1a212c' } },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#232b38' },
      rightPriceScale: { borderColor: '#232b38' },
    };
    proChart = LightweightCharts.createChart(container, chartOptions);
    
    proVolumeSeries = proChart.addHistogramSeries({
      color: '#26a69a',
      priceFormat: { type: 'volume' },
      priceScaleId: '', 
      scaleMargins: { top: 0.8, bottom: 0 },
    });
    
    proCandleSeries = proChart.addCandlestickSeries({
      upColor: '#2ecc71',
      downColor: '#ff5765',
      borderVisible: false,
      wickUpColor: '#2ecc71',
      wickDownColor: '#ff5765',
    });

    proEma50Series = proChart.addLineSeries({ color: '#b07cff', lineWidth: 1, crosshairMarkerVisible: false });
    proEma21Series = proChart.addLineSeries({ color: '#4aa3ff', lineWidth: 1, crosshairMarkerVisible: false });
    proEma9Series = proChart.addLineSeries({ color: '#6ee7d7', lineWidth: 1, crosshairMarkerVisible: false });

    new ResizeObserver(entries => {
      if (entries.length === 0 || entries[0].target !== container) { return; }
      const newRect = entries[0].contentRect;
      proChart.applyOptions({ height: newRect.height, width: newRect.width });
    }).observe(container);
  }

  if (!proChart) return;
  let rawSeries = [];
  if (s.price_series && s.price_series.length >= 2) {
    rawSeries = s.price_series.filter(p => p.close > 0);
  } else if (s.candles && s.candles.length >= 2) {
    rawSeries = s.candles.map(c => ({
      ts: new Date(c.ts).getTime(),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: 0
    })).filter(p => p.close > 0 && !isNaN(p.ts));
  }

  if (rawSeries.length < 2) return;

  if (s.last_price > 0 && rawSeries.length) {
    const lp = Number(s.last_price);
    rawSeries = rawSeries.concat([{ ts: Date.now(), open: lp, high: lp, low: lp, close: lp, volume: 0 }]);
  }

  const cData = rawSeries.map(c => ({
    time: c.ts / 1000,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close
  }));

  const vData = rawSeries.map(c => ({
    time: c.ts / 1000,
    value: c.volume,
    color: c.close >= c.open ? 'rgba(46, 204, 113, 0.3)' : 'rgba(255, 87, 101, 0.3)'
  }));

  const uniqueCandles = [];
  const uniqueVolumes = [];
  const seenTimes = new Set();
  
  for (let i = 0; i < cData.length; i++) {
    const t = Math.floor(cData[i].time);
    if (!seenTimes.has(t)) {
      seenTimes.add(t);
      uniqueCandles.push({ ...cData[i], time: t });
      uniqueVolumes.push({ ...vData[i], time: t });
    }
  }

  uniqueCandles.sort((a, b) => a.time - b.time);
  uniqueVolumes.sort((a, b) => a.time - b.time);

  proCandleSeries.setData(uniqueCandles);
  proVolumeSeries.setData(uniqueVolumes);

  const closes = uniqueCandles.map(c => c.close);
  const ema9 = emaSeries(closes, 9);
  const ema21 = emaSeries(closes, 21);
  const ema50 = emaSeries(closes, 50);

  const formatEma = (arr) => arr.map((val, i) => val == null ? null : ({ time: uniqueCandles[i].time, value: val })).filter(x => x !== null);

  const d50 = formatEma(ema50); if (d50.length) proEma50Series.setData(d50);
  const d21 = formatEma(ema21); if (d21.length) proEma21Series.setData(d21);
  const d9 = formatEma(ema9); if (d9.length) proEma9Series.setData(d9);

  const markers = [];
  const t0 = uniqueCandles[0].time;
  const t1 = uniqueCandles[uniqueCandles.length - 1].time;

  (s.trade_markers || []).forEach(m => {
    const entryTime = Math.floor(m.entry_ts / 1000);
    if (entryTime >= t0 && entryTime <= t1) {
      markers.push({
        time: entryTime,
        position: m.side === 'long' ? 'belowBar' : 'aboveBar',
        color: m.side === 'long' ? '#2ecc71' : '#ff5765',
        shape: m.side === 'long' ? 'arrowUp' : 'arrowDown',
        text: 'Entry ' + m.side
      });
    }
    if (m.exit_price > 0 && m.exit_ts) {
      const exitTime = Math.floor(m.exit_ts / 1000);
      if (exitTime >= t0 && exitTime <= t1) {
        markers.push({
          time: exitTime,
          position: m.win ? 'aboveBar' : 'belowBar',
          color: m.win ? '#2ecc71' : '#ff5765',
          shape: 'circle',
          text: 'Exit'
        });
      }
    }
  });

  markers.sort((a, b) => a.time - b.time);
  
  // Dedup markers with same time
  const dedupMarkers = [];
  const seenMarkerTimes = new Set();
  for (const mark of markers) {
    let t = mark.time;
    while (seenMarkerTimes.has(t)) t++; // slightly shift time to avoid exact overlap if possible, or just ignore. Actually lightweight charts allows multiple markers if they have exact same time? Wait, lightweight charts requires strict ascending time OR same time but different items? It's better to just ensure no duplicates by replacing or shifting. Let's just keep the last one or skip.
    if (!seenMarkerTimes.has(t)) {
      seenMarkerTimes.add(t);
      dedupMarkers.push({ ...mark, time: t });
    }
  }

  try { proCandleSeries.setMarkers(dedupMarkers.sort((a,b) => a.time - b.time)); } catch (e) { console.error('Marker error:', e); }

  const last = uniqueCandles[uniqueCandles.length - 1].close;
  const hint = $("pro-px-hint");
  if (hint) hint.textContent = `BTC: ${num(last, 2)}`;
}

/* ---- poll loop -------------------------------------------------------- */
async function tick() {
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const s = await r.json();
    renderTop(s); renderKpis(s); renderProChart(s); renderPrice(s); renderStats(s);
    renderPosition(s); renderEquity(s); renderStrategy(s); renderExternal(s); renderLogs(s);
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

// Stub out DOM functions
const elements = {};
global.$ = (id) => {
  if (!elements[id]) {
    elements[id] = { innerHTML: '', textContent: '', style: {}, classList: { add: ()=>{}, remove: ()=>{} }, querySelector: ()=>({ setAttribute: ()=>{}, style: {}, addEventListener: ()=>{} }) };
  }
  return elements[id];
};
global.window = {};
global.document = { querySelector: ()=>({}), createElement: ()=>({ style: {} }) };
global.ResizeObserver = class { observe(){} };

// Mock LightweightCharts
global.window.LightweightCharts = {
  CrosshairMode: { Normal: 1 },
  createChart: () => {
    return {
      addHistogramSeries: () => ({ setData: (d) => { console.log('Histogram setData:', d.length); } }),
      addCandlestickSeries: () => ({ setMarkers: (m) => { console.log('setMarkers:', m.length); }, setData: (d) => { console.log('Candle setData:', d.length); } }),
      addLineSeries: () => ({ setData: (d) => { console.log('Line setData:', d.length); } }),
      applyOptions: () => {}
    };
  }
};

async function test() {
  const r = await fetch("http://localhost:8787/api/state");
  const s = await r.json();
  try {
    console.log("renderTop"); renderTop(s);
    console.log("renderKpis"); renderKpis(s);
    console.log("renderProChart"); renderProChart(s);
    console.log("renderPrice"); renderPrice(s);
    console.log("renderStats"); renderStats(s);
    console.log("renderPosition"); renderPosition(s);
    console.log("renderEquity"); renderEquity(s);
    console.log("renderStrategy"); renderStrategy(s);
    console.log("renderExternal"); renderExternal(s);
    console.log("renderLogs"); renderLogs(s);
    console.log("SUCCESS!");
  } catch (e) {
    console.error("CRASH", e);
  }
}
test();
