const s = {
  candles: [
    {
      "close": 62924.0,
      "high": 63048.7935,
      "low": 62607.2165,
      "open": 62732.01,
      "synthetic_range": true,
      "ts": "2026-06-11T13:44:31.475496+00:00"
    },
    {
      "close": 62768.89,
      "high": 62847.4685,
      "low": 62569.4215,
      "open": 62648.0,
      "synthetic_range": true,
      "ts": "2026-06-11T16:36:38.366231+00:00"
    }
  ],
  last_price: 64496.0
};

let rawSeries = [];
if (s.candles && s.candles.length >= 2) {
  rawSeries = s.candles.map(c => ({
    ts: new Date(c.ts).getTime(),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: 0
  })).filter(p => p.close > 0 && !isNaN(p.ts));
}

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

console.log(cData);
