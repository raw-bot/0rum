# HermesTrading × TradingView — Plan d'intégration

> Statut : **TOUTES les phases (0–7) terminées.** Suite à **263 tests verts**. Pine Hermes Mirror live
> (`pine/hermes_mirror.pine`). Boucle complète TradingView→Hermes démontrée EN LIVE (MCP table → poller →
> orchestrator → paper close). Dashboard refondu (console ops sombre) avec panneau external + event log.
> Backlog hors-scope priorisé : **1) binding polling always-on**, **2) live exchange**.
> Décision d'archi maîtresse : **Hermes reste maître** des positions, du risque, des garde-fous,
> de l'idempotence, de l'exécution, des logs, de l'accounting et de la **décision finale**.
> TradingView ne place **jamais** d'ordre sans validation Hermes.
> Référence : `PROCESS_FreePlanUnlocked.md`, `indicators.py`, `schema.py`, `market_regime.py`, `loop.py`.

Canonical tree : `.sandbox/hermes-one-shot-home/hermes-trading/`.

---

## A. Diagnostic du repo (fichiers lus)

`loop.py` (569), `dsl/{schema,indicators,evaluator,backtest,migrate,diff}.py`, `accounting.py`,
`market_regime.py`, `paths.py`, `run.py`, `dashboard.py`, `tests/test_guardrails.py`.

### Où sont les briques
| Brique | Emplacement | Notes |
|---|---|---|
| Stratégie | `state/strategy.yaml` (DSL mutable, `version` défaut `"01"`, `DSL_VERSION=1`) | mutée par `reflect.py` (LLM) |
| Indicateurs | `dsl/indicators.py` | whitelist `rsi,sma,ema,close,bollinger,atr,regime` |
| Schéma DSL | `dsl/schema.py` | jsonschema + validation sémantique, `MAX_CONDITIONS=4`, AND/OR plat |
| Régime | `market_regime.py` | rolling-return 20 barres, seuil ±0.3 %, labels favorable/neutral/unfavorable |
| Risk gates | `loop.py:guardrail_action` | normal → halt_entries → emergency |
| Kill-switch | fichier `state/manual_resume.ok` (+ `RESUME_ACK_PATH`) | reprise manuelle après emergency |
| Drawdown | `accounting.py:max_drawdown` | high-water au niveau compte |
| Sizing / risk fields | `loop.py:_sizing` + `dsl/migrate.py:risk_value` | `position_size_r`, `stop_loss_pct`, `take_profit_pct`, `max_hold_candles`, `fee_rate` |
| "Executor" | **inline dans `loop.py`** (`open_position_from_signal`, `close_position_if_needed`, `_build_closed_trade`) | **paper-only**, simulé |
| Dédup / idempotence | `signal_id = asset|sha256(strategy)|candle_ts`, `should_record_signal`, `is_duplicate_close` | |
| Position stale | `position_is_stale` + `_quarantine_position` | `HERMES_MAX_POSITION_AGE_HOURS` |
| Boucle paper/live | `loop.py:run_loop`, mode via `HERMES_TRADING_MODE` (défaut `paper`) | |
| Accounting | `accounting.py` (`compound_balance`, `max_drawdown`) | |
| Dashboard | `dashboard.py` + `static/` | |
| État (DB) | **JSONL files** dans `state/` (`trades.jsonl`, `events.jsonl`, `open_position.json`, …) | **pas d'ORM/DB** |
| Config | `state/goal.yaml` (asset, seuils drawdown, balance), `state/strategy.yaml` | |
| Adapters | `adapters/{price,news,macro,onchain}.py` | `price.py` a un fallback offline |

### Ce qui existe déjà (à RÉUTILISER, ne pas redévelopper)
Dédup, idempotence, guardrails, drawdown, kill-switch, quarantaine stale, gel offline, accounting paper, logging d'événements (`events.jsonl`).

