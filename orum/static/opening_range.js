"use strict";

const byId = (id) => document.getElementById(id);

function formatAge(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "—";
  if (value < 60) return `${Math.round(value)} s`;
  return `${Math.round(value / 60)} min`;
}

function formatPrice(value) {
  const number = Number(value);
  return Number.isFinite(number)
    ? number.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "—";
}

function phaseForNewYork(now) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  if (["Sat", "Sun"].includes(values.weekday)) {
    return { id: "closed", label: "FERMÉ", detail: "week-end à New York" };
  }
  const minutes = Number(values.hour) * 60 + Number(values.minute);
  if (minutes < 570) return { id: "pre", label: "PRÉ-OUVERTURE", detail: "avant 09:30 ET" };
  if (minutes < 585) return { id: "range", label: "OPENING RANGE", detail: "construction 09:30–09:45 ET" };
  if (minutes < 660) return { id: "entry", label: "FENÊTRE D’ENTRÉE", detail: "09:45–11:00 ET" };
  if (minutes < 960) return { id: "monitor", label: "SUIVI SEULEMENT", detail: "aucune nouvelle entrée après 11:00 ET" };
  return { id: "closed", label: "FERMÉ", detail: "hors séance régulière" };
}

function renderClocks() {
  const now = new Date();
  const timeOptions = { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false };
  const dateOptions = { weekday: "short", day: "2-digit", month: "short", year: "numeric" };
  byId("or-ny-clock").textContent = now.toLocaleTimeString("fr-FR", { ...timeOptions, timeZone: "America/New_York" });
  byId("or-ny-date").textContent = now.toLocaleDateString("fr-FR", { ...dateOptions, timeZone: "America/New_York" });
  byId("or-paris-clock").textContent = now.toLocaleTimeString("fr-FR", { ...timeOptions, timeZone: "Europe/Paris" });
  byId("or-paris-date").textContent = now.toLocaleDateString("fr-FR", { ...dateOptions, timeZone: "Europe/Paris" });
  const phase = phaseForNewYork(now);
  byId("or-phase").textContent = phase.label;
  byId("or-phase-detail").textContent = phase.detail;
  document.querySelectorAll(".or-timeline li").forEach((item) => {
    item.classList.toggle("active", item.dataset.phase === phase.id);
  });
}

function renderRuntime(snapshot) {
  const worker = snapshot.worker || {};
  const legacy = snapshot.legacy_audit || {};
  const healthy = Boolean(worker.running) && !worker.stale;
  const workerLabel = healthy ? "actif" : worker.running ? "silencieux" : "arrêté";
  const workerStatus = byId("or-worker-status");
  workerStatus.textContent = workerLabel;
  workerStatus.className = `or-status ${healthy ? "good" : "bad"}`;
  byId("or-worker-mode").textContent = worker.mode || "—";
  byId("or-worker-heartbeat").textContent = formatAge(worker.heartbeat_age_seconds);
  byId("or-legacy-status").textContent = legacy.process_running ? "encore actif" : "déconnecté";
  byId("or-legacy-status").className = legacy.process_running ? "or-danger" : "or-good";
  const chip = byId("or-engine-chip");
  chip.className = `chip ${healthy ? "ok" : "warn"}`;
  chip.querySelector(".k").textContent = healthy ? "paper unifié actif" : workerLabel;
}

function renderCandles(candles) {
  const body = byId("or-candles-body");
  body.replaceChildren();
  if (!Array.isArray(candles) || candles.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.textContent = "Aucune bougie complète disponible.";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  candles.slice(-8).reverse().forEach((candle) => {
    const row = document.createElement("tr");
    [
      candle.new_york_time || "—",
      formatPrice(candle.open),
      formatPrice(candle.high),
      formatPrice(candle.low),
      formatPrice(candle.close),
      Number(candle.volume || 0).toLocaleString("fr-FR"),
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });
    body.appendChild(row);
  });
}

