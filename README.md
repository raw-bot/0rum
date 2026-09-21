# 0rum-trading

Bot paper multi-stratégies : données publiques de marché, exécutions simulées,
comptes et journaux persistants. `com.0rum.paper` exécute le portefeuille
principal toutes les 300 secondes en `observed_mark_v2`. Le dashboard sur
`http://127.0.0.1:8787` est un service distinct. Les résultats paper ne prouvent
pas une rentabilité réalisable ; aucun connecteur d'ordre réel n'est activé.

## Configuration réellement utilisée

Le runner lit en entier `state/portfolio.yaml` s'il existe, sinon en entier
`config/portfolio.yaml`. Il n'existe aucune fusion par clé. Le secours n'est
donc pas nécessairement une reproduction des réglages opérateur.
`0RUM_STATE_DIR`, défini avant les imports, redirige `state/`.

Inspecter la source, son empreinte et les stratégies sans collecte ni cycle
financier :

```sh
./.venv/bin/python scripts/run_paper_portfolio.py --dry
```

Cette commande construit les stratégies à partir de la même lecture dont elle
affiche le hash. Le service utilise `--once` : chaque invocation relit la
configuration. Le mode manuel `--loop` la conserve jusqu'à la fin du processus.

## Services et expériences

| Service | Rôle et état propre |
|---|---|
| `com.0rum.paper` | Portefeuille principal : `state/paper_*` |
| `com.0rum.dashboard` | Dashboard et paramètres opérateur du principal |
| `com.0rum.strategy-accounts` | Huit comptes natifs et un témoin : `state/strategy_accounts/v1` |
| `com.0rum.llm-paper` | Comptes LLM référence et évolutif, journaux `state/llm_*` |
| `com.0rum.portfolio-shadow` | Expérience historique distincte, `state/portfolio_shadow*` |
| `com.0rum.l2lab.20260908` | Laboratoire L2 dans son installation indépendante, port 8797 |

Lire les statuts avec leur date : un service chargé ne prouve pas un cycle
récent réussi. Les comptes ne sont pas additionnables pour calculer le rendement
d'un portefeuille unique.

Voir [les modes opérationnels](docs/OPERATING-MODES.md),
[la reprise paper](docs/decisions/ADR-018-paper-execution-and-crash-recovery-v2.md),
[les comptes par stratégie](docs/decisions/ADR-019-independent-strategy-paper-accounts.md)
et [les hypothèses LLM](docs/decisions/ADR-020-evolving-lesson-hypotheses-v2.md).

## Développement

Suivre [AGENTS.md](AGENTS.md). Travailler dans une copie isolée incluant les
modifications locales courantes. `tests/conftest.py` redirige l'état avant les
imports ; ne jamais faire écrire les tests dans les comptes opérationnels.
Depuis la copie isolée et son environnement Python de test :

```sh
python -m pytest -q tests/test_paper_engine.py tests/test_paper_broker.py tests/test_run_paper_portfolio.py tests/test_strategy_accounts.py
```

Le risque et les protections d'exécution ne sont pas modifiables par le LLM.
`DynamicRiskShadow` reste observationnel. Conserver décisions, erreurs,
versions, fills et checkpoints.

## Historique

Les helpers mono-actifs restent accessibles aux lecteurs et replays qui en
dépendent. Les anciens lanceurs et commandes CLI `start/stop/restart` refusent
toute action ; ils ne pilotent pas `com.0rum.paper`.
L'[ancien README](docs/archive/README-monoasset-20260909.md) est archivé : ses
instructions de worker, watcher, TradingView et remise à zéro ne décrivent pas
le portefeuille courant. Voir [ADR-021](docs/decisions/ADR-021-runtime-consolidation.md).
