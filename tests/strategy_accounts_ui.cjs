// Render the research card without browser/network dependencies.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('orum/static/strategy_accounts.js', 'utf8');
const card = { innerHTML: '' };
const context = vm.createContext({ document: {getElementById: () => card} });
vm.runInContext(source.slice(0, source.indexOf('async function pollStrategyAccounts')), context);
const render = data => { context.renderStrategyAccounts({strategy_accounts: data}); return card.innerHTML; };
assert.match(render({status:'not_started', accounts:[]}), /premier cycle/);
const accounts = Array.from({length:9}, (_,i) => ({id:i===0?'shared_reference':`strategy_${i}`, reference:i===0,
  status:'ok', equity_usd:10000, balance_usd:10000, pnl_pct:0, max_drawdown:0, drawdown:0,
  risk_pct:.01, open_positions:[], closed_trades:0, fees_usd:0, curve:[{equity_usd:10000}]}));
let html = render({status:'ok', accounts, fee_roundtrip:.001});
assert.equal((html.match(/<svg /g)||[]).length,9);
assert.match(html, /Référence · compte partagé/);
accounts[1] = {...accounts[1], equity_usd:null, pnl_pct:null, status:'error',
  errors:{source:'bad " onmouseover="attack <tag>'}, positions:{test:{symbol:'BTC/USDT',side:'long',qty:.1,entry_px:100,stop_loss_price:90}}};
html = render({status:'degraded', stale:true, method_changed:true, accounts});
assert.match(html, /cycle en retard/);
assert.match(html, /Code modifié depuis le départ/);
assert.match(html, /stop \$90/);
assert.match(html, /&quot; onmouseover=&quot;/);
assert.ok(!html.includes('<tag>'));
console.log('Research card: empty, nine accounts, curves, missing valuations, errors, stale, method change, position details and escaping PASS');