### Ce qui manque (à construire)
- Un type/parseur `ExternalSignal` + file d'ingestion `state/external_signals.jsonl`.
- Une fonction de **validation** qui réutilise les gates existants.
- Un switch de mode `signal_source: native | tradingview_external`.
- Une **couture `Executor`** (extraction du paper inline) — pré-requis propre du mode external.
- Une allowlist `allowed_external_strategies` (identifiants externes uniquement).
- Indicateur Pine **Hermes Mirror**.

### Incohérences relevées dans les hypothèses initiales (corrigées)
1. **Pas de DB/ORM/TradeORM** → on reste JSONL + index dédup en mémoire.
2. **Pas d'executor live / d'exchange** → bot paper-only ; on construit la *couture*, pas le live.
3. **Pas de stratégies nommées dans le moteur** → DSL unique mutable ; les noms n'existent que côté external (allowlist).
4. **`bar_time`** : le bot stocke des ts en **millisecondes**, `_held_candles` divise par 60000 (bougies 1 min supposées). Le JSON TV doit canoniser en ms + déclarer `timeframe`.

---

## B. Architecture proposée — deux modes mutuellement exclusifs

Switch unique dans `state/goal.yaml` :
```yaml
signal_source: native            # ou: tradingview_external
```

### Mode `native` (défaut, inchangé)
- Hermes calcule indicateurs + signaux en Python (DSL evaluator).
- TradingView = **miroir visuel/debug** uniquement (Hermes Mirror).
- Le Pine n'influence **aucune** décision. On compare visuellement Pine ↔ Python.
- **Reproductibilité backtest/live intacte.**

### Mode `tradingview_external`
- L'évaluateur DSL natif d'entrée est **désactivé** (pas de mélange des sources).
- Les signaux arrivent par **polling MCP** (gratuit) — webhook = option future (Pro+).
- Chaque signal devient un `ExternalSignal`, **jamais exécuté automatiquement**.
- Hermes valide (section E) → **accepte** (→ signal interne → `Executor`) ou **refuse** (→ log raison).
- Les gates de risque, drawdown, kill-switch, idempotence restent **les mêmes** que native.

### Flux de données
```
NATIVE :
  price adapter → indicators.py → DSL evaluator → signal interne
      → guardrail_action → Executor.open/close → trades.jsonl
  (Pine Hermes Mirror lit le chart en parallèle, lecture seule, aucun effet)

EXTERNAL :
  Pine Hermes Mirror (alert JSON) → [polling MCP data_get_pine_labels/tables]
      → ingest → ExternalSignal (state/external_signals.jsonl, status=received)
      → validation (E) → accepted | rejected(reason)
      → si accepted : map → signal interne → guardrail_action → Executor.open/close
      → trades.jsonl (+ lien external_signal_id)
```

---

## C. Modèle de données (file-based, PAS d'ORM)

Nouveau fichier append-only : `state/external_signals.jsonl`. Un index de dédup en mémoire
(`set` de clés) chargé au démarrage depuis ce fichier — **pas de DB**.

### `ExternalSignal` (dataclass, sérialisée JSONL)
```python
@dataclass(frozen=True)
class ExternalSignal:
    source: str            # "tradingview"
    strategy: str          # identifiant external (allowlist)
    symbol: str            # "BTCUSDT"
    timeframe: str         # "15m"
    event: str             # "BUY_CANDIDATE" | "SELL_CANDIDATE" | "EXIT"
    bar_time: int          # MILLISECONDES (canonisé)
    price: float           # bar_close
    version: str | None    # ex "tv_ema_momentum_v3"
    received_at: str       # iso utc
    raw: dict              # payload brut conservé pour audit
```

### `ExternalSignalStatus` (enum)
`RECEIVED → VALIDATED → ACCEPTED → EXECUTED` | `REJECTED` | `DUPLICATE`.

### `ExternalSignalSource` (enum)
`TRADINGVIEW` (extensible). Toute source hors enum/allowlist = rejet.

