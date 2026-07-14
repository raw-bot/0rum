"use strict";

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value == null ? "" : value).replace(/[&<>]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;",
}[char]));
const list = (value) => Array.isArray(value) ? value : [];
const num = (value, digits = 2) => {
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : "—";
};
const usd = (value) => value == null ? "—" : `${num(value)} $`;
const pct = (value, digits = 2) => value == null ? "—" : `${num(Number(value) * 100, digits)} %`;
const signClass = (value) => Number(value) > 0 ? "pos" : Number(value) < 0 ? "neg" : "";
const laneName = (lane) => lane === "llm_evolving" ? "Évolutif" : "Référence";
const statusClass = (value) => ["error", "warning", "info"].includes(value) ? value : "info";

function localTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? esc(value) : date.toLocaleString("fr-FR");
}

function empty(message) {
  return `<p class="llm-empty">${esc(message)}</p>`;
}

function renderUnified(snapshot) {
  const unified = snapshot.unified || snapshot.paper || {};
  const worker = snapshot.worker || {};
  const guardrail = snapshot.guardrail || {};
  const positions = list(unified.open_positions);
  const workerState = worker.running ? worker.stale ? "silencieux" : "actif" : "arrêté";
  const positionRows = positions.map((position) => `<tr>
    <td>${esc(position.strategy_id || "stratégie inconnue")}</td>
    <td>${esc(position.symbol || "actif inconnu")}</td>
    <td>${esc(position.side || "—")}</td>
    <td>${num(position.qty, 6)}</td>
    <td>${num(position.entry_px)}</td>
    <td>${num(position.stop_loss_price)}</td>
    <td>${num(position.take_profit_price)}</td>
  </tr>`).join("") || `<tr><td colspan="7">Aucune position paper ouverte.</td></tr>`;
  $("bot-unified").innerHTML = `<div class="bot-card-head"><div><p class="eyebrow">PORTEFEUILLE UNIFIÉ</p><h2>Paper opérationnel</h2></div><span class="llm-badge ${worker.running && !worker.stale ? "good" : "bad"}">${esc(workerState)}</span></div>
    <div class="bot-metrics"><div><span>Solde</span><b>${usd(unified.balance_usd)}</b></div><div><span>Equity</span><b>${usd(unified.equity_usd)}</b></div><div><span>P&amp;L</span><b class="${signClass(unified.pnl_usd)}">${usd(unified.pnl_usd)} · ${pct(unified.pnl_pct)}</b></div><div><span>Positions</span><b>${num(unified.open_count, 0)}</b></div><div><span>Garde-fou</span><b>${esc(guardrail.status || "—")}</b></div></div>
    <div class="bot-table-wrap"><table class="mini-table"><thead><tr><th>Stratégie</th><th>Actif</th><th>Sens</th><th>Quantité</th><th>Entrée</th><th>SL</th><th>TP</th></tr></thead><tbody>${positionRows}</tbody></table></div>
    <p class="bot-meta">Dernière écriture du portefeuille : ${esc(localTime(unified.updated_at))} · mode ${esc(worker.mode || "paper")}</p>`;
}

function renderRuntime(snapshot) {
  const lab = snapshot.llm_lab || {};
  const runtime = lab.runtime || {};
  const state = runtime.running ? "cycle en cours" : runtime.enabled ? "agent actif" : "agent désactivé";
  const alerts = list(lab.alerts).map((alert) => `<div class="llm-alert ${statusClass(alert.level)}"><b>${esc(alert.kind || "alerte")}</b><span>${esc(alert.message || "Aucun détail")}</span></div>`).join("") || empty("Aucune alerte LLM.");
  const error = runtime.last_error ? `<div class="llm-alert error"><b>runtime</b><span>${esc(runtime.last_error)}</span></div>` : "";
  $("bot-runtime").innerHTML = `<div class="bot-card-head"><div><p class="eyebrow">LLM PAPER</p><h2>Runtime et contrôles</h2></div><span class="llm-badge ${runtime.running ? "info" : runtime.enabled ? "good" : "neutral"}">${esc(state)}</span></div>
    <div class="llm-metrics"><span>modèle ${esc(runtime.model || "modèle inconnu")}</span><span>cadence ${num(runtime.interval_minutes, 0)} min</span><span>résultat ${esc(runtime.last_result || "non démarré")}</span><span>dernier cycle ${esc(localTime(runtime.last_cycle_completed_at))}</span></div>
    ${error}<div class="llm-alerts">${alerts}</div>`;
}

