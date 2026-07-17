# Dashboard V5 — plan d'implémentation

> Exécution sur `/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading/`, branche `strategy/backtest-parity`. La demande explicite d'implémentation autorise les changements locaux ; aucun redémarrage de processus n'est inclus sans confirmation séparée.

## 1. Figer le rollback

- Sauvegarder `orum/dashboard.py` et les trois assets `orum/static/dashboard.*`.
- Vérifier que les quatre copies existent et correspondent byte pour byte.

## 2. Spécifier le contrat d'affichage par test

- Ajouter un test qui impose des bougies d'affichage 1 h pour BTC, ETH et PAXG.
- Imposer la conservation du timeframe natif de chaque moteur.
- Imposer la présence des marqueurs paper réels, des trades appariés et d'une note explicite sur les scénarios.
- Lancer le test et constater l'échec attendu.

## 3. Étendre le snapshot sans casser l'existant

- Adapter `_market_signals` pour charger séparément les bougies 1 h et les bougies de calcul natives.
- Conserver `timeframe` pour compatibilité et ajouter `display_timeframe`.
- Garder les marqueurs/trades issus des moteurs natifs et les fills du ledger paper.
- Relancer les tests ciblés.

## 4. Spécifier la structure visuelle par test

- Ajouter un test de contrat statique pour les onglets d'actifs, le canvas SVG, le rail d'indicateurs, les libellés de scénarios et les classes des traits verticaux.
- Lancer le test et constater l'échec attendu.

## 5. Implémenter le terminal V5

- Remplacer la zone Pro Chart par le terminal V5 dans `dashboard.html`.
- Réécrire `renderProChart` en SVG léger et déterministe : bougies, volumes, momentum, événements et éventail de scénarios.
- Ajouter les onglets BTC/ETH/PAXG en conservant un seul état `paper`.
- Corriger les KPI pour lire `paper` plutôt que l'ancien portefeuille calculé.
- Appliquer le thème anthracite, la typographie fine et les règles responsive.

## 6. Vérifier

- `python3 -m py_compile orum/dashboard.py`
- `node --check orum/static/dashboard.js`
- Tests dashboard ciblés puis suite complète.
- Comparer les fichiers de sauvegarde et vérifier qu'aucun fichier d'état n'a été touché par l'implémentation.
- Lancer GitNexus `detect_changes` et contrôler le rayon réel.

## 7. Activation contrôlée

- Ne pas redémarrer automatiquement.
- Demander confirmation pour redémarrer uniquement `com.0rum.dashboard`.
- Après confirmation : vérifier `/`, `/api/state`, les trois onglets et effectuer une capture de contrôle.
- Rollback immédiat possible depuis `backups/dashboard-pre-v5-20260711/`.