### Clé de dédup (composite, distincte de `signal_id` natif)
```
external_key = source | strategy | symbol | timeframe | event | bar_time
```
Stockée hashée. Réutilise le **pattern** `should_record_signal`/`is_duplicate_close`, pas leur clé.

### Journalisation
Chaque transition (accept/reject/duplicate/execute) → ligne dans `external_signals.jsonl`
**et** event dans `events.jsonl` (`external_signal_received/accepted/rejected/executed`, avec `reason`).
Lien vers le trade : le trade exécuté porte `external_signal_id = hash(external_key)`.

---

## D. Format JSON TradingView (strict)

Émis par `alert()` Pine, `alert.freq_once_per_bar_close`. **`bar_time` en millisecondes.**
```json
{
  "source": "tradingview",
  "strategy": "ema_momentum_v3",
  "symbol": "BTCUSDT",
  "timeframe": "15m",
  "event": "BUY_CANDIDATE",
  "bar_time": 1781424000000,
  "price": 68200.5,
  "version": "tv_ema_momentum_v3"
}
```
Règles : champs obligatoires = `source, strategy, symbol, timeframe, event, bar_time, price`
(`version` recommandé). `event ∈ {BUY_CANDIDATE, SELL_CANDIDATE, EXIT}`.
Tout champ manquant/typé faux = rejet `malformed`. JSON compact (parsing trivial côté bot).

---

## E. Validation Hermes (ordre des checks, court-circuit au 1er échec → log raison)