function laneCard(lane, account) {
  const positions = list(account.positions).map((position) => `<div class="llm-position"><b>${esc(position.symbol || "actif inconnu")}</b> · ${esc(position.side || "—")}
    <div class="llm-metrics"><span>entrée ${num(position.entry_px)}</span><span>mark ${num(position.mark_px)}</span><span>levier ${num(position.effective_leverage, 1)}×</span><span>SL ${num(position.stop_loss)}</span></div>
    <p class="llm-copy">${esc(position.thesis || "Thèse non renseignée")}</p><p class="llm-id">décision ${esc(position.decision_id || "—")} · position ${esc(position.position_id || "—")}</p></div>`).join("") || empty("Aucune position ouverte.");
  return `<section class="llm-panel"><h3>${laneName(lane)} <span class="llm-badge neutral">${esc(account.status || "absent")}</span></h3>
    <div class="llm-metrics"><span>solde ${usd(account.balance_usd)}</span><span>equity ${usd(account.equity_usd)}</span><span>${num(account.processed_decision_count, 0)} décisions traitées</span></div>${positions}</section>`;
}

function renderLanes(snapshot) {
  const accounts = (snapshot.llm_lab || {}).accounts || {};
  $("bot-lanes").innerHTML = `<div class="bot-card-head"><div><p class="eyebrow">COMPTES LLM</p><h2>Référence et évolutif</h2></div><span class="bot-note">Deux comptes paper séparés</span></div>
    <div class="llm-lab-grid">${laneCard("llm_reference", accounts.llm_reference || {})}${laneCard("llm_evolving", accounts.llm_evolving || {})}</div>`;
}

function renderDecisions(snapshot) {
  const timeline = list((snapshot.llm_lab || {}).timeline).slice(0, 12);
  const rows = timeline.map((item) => {
    const message = item.memo_fr || item.error || list(item.reasons).join(", ") || "Événement mécanique audité";
    const state = item.status === "rejected" || item.status === "error" ? "bad" : item.status === "accepted" || item.status === "executed" ? "good" : "neutral";
    return `<article class="llm-decision"><div class="llm-decision-head"><span class="llm-badge ${state}">${esc(item.status || item.kind || "événement")}</span><b>${esc(item.action || item.kind || "événement")}</b><span>${esc(laneName(item.lane))}</span><time>${esc(localTime(item.recorded_at))}</time></div>
      <p class="llm-copy">${esc(message)}</p><p class="llm-subcopy"><b>Thèse</b> ${esc(item.thesis || "—")} · <b>Invalidation</b> ${esc(item.invalidation || "—")}</p>
      <div class="llm-metrics"><span>levier demandé ${num(item.requested_leverage, 1)}×</span><span>paper ${num(item.paper_effective_leverage, 1)}×</span><span>repère FR ${num(item.fr_retail_eligible_leverage, 1)}×</span><span>confiance ${pct(item.confidence, 0)}</span></div>
      <p class="llm-id">décision ${esc(item.decision_id || "—")} · fill ${esc(item.fill_id || "—")} · snapshot ${esc(item.snapshot_id || "—")}</p></article>`;
  }).join("") || empty("Aucune décision ni aucun fill LLM journalisé.");
  $("bot-decisions").innerHTML = `<div class="bot-card-head"><div><p class="eyebrow">AUDIT LLM</p><h2>Décisions et fills récents</h2></div><span class="bot-note">Les journaux source restent inchangés</span></div><div class="bot-feed">${rows}</div>`;
}

