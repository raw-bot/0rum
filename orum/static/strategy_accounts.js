"use strict";
const $ = (id) => document.getElementById(id);
const num = (v, d = 2) => Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const usd = (v, d = 2) => "$" + num(v, d);
const pct = (v, d = 2) => `${(Number(v || 0) * 100).toFixed(d)}%`;
const signed = (v, fn) => (Number(v) > 0 ? "+" : "") + fn(v);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const cls = (v) => (Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "");
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const ACCOUNT_LABELS = {
  shared_reference: "Référence · compte partagé", btc_ak_macd_4h: "BTC · AK MACD H4",
  btc_utbot_m15_h1: "BTC · UT Bot M15/H1", btc_ema_cross: "BTC · EMA 9/21",
  eth_ema_cross: "ETH · EMA 9/21", btc_ha_trend_4h: "BTC · HA Trend H4",
  eth_donchian: "ETH · Donchian", gold_cot: "Or · COT", nvda_opening_range: "NVDA · Opening Range",
};
const researchAttr = v => esc(v).replaceAll('"', '&quot;');
function researchSparkline(curve) {
  const values = [10000, ...(curve || []).map(p => p.equity_usd)].filter(Number.isFinite);
  if (values.length < 2) return "—";
  const low = Math.min(...values), high = Math.max(...values), span = high - low || 1;
  const points = values.map((v, i) => `${i * 100 / (values.length - 1)},${high === low ? 15 : 28 - (v - low) * 26 / span}`).join(" ");
  return `<svg width="100" height="30" viewBox="0 0 100 30" role="img" aria-label="Évolution de l'équité, échelle propre à chaque compte"><polyline points="${points}" fill="none" stroke="${values.at(-1) >= 10000 ? '#2ecc71' : '#ff5765'}" stroke-width="1.5"/></svg>`;
}
function renderStrategyAccounts(s) {
  const card = $("strategy-accounts-card");
  if (!card) return;
  const detailsOpen = card.querySelector?.("details")?.open;
  const lab = s.strategy_accounts || {}, accounts = [...(lab.accounts || [])].sort((a,b) => Number(b.reference) - Number(a.reference));
  const status = lab.stale ? "cycle en retard" : lab.status === "ok" ? "actif" : lab.status === "not_started" ? "en préparation" : "données dégradées";
  const percent = v => v == null ? "—" : `${(v * 100).toFixed(2)}%`;
  const rows = accounts.map(a => {
    const reasons = Object.entries(a.intents || {}).map(([id, value]) => `${ACCOUNT_LABELS[id] || id}: ${value}`).join(" · ");
    const errors = [a.fatal_error, ...Object.values(a.errors || {})].filter(Boolean).join(" · ");
    return `<tr${a.reference ? ' style="background:rgba(138,160,184,.12)"' : ''}><td><b>${esc(ACCOUNT_LABELS[a.id] || a.id)}</b><div class="hint">${a.reference ? "10 000 $ partagés entre 8 stratégies" : "10 000 $ indépendants"}</div></td><td title="Solde réalisé : ${a.balance_usd == null ? 'indisponible' : usd(a.balance_usd)}">${a.equity_usd == null ? '—' : usd(a.equity_usd)}</td><td class="${cls(a.pnl_pct)}">${percent(a.pnl_pct)}</td><td>${researchSparkline(a.curve)}</td><td title="DD actuel : ${percent(a.drawdown)}">${percent(a.max_drawdown)}</td><td>${a.open_positions == null ? '—' : a.open_positions.length} / ${a.closed_trades ?? '—'}</td><td>${a.reference ? 'par stratégie' : percent(a.risk_pct)}</td><td title="${researchAttr(errors || reasons)}">${a.status === 'ok' ? '● OK' : '⚠ à vérifier'}</td></tr>`;
  }).join("");
  const details = accounts.map(a => {
    const positions = Object.entries(a.positions || {}).map(([id, p]) => `<div class="hint">${esc(id)} · ${esc(p.symbol)} ${esc(p.side)} · quantité ${num(p.qty, 6)} · entrée ${usd(p.entry_px)} · stop ${p.stop_loss_price == null ? '—' : usd(p.stop_loss_price)} · cible ${p.take_profit_price == null ? '—' : usd(p.take_profit_price)} · mark ${a.marks?.[p.symbol] == null ? 'indisponible' : usd(a.marks[p.symbol])}</div>`).join('');
    return `<div><b>${esc(ACCOUNT_LABELS[a.id] || a.id)}</b> · frais cumulés ${a.fees_usd == null ? '—' : usd(a.fees_usd)}<br>${esc([a.fatal_error, ...Object.values(a.errors || {}), ...Object.entries(a.intents || {}).map(([id,v]) => `${id}: ${v}`)].filter(Boolean).join(' · ') || 'aucun cycle' )}${positions}</div>`;
  }).join('<br>');
  card.innerHTML = `<h2>Comptes de recherche <span class="hint">paper virtuel · ${status} · ${lab.age_seconds == null ? '—' : Math.round(lab.age_seconds / 60) + ' min'}</span></h2><div class="body">
    <div class="scenario-disclaimer">8 stratégies isolées + 1 référence partagée · 10 000 $ fictifs par compte · risque nominal sans réduction au drawdown. Plafonds d'exposition, sorties et frais conservés. Les capitaux ne s'additionnent pas pour mesurer un rendement.</div>
    ${lab.method_changed ? '<div class="worker-alert">Code modifié depuis le départ : comparaison de méthodes à séparer.</div>' : ''}
    ${lab.runner_error ? `<div class="worker-alert">${esc(lab.runner_error)}</div>` : ''}
    <div style="overflow-x:auto"><table class="mini-table"><thead><tr><th>Compte</th><th>Équité $</th><th>Rendement</th><th>Courbe*</th><th>DD max</th><th>Pos. / clos</th><th>Risque nominal</th><th>Cycle</th></tr></thead><tbody>${rows || '<tr><td colspan="8" class="flat">Les comptes apparaîtront après le premier cycle.</td></tr>'}</tbody></table></div>
    <div class="hint">Départ : ${esc(lab.started_at || 'en attente')} · *Échelles propres à chaque courbe. Frais aller-retour : ${percent(lab.fee_roundtrip)}. Positions ouvertes incluses dans l'équité si valorisation complète.</div>
    <details${detailsOpen ? " open" : ""}><summary>Décisions et erreurs du dernier cycle · frais</summary>${details}</details>
  </div>`;
}


async function pollStrategyAccounts() {
  const status = $("accounts-connection");
  try {
    const response = await fetch("/api/strategy-accounts", {cache: "no-store", signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderStrategyAccounts({strategy_accounts: await response.json()});
    status.textContent = "Dernière lecture : " + new Date().toLocaleTimeString("fr-FR");
  } catch (error) {
    status.textContent = "Lecture indisponible · données précédentes conservées · " + error.message;
  } finally {
    setTimeout(pollStrategyAccounts, 5000);
  }
}
pollStrategyAccounts();