1. **JSON valide** (sinon `malformed`).
2. **source autorisée** (`∈ ExternalSignalSource`).
3. **strategy connue** (`∈ goal.allowed_external_strategies`).
4. **symbol/timeframe autorisés** pour cette stratégie (allowlist).
5. **bougie clôturée** (`bar_time` aligné sur la grille du `timeframe`, pas intrabar).
6. **bar_time non déjà traité** (`external_key` absente de l'index → sinon `duplicate`).
7. **pas de position contradictoire** (`_load_open_position` : pas de BUY si déjà long, etc.).
8. **kill-switch inactif** (pas en `emergency` sans `manual_resume.ok`).
9. **risk gates OK** (`guardrail_action != halt_entries/emergency` pour une entrée).
10. **drawdown OK** (`max_drawdown` sous seuil `goal.max_drawdown`).
11. **mode paper/live cohérent** (`HERMES_TRADING_MODE`).
12. **prix non-offline** (adapter pas en fallback ; sinon entrées gelées).
13. *(live futur)* exchange/API accessible, spread/slippage acceptables — **N/A en paper**.
14. **log complet** de l'issue (accepted/rejected + raison) dans `external_signals.jsonl` + `events.jsonl`.

Accepté → mappé en signal interne (`open_position_from_signal`/exit) → `Executor`.
Refusé → aucune action, raison persistée.

---

## F. Pine Script « Hermes Mirror » (un seul indicateur visible)

Périmètre — **strictement la whitelist DSL**, rien de générique :
- Dashboard `table` (lisible MCP `data_get_pine_tables`) : RSI, EMA(s), SMA(s), ATR, Bollinger (upper/middle/lower/pct_b), **régime**, close.
- `plot` des valeurs (lisible `data_get_study_values`).
- `label` de signaux candidats (BUY/SELL/EXIT) — **à la clôture seulement**.
- **Smart Alert Engine** : une `alert()` JSON par canal, `freq_once_per_bar_close`.
- **Interdits** : aucun ordre, aucune stratégie cachée, aucun repaint, aucun signal intrabar.

### Pièges de parité (sinon le miroir ment vs Python)
| Élément | Risque | Correctif Pine |
|---|---|---|
| **EMA seed** | `indicators.py` seed EMA sur SMA des `period` 1res barres ; `ta.ema` non | réimplémenter le seed SMA (ou accepter écart de warm-up documenté) |
| **Bollinger** | variance **population** côté Python | `ta.stdev(src,len)` (biased=true par défaut) ✓ — ne pas mettre `biased=false` |
| **RSI** | Wilder | `ta.rsi` ✓ |
| **ATR** | RMA Wilder | `ta.atr` ✓ |
| **Régime** | aucun builtin Pine | coder `ret=(close-close[19])/close[19]`, seuils ±0.3 %, lookback 20 |
| **Warm-up** | NaN durant warm-up | masquer/`na` les valeurs avant `first_valid_index` |
| **Timezone / bar_time** | TV vs Hermes | canoniser `time` en ms UTC dans le payload |
| **Bougies clôturées** | repaint | `barstate.isconfirmed` / `freq_once_per_bar_close` |

---

## G. Plan de tests

Unitaires :
- parsing JSON externe (valide / champs manquants / types faux → `malformed`).
- dédup : même `external_key` 2× → 2e = `duplicate`.
- refus risk gate (`halt_entries`/`emergency`).
- refus kill-switch (emergency sans `manual_resume.ok`).
- refus stratégie/symbol/timeframe hors allowlist.
- refus bougie non clôturée / `bar_time` désaligné.
- refus position contradictoire.
- acceptation paper → trade écrit avec `external_signal_id`.
- log complet (raison présente sur chaque issue).

Intégration :
- **non-régression mode native** (suite existante verte, comportement inchangé).
- simulation d'alertes TradingView (payloads injectés sans MCP).
- mismatch symbole/timeframe.
- double alerte même bar (idempotence bout-en-bout).
- `Executor` : `PaperExecutor` produit exactement les mêmes trades qu'aujourd'hui (refactor sans changement de comportement).

---

## H. Plan d'implémentation par phases

| Phase | Contenu | Touche au natif ? | Gate de sortie |
|---|---|---|---|
| **0** | Audit (ce doc) | non | ✅ fait |
| **1** | Types/schemas `ExternalSignal`, enums, parseur strict + tests | non | parsing/validation unitaires verts |
| **2** | Ingestion locale **simulée** (payloads → `external_signals.jsonl`, index dédup) | non | dédup + log testés |
| **3** | Validation + mapping sur gates existants (`guardrail_action`, drawdown, kill-switch, position) | lecture seule | refus/acceptation testés |
| **3.5** | **Refactor couture `Executor`/`PaperExecutor`** (extraction paper inline) | refactor neutre | non-régression native verte |
| **4** | Routage paper : signal accepté → `Executor.open/close` → `trades.jsonl` | via switch `signal_source` | trade paper depuis external testé |
| **5** | Transport MCP : polling `data_get_pine_labels/tables`, dédup par `bar_time` | non | e2e simulé MCP |
| **6** | Pine **Hermes Mirror** (build + `pine_smart_compile` en live, parité) | non | compile 0 err, valeurs ≈ Python |
| **7** | Dashboard/logs : visibilité external (reçus/acceptés/refusés + raisons) | additif | UI lit `external_signals.jsonl` |

### Backlog hors-scope priorisé

- [ ] **P1 — Binding polling always-on** : brancher le reader du poller pour fonctionner sans agent
  interactif, via lecture CDP directe ou petit pont dédié. Décision requise avant tout
  paper-trading continu.
- [ ] **P2 — Live exchange (`CcxtExecutor`)** : différé jusqu'à preuve de rentabilité en paper.
  Aucun ordre réel tant que la stratégie n'a pas produit un historique paper convaincant
  (rappel : net −$12.62 / 8 trades à ce jour).

### Décisions actées
1. Stockage external = **JSONL + index mémoire** (pas de DB).
2. `bar_time` canonisé en **millisecondes** + `timeframe` explicite.
3. Identité stratégie = **allowlist `goal.allowed_external_strategies`** (external only, moteur natif inchangé).
4. Transport = **polling MCP** (gratuit) ; webhook = option future Pro+.
5. Live = **différé** jusqu'à preuve de rentabilité paper.