function renderLearning(snapshot) {
  const lab = snapshot.llm_lab || {};
  const opinion = lab.opinion || {};
  const outcomes = list(lab.outcomes).slice(0, 6).map((item) => `<div class="llm-row"><span>${esc(laneName(item.lane))} · ${esc(item.side || "—")}</span><b class="${signClass(item.net_return_on_margin)}">${pct(item.net_return_on_margin)}</b><span>${esc(item.exit_reason || "—")}</span><small>${esc(item.decision_id || "—")} → ${esc(item.outcome_id || "—")}</small></div>`).join("") || empty("Aucun outcome fermé.");
  const postmortems = list(lab.postmortems).slice(0, 3).map((item) => `<div class="llm-postmortem"><b>${esc(item.process_quality || "qualité non renseignée")} · ${esc(item.primary_error || "sans erreur classée")}</b><p>${esc(item.memo_fr || "Aucun mémo")}</p><small>${esc(item.postmortem_id || "—")} · outcome ${esc(item.outcome_id || "—")}</small></div>`).join("") || empty("Aucun post-mortem validé.");
  const lessons = list(lab.lessons).slice(0, 6).map((item) => `<div class="llm-lesson"><span class="llm-badge ${item.state === "active" ? "good" : "neutral"}">${esc(item.state || "—")}</span><b>${esc(item.error_category || "sans catégorie")}</b><span>${esc(item.adjustment || "Ajustement non renseigné")}</span><small>${esc(item.lesson_id || "—")} · force ${num(item.evidence_strength, 2)}</small></div>`).join("") || empty("Aucune leçon candidate ou active.");
  $("bot-learning").innerHTML = `<div class="bot-card-head"><div><p class="eyebrow">LECTURE ET APPRENTISSAGE</p><h2>Avis du marché, outcomes et leçons</h2></div><span class="llm-badge info">${esc(opinion.bias || "sans biais")}</span></div>
    <div class="llm-lab-grid"><section class="llm-panel llm-opinion"><h3>Avis analyste</h3><div class="llm-metrics"><span>${esc(opinion.regime || "régime inconnu")}</span><span>confiance ${pct(opinion.confidence, 0)}</span><span>${esc(opinion.model || "modèle inconnu")}</span><span>${esc(localTime(opinion.recorded_at))}</span></div><p class="llm-copy">${esc(opinion.memo_fr || "Aucun mémo disponible.")}</p><p class="llm-subcopy"><b>Lecture</b> ${esc(opinion.interpretation || "—")} · <b>Invalidation</b> ${esc(opinion.invalidation || "—")}</p><p class="llm-id">brief ${esc(opinion.brief_id || "—")} · snapshot ${esc(opinion.snapshot_id || "—")}</p></section>
      <section class="llm-panel"><h3>Outcomes</h3>${outcomes}</section><section class="llm-panel"><h3>Post-mortems</h3>${postmortems}</section><section class="llm-panel"><h3>Leçons falsifiables</h3>${lessons}</section></div>`;
}

function render(snapshot) {
  renderUnified(snapshot);
  renderRuntime(snapshot);
  renderLanes(snapshot);
  renderDecisions(snapshot);
  renderLearning(snapshot);
  $("bot-updated").textContent = `Dernière lecture : ${new Date().toLocaleTimeString("fr-FR")}`;
}

let pollInFlight = false;

async function tick() {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    render(await response.json());
    $("bot-poll").textContent = "● connecté";
    $("bot-poll").className = "clock conn";
  } catch (error) {
    $("bot-poll").textContent = "● déconnecté";
    $("bot-poll").className = "clock conn down";
  } finally {
    pollInFlight = false;
  }
}

tick();
setInterval(tick, 3_000);
