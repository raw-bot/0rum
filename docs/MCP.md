# orum-mcp — serveur MCP read-only pour 0rum

Serveur MCP (Model Context Protocol) local, en **lecture seule**, qui expose
l'état réel du bot 0rum à Claude Code et Codex : worker, compte paper,
positions, risque, signaux, décisions, trades, portefeuilles shadow, erreurs,
config et backtests hors-ligne.

Module **additionnel et isolé** : rien sous `orum/` n'importe `orum_mcp/`.
Arrêter ou supprimer le MCP ne change strictement rien au fonctionnement du bot
(garanti par `tests/test_mcp_isolation.py`).

## Installation

Aucune : zéro nouvelle dépendance (`pyproject.toml` et `uv.lock` intacts). Le
protocole MCP stdio est implémenté en stdlib pure. Le module vit dans le dépôt :

```
.sandbox/0rum-one-shot-home/0rum-trading/orum_mcp/
```

## Lancement

Depuis la racine du dépôt bot :

```bash
uv run python -m orum_mcp.server
```

Le serveur parle JSON-RPC 2.0 (délimité par lignes) sur stdin/stdout — c'est un
processus stdio lancé par le client MCP, il n'écoute sur **aucun port**. Les
diagnostics vont sur stderr.

## Configuration Claude Code

```bash
claude mcp add orum-mcp -- uv run --directory \
  "/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading" \
  python -m orum_mcp.server
```

ou dans `.mcp.json` :

```json
{
  "mcpServers": {
    "orum-mcp": {
      "command": "uv",
      "args": [
        "run", "--directory",
        "/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading",
        "python", "-m", "orum_mcp.server"
      ]
    }
  }
}
```

## Configuration Codex

Dans `~/.codex/config.toml` :

```toml
[mcp_servers.orum-mcp]
command = "uv"
args = [
  "run", "--directory",
  "/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading",
  "python", "-m", "orum_mcp.server",
]
```

Un seul et même serveur sert les deux clients.

## Outils disponibles (16, tous read-only)

| Outil | Rôle | Source de vérité |
|---|---|---|
| `get_worker_status` | vivacité moteur/worker/watcher | `worker.pid`, `paper_equity.jsonl`, `heartbeat.json`, `orum_watcher.json` |
| `get_account_state` | compte paper unifié + comptes labo LLM | `paper_positions.json`, `paper_equity.jsonl`, `llm_*_account.json` |
| `get_open_positions` | positions ouvertes (paper, legacy, shadow) | `paper_positions.json`, `open_position.json` |
| `get_pending_orders` | brackets stop/TP des positions (0rum n'a pas de carnet d'ordres) | `paper_positions.json` |
| `get_risk_state` | drawdown, guardrail, seuils | `paper_fills.jsonl`, `goal.yaml`, `orum.loop.guardrail_action` |
| `get_current_signal_state` | dernière évaluation DSL + décision et raison | `heartbeat.json` |
| `get_last_decisions` | worker + réflexions + labo LLM | `heartbeat.json`, `hypotheses.jsonl`, `llm_decisions.jsonl` |
| `explain_rejected_signal` | rejets/quarantaines verbatim (filtre `query`) | `llm_decisions.jsonl`, `hypotheses.jsonl`, `events.jsonl` |
| `explain_trade` | un trade clos + fills bruts + événements pendant le trade | `paper_fills.jsonl`, `events.jsonl` |
| `get_recent_trades` | trades clos (`source=paper\|legacy`) | `paper_fills.jsonl` / `trades.jsonl` |
| `get_strategy_metrics` | win rate, drawdown, score officiel, détail par stratégie | `orum.accounting`, `orum.score` |
| `get_shadow_portfolios` | moteurs shadow Kelly, shadow AK MACD, forecast gate | `portfolio_shadow.jsonl`, `ak_macd_local_shadow.jsonl`, `forecast_gate.json` |
| `get_recent_errors` | incidents + erreurs DSL (+ tails `.err` bornés en option) | `events.jsonl`, `heartbeat.json`, `*.err` |
| `get_runtime_config` | config réellement chargée (goal, stratégie, portfolio, env `ORUM_*`) | `goal.yaml`, `strategy.yaml`, `config/portfolio.yaml` |
| `run_existing_backtest` | replay hors-ligne d'une version (`current` ou `vNNNN`) via le `simulate()` existant | `candle_history.json`, `strategy.yaml`, `state/history/` |
| `compare_backtest_runs` | diff de deux versions sur les mêmes bougies en cache | idem |

