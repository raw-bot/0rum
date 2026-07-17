# MCP_AUDIT — état des lieux avant l'ajout de `orum_mcp`

Audit réalisé le 2026-07-15, avant toute écriture de code. Baseline : `uv run
python -m unittest discover -s tests` → **495 tests, OK** (14 s).

## Où se trouvent les données (sources de vérité)

Tout l'état vit dans `state/`, dont les chemins sont centralisés dans
`orum/paths.py` (redirigeables via `0RUM_STATE_DIR`, mécanisme déjà utilisé par
`tests/conftest.py`).

| Donnée | Source de vérité |
|---|---|
| Portefeuille actif (source officielle depuis 2026-07-08) | `paper_positions.json`, `paper_fills.jsonl`, `paper_equity.jsonl` (moteur `orum/portfolio/paper_engine.py`, config `config/portfolio.yaml` + override `state/portfolio.yaml`) |
| Worker mono-asset (retiré, conservé en audit) | `heartbeat.json`, `open_position.json`, `trades.jsonl` |
| Vivacité du moteur | `state/worker.pid` (+ `os.kill(pid, 0)`), fraîcheur de `paper_equity.jsonl` (>2 h = down), `orum_watcher.json` |
| Signal courant & décision | `heartbeat.json` → champs `dsl.entry_summary/exit_summary/triggered/errors`, `decision_action`, `decision_reason` (déjà structurés, rien à instrumenter) |
| Décisions de réflexion (LLM) | `hypotheses.jsonl` (changed/held + raison + modèle) |
| Incidents / événements | `events.jsonl` (boots, guardrails, quarantines, échecs) |
| Labo LLM | `llm_decisions.jsonl` (validations & **rejets structurés**), `llm_outcomes.jsonl`, comptes `llm_*_account.json` |
| Portefeuilles shadow | `portfolio_shadow.jsonl` + `portfolio_shadow_positions.json` (moteurs recherche Kelly) ; shadow AK MACD : `ak_macd_local_shadow.jsonl` ; `forecast_gate.json` |
| Config runtime | `state/goal.yaml` (cibles, seuils drawdown, politique de réflexion), `state/strategy.yaml` (DSL courant, versionné), `state/history/vNNNN.yaml` (versions antérieures), `config/portfolio.yaml` |
| Backtest existant | `orum/dsl/backtest.py` → `simulate()` (pur) + cache `state/candle_history.json` |
| Secrets | `.env` (clés exchange/news/Glassnode), trousseau via `orum/llm/keychain.py` — **jamais lus par le MCP** |

## Fonctions existantes réutilisables (vérifié : import sans effet de bord, 0,07 s)

- `orum.paths` — tous les chemins d'état, redirigeables pour les tests.
- `orum.accounting` — `account_returns`, `compound_balance`, `max_drawdown` (purs).
- `orum.score.score` — score officiel.
- `orum.dashboard` — couche de lecture déjà écrite et testée :
  `_paper_closed_trades()`, `_paper_state()`, `_paper_worker()`,
  `_portfolio_shadow()`, `_guardrail_status()`, `_engine_status()`,
  `_decisions()`, `_read_jsonl_tail()`, `worker_running()`.
  ⚠️ Ne PAS utiliser `build_snapshot()` : il appelle Binance (`_binance_15m_candles`,
  `_markets`, `_market_signals`). Le MCP n'appelle que les helpers hors réseau.
- `orum.dsl.backtest.simulate()` + `orum.dsl.evaluator` — replay pur d'une
  stratégie DSL sur des bougies fournies. ⚠️ `load_history()` peut fetch
  Binance si le cache est périmé : le MCP lira `candle_history.json`
  directement et refusera (erreur structurée) si le cache est absent/périmé.
- `orum.loop.guardrail_action(drawdown, goal, resume_ack)` — décision guardrail
  officielle, pure.

## Informations déjà disponibles vs manquantes

Disponible et structuré : tout ce qui précède, y compris la raison exacte de la
dernière décision (`decision_reason` du heartbeat) et les rejets LLM
(`llm_decisions.jsonl`). Les raisons de rejet DSL vivent dans `hypotheses.jsonl`.

Manquant (accepté, pas d'instrumentation nécessaire) :
- **Ordres en attente** : 0rum n'a pas de carnet d'ordres pending — les stops/TP
  sont des attributs des positions (`exit_policy`, `stop_loss_price`,
  `take_profit_price`). `get_pending_orders` exposera ces brackets et le dira
  explicitement, sans rien inventer.
- **Historique des décisions "wait" du worker** : seul le dernier heartbeat est
  conservé (écrasé à chaque boucle). Le MCP expose le dernier + les événements ;
  il n'ajoute PAS d'instrumentation au worker (règle : zéro modification du cœur).
- `.err`/`.out` : logs texte, exposés en brut (bornés) et étiquetés comme tels.

## Fichiers ajoutés (aucun fichier existant modifié)

```
orum_mcp/                      # module isolé, jamais importé par orum/
    __init__.py
    redact.py                  # masquage récursif des secrets
    adapters/state_reader.py   # lectures read-only via orum.paths
    adapters/backtest_runner.py# simulate() sur cache uniquement, jamais de réseau
    tools.py                   # définitions + validation des paramètres
    server.py                  # JSON-RPC 2.0 / MCP sur stdio, zéro dépendance
tests/test_mcp_*.py
docs/MCP.md
MCP_AUDIT.md                   # ce fichier
```

Pas de nouvelle dépendance : le protocole MCP stdio (JSON-RPC newline-delimited)
est implémenté en stdlib pure → `pyproject.toml` et `uv.lock` intacts.

## Anti-régression

- `orum/` n'importe jamais `orum_mcp` ; supprimer `orum_mcp/` ne change rien au bot.
- Aucune écriture : les adaptateurs n'ouvrent les fichiers qu'en lecture ; test
  qui hash le state dir avant/après chaque outil.
- Aucun réseau : test qui vérifie qu'aucun module réseau n'est importé par
  `orum_mcp` et qu'aucun outil de trading n'existe.
- Tests MCP isolés par `0RUM_STATE_DIR` (même pattern que `tests/conftest.py`).
- Suite complète relancée après ajout et comparée à la baseline (495 OK).
