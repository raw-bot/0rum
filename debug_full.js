const fs = require('fs');

// Stub out DOM functions
const elements = {};
global.$ = (id) => {
  if (!elements[id]) {
    elements[id] = { innerHTML: '', style: {}, classList: { add: ()=>{}, remove: ()=>{} }, querySelector: ()=>({ setAttribute: ()=>{}, style: {}, addEventListener: ()=>{} }) };
  }
  return elements[id];
};
global.window = {};
global.document = { querySelector: ()=>({}), createElement: ()=>({ style: {} }) };
global.ResizeObserver = class { observe(){} };
global.num = (v, d = 2) => Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
global.usd = (v, d = 2) => "$" + global.num(v, d);
global.pct = (v, d = 2) => `${(Number(v || 0) * 100).toFixed(d)}%`;
global.signed = (v, fn) => (Number(v) > 0 ? "+" : "") + fn(v);
global.esc = (s) => String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
global.cls = (v) => (Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "");
global.clamp = (v, a, b) => Math.max(a, Math.min(b, v));

global.emaSeries = function(vals, period) {
  const out = new Array(vals.length).fill(null);
  if (vals.length < period) return out;
  const k = 2 / (period + 1);
  let prev = vals.slice(0, period).reduce((a, b) => a + b, 0) / period;
  out[period - 1] = prev;
  for (let i = period; i < vals.length; i++) { prev = (vals[i] - prev) * k + prev; out[i] = prev; }
  return out;
};
global.rsiSeries = function(vals, period = 14) { return new Array(vals.length).fill(null); }; // stub

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

const dashboardJs = fs.readFileSync('/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/orum/static/dashboard.js', 'utf8');

// evaluate everything up to tick
const code = dashboardJs.split('async function tick()')[0];
eval(code);

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
