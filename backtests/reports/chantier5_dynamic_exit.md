# Chantier 5 — Dynamic Exit AK (MFE + SSL)

Date: 2026-07-22
Statut: implémenté et rejoué hors ligne; runtime limité à `observe`

## Protocole

- Chemin testé: vrai `PaperEngine` via `scripts/run_replay_harness.py runtime`.
- Fenêtre: 2026-01-01 → 2026-07-15, 18 721 cycles.
- Empreinte données:
  `d5efff8d2d9461ad1f1ab189b01b0bdfa65cf3d3f1e72a36e3ebdcc3a4893571`.
- Même portefeuille, même ordre de mérite, même politique `topup`.
- Variable unique: `dynamic_exit.mode` (`disabled`, `observe`, `execute`).
- Le mode execute reste une simulation de replay; il n'est pas activé dans
  `state/portfolio.yaml`.

Commandes reproductibles depuis la racine, avec les snapshots locaux:

```bash
PYTHONPATH=. .venv/bin/python scripts/run_replay_harness.py runtime \
  --run-id dynamic_exit_observe_final_20260722 \
  --start 2026-01-01 --end 2026-07-15 \
  --portfolio-config state/portfolio.yaml --arbiter topup
```

Pour les deux autres variantes, une copie temporaire du même YAML remplace
uniquement `mode: observe` par `disabled` ou `execute`.

## Résultats

| Variante | Equity finale | Rendement | Drawdown max / pic | Positions finales |
|---|---:|---:|---:|---:|
| Dynamic disabled | 11 732,54 $ | +17,33 % | 25,82 % | 1 |
| Dynamic observe | 11 732,54 $ | +17,33 % | 25,82 % | 1 |
| Dynamic execute (replay) | 12 698,52 $ | +26,99 % | 21,97 % | 0 |

Écart execute contre bracket seul: **+965,98 $**, soit +9,66 points de
rendement, avec -3,85 points de drawdown maximal.

Le replay execute produit 7 sorties `dynamic_stop`, pour 1 606,00 $ de PnL
réalisé cumulé sur ces sorties. Ce chiffre ne constitue pas une promesse de
performance: il sert à vérifier que le mécanisme combat bien le giveback sur
la fenêtre retenue.

## Parité et absence d'effet observe

Les flux equity `disabled` et `observe` sont identiques octet pour octet:

```text
sha256 50f8b44c58532effe99ae9607c8794e9a64b0f9605309ffa5f2d8a23f04b6a83
```

Le journal observe enregistre 6 sorties hypothétiques `dynamic_stop` pour les
positions du chemin bracket réel, sans modifier aucun solde ni fill. Les fills
observe ajoutent uniquement l'audit de politique dynamique sur les nouvelles
positions; après suppression de cette clé d'audit, leur flux canonique est
identique au flux disabled:

```text
sha256 98597fc2f045f1bec7bb5f2760c0e001f8b510af6d4eb58375361af6b826f2d2
```

## Garde d'activation

La configuration livrée reste `mode: observe`. Une promotion à `execute`
demande encore une décision opérateur distincte après observation live. Les
positions déjà ouvertes ne sont jamais migrées automatiquement; une position
observe ne peut donc pas devenir exécutable par simple changement du YAML.