## Exemples d'utilisation

Dans Claude Code ou Codex, une fois le serveur configuré :

- « Pourquoi le bot n'a pas pris de trade sur la dernière bougie ? » →
  `get_current_signal_state` renvoie la raison exacte du worker, p. ex.
  `"No long entry: rsi(14) <= 25 (lhs=38.15) -> not met AND regime != unfavorable -> met."`
- « Explique le dernier trade ETH » → `explain_trade {"strategy_id": "eth_donchian"}`
- « Pourquoi la décision decision-777 a-t-elle été rejetée ? » →
  `explain_rejected_signal {"query": "decision-777"}` (enregistrements verbatim ;
  s'il n'y a pas de rejet journalisé, le serveur répond `found: false` — il
  n'invente jamais de raison)
- « La stratégie actuelle est-elle meilleure que la v0003 ? » →
  `compare_backtest_runs {"version_a": "v0003", "version_b": "current"}`

## Sécurité

- **Lecture seule** : aucun outil n'écrit, ne trade, ne modifie stratégie/risque/
  config. Les noms `place_order`, `close_position`, `change_risk`,
  `enable_live_mode`, etc. sont interdits et leur absence est testée.
- **stdio uniquement, local uniquement** : pas de socket, pas de port.
- **Aucun accès réseau** : jamais d'appel Binance ; le backtest MCP ne lit que le
  cache local `candle_history.json` (testé en simulant l'absence de réseau).
- **Aucun secret** : `.env` n'est jamais lu ; toute clé au nom sensible
  (`*api_key*`, `*secret*`, `*token*`, `*password*`, …) est masquée
  récursivement dans chaque réponse (`orum_mcp/redact.py`).
- **Paramètres validés** (jsonschema strict, `additionalProperties: false`,
  bornes sur `limit`/`days`, versions `^(current|v\d{4})$` — pas de traversée de
  chemin), **réponses bornées** (200 KB), **timeout** de 20 s par outil.
- Pas de shell libre, pas de SQL libre : chaque outil lit une liste fixe de
  fichiers d'état.

## Limites

- `get_pending_orders` renvoie une liste vide avec explication : 0rum n'a pas de
  carnet d'ordres en attente (les stops/TP sont des attributs de position).
- `get_current_signal_state` reflète la *dernière* itération du worker
  (le heartbeat est écrasé à chaque boucle ; pas d'historique des « wait »).
  Si le worker est arrêté, la réponse porte `stale: true` et un
  `stale_warning` explicite — ne pas interpréter ce snapshot comme le présent.
- `get_shadow_portfolios` résume `forecast_gate.json` (scalaires + longueurs de
  listes) : le fichier brut peut dépasser la borne de 200 Ko.
- `run_existing_backtest` exige un cache de bougies présent et couvrant l'actif
  de `goal.yaml` ; sinon il renvoie une erreur structurée (jamais de fetch).
- Les tails de `.err` sont du texte brut, étiquetés comme tels.

## Désactivation

- Claude Code : `claude mcp remove orum-mcp` (ou retirer l'entrée de `.mcp.json`).
- Codex : supprimer le bloc `[mcp_servers.orum-mcp]` de `~/.codex/config.toml`.
- Suppression totale : `rm -rf orum_mcp tests/test_mcp_*.py docs/MCP.md MCP_AUDIT.md`
  — le bot et sa suite de tests d'origine continuent de fonctionner à l'identique.

## Tests

```bash
uv run python -m unittest discover -s tests          # suite complète (530 tests)
uv run python -m unittest tests.test_mcp_tools tests.test_mcp_security \
  tests.test_mcp_server tests.test_mcp_isolation -v  # tests MCP seuls
```