function renderMarket(market, snapshot) {
  const connected = Boolean(market.connected);
  const fresh = Boolean(market.fresh);
  const status = byId("or-data-status");
  status.textContent = !connected ? "indisponible" : fresh ? "connecté · paper" : "données anciennes";
  status.className = `or-status ${connected && fresh ? "good" : connected ? "warning" : "bad"}`;
  byId("or-data-provider").textContent = market.provider || "—";
  byId("or-latest-close").textContent = market.latest ? `${formatPrice(market.latest.close)} $` : "—";
  byId("or-latest-time").textContent = market.latest
    ? new Date(market.latest.new_york_time).toLocaleString("fr-FR", {
        timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit",
      })
    : "—";
  const counts = market.timeframes || {};
  byId("or-data-counts").textContent = `M5 ${counts["5m"] || 0} · D1 ${counts["1d"] || 0}`;
  byId("or-data-freshness").textContent = market.freshness_seconds == null
    ? market.error || "—"
    : formatAge(market.freshness_seconds);

  const opening = market.opening_range;
  const rangeStatus = byId("or-range-status");
  if (opening) {
    const ratio = Number(opening.atr_ratio) * 100;
    rangeStatus.textContent = `range ${formatPrice(opening.low)}–${formatPrice(opening.high)} · ${ratio.toFixed(1)} % ATR`;
    rangeStatus.className = `or-status ${ratio >= 25 ? "good" : "warning"}`;
  } else {
    rangeStatus.textContent = "range non formée";
    rangeStatus.className = "or-status neutral";
  }
  renderCandles(market.candles);

  const unified = snapshot.unified || snapshot.paper || {};
  const positions = Array.isArray(unified.open_positions) ? unified.open_positions : [];
  const position = positions.find((item) => item.symbol === "NVDA");
  const signalStatus = byId("or-signal-status");
  if (position) {
    signalStatus.textContent = "position paper ouverte";
    signalStatus.className = "or-status good";
    byId("or-signal-title").textContent = `${String(position.side || "position").toUpperCase()} NVDA · paper`;
    byId("or-signal-detail").textContent = `Entrée ${formatPrice(position.entry_px)} $ · stop ${formatPrice(position.stop_loss_price)} $ · objectif ${formatPrice(position.take_profit_price)} $.`;
  } else if (market.signal && ["long", "short"].includes(market.signal.side)) {
    signalStatus.textContent = "signal détecté";
    signalStatus.className = "or-status warning";
    byId("or-signal-title").textContent = `${market.signal.side.toUpperCase()} NVDA détecté`;
    byId("or-signal-detail").textContent = `Stop ${formatPrice(market.signal.stop)} $ · objectif ${formatPrice(market.signal.target)} $ · traitement au prochain cycle paper.`;
  } else {
    signalStatus.textContent = connected ? "aucun signal" : "non calculable";
    signalStatus.className = "or-status neutral";
    byId("or-signal-title").textContent = connected ? "Aucun retournement valide actuellement" : "Flux indisponible";
    byId("or-signal-detail").textContent = connected
      ? "Le moteur observe les bougies M5 clôturées. Il n’ouvre une position que pendant 09:45–11:00 ET."
      : (market.error || "Impossible de lire NVDA.");
  }

  const ready = {
    market: connected && (counts["5m"] || 0) >= 3 && (counts["1d"] || 0) >= 15,
    calendar: false,
    timezone: true,
    detector: connected,
    costs: false,
    paper: Boolean((snapshot.worker || {}).running),
  };
  let readyCount = 0;
  document.querySelectorAll("[data-ready]").forEach((item) => {
    const isReady = Boolean(ready[item.dataset.ready]);
    item.classList.toggle("ready", isReady);
    if (isReady) readyCount += 1;
  });
  const readiness = byId("or-readiness-count");
  readiness.textContent = `${readyCount} / 6`;
  readiness.className = `or-status ${readyCount >= 4 ? "good" : "warning"}`;
}

let pollInFlight = false;

async function pollRuntime() {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    const stateResponse = await fetch("/api/state", { cache: "no-store" });
    const marketResponse = await fetch("/api/opening-range", { cache: "no-store" });
    if (!stateResponse.ok || !marketResponse.ok) {
      throw new Error(`${stateResponse.status}/${marketResponse.status}`);
    }
    const snapshot = await stateResponse.json();
    const market = await marketResponse.json();
    renderRuntime(snapshot);
    renderMarket(market, snapshot);
    byId("or-poll").textContent = "● connecté";
    byId("or-poll").className = "clock conn";
    byId("or-updated").textContent = `Dernière lecture : ${new Date().toLocaleTimeString("fr-FR")}`;
  } catch (error) {
    byId("or-poll").textContent = "● déconnecté";
    byId("or-poll").className = "clock conn down";
  } finally {
    pollInFlight = false;
  }
}

renderClocks();
pollRuntime();
setInterval(renderClocks, 1_000);
setInterval(pollRuntime, 30_000);
