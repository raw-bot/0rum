# Modes opérationnels et statut des composants

Responsabilités confirmées le 9 septembre 2026. Les chemins sont relatifs au
bot ; Documents/00-code/0rum est un moteur de recherche différent.

## Configuration et autorité

Le runner sélectionne un fichier complet : `0RUM_STATE_DIR/portfolio.yaml`
(ou `state/portfolio.yaml`), puis `config/portfolio.yaml` seulement si le premier
manque. Pas de fusion. Un chemin explicite absent conserve aussi ce secours.
Les erreurs de lecture/YAML ne déclenchent pas de basculement silencieux.

`--dry` lit une fois ce fichier, affiche son chemin, son hash sémantique (même
calcul que `config_sha256` du statut) et ses stratégies. Il ne collecte pas de
prix et ne lance aucun cycle financier. `com.0rum.paper --once` est relancé
toutes les 300 secondes ; `--loop` construit une seule instance.

Différences volontairement conservées : UTBot 0,005 dans le secours contre 0,01
dans l'état opérateur ; `max_leverage` absent du secours contre 3,0 dans l'état.
Ce dernier est un plafond par tranche ; le plafond notionnel BTC 1× est
distinct. Ne pas synchroniser ces valeurs lors d'un nettoyage. Les manifestes
des expériences figées ne sont pas réécrits.

## Inventaire et décision de maintien

| Composant | Décision et motif |
|---|---|
| Portefeuille principal | Maintenir : compte opérateur et référence opérationnelle |
| Comptes individuels + témoin partagé | Maintenir séparés : comparaison capital séparé/partagé, ADR-019 |
| LLM référence / évolutif | Maintenir séparés : mesurer les hypothèses ; une leçon active ne prouve pas un gain |
| DynamicRiskShadow et filtres shadow | Maintenir observationnels : aucune autorité sur le risque appliqué |
| `portfolio_shadow.py` | Maintenir comme expérience historique : EMA et `kelly_6pct` diffèrent du principal ; ne pas fusionner les courbes ni abandonner ses positions |
| Laboratoire L2 | Maintenir dans son installation autonome ; aucun changement de ses comptes/protocole |
| `orum.loop`, DSL et helpers externes | Compatibilité : encore importés par stratégies, lecteurs et replays |
| Anciennes suites de lancement mono-actif | Retirées : stubs explicites, aucun démarrage/arrêt implicite |
| `forecast_gate` dans les configurations courantes | Retiré : option ignorée depuis le retrait de l'expérience ; anciennes configurations toujours lisibles |

`com.0rum.engine` était non chargé lors de la vérification. Son plist peut
encore pointer sur le stub `scripts/run_engine.sh`. Les plists et services
actifs ne sont pas supprimés par cette consolidation.

## Contrôle des services

Le dashboard et le paper ont des cycles de vie distincts. Les anciennes
commandes CLI `start/stop/restart` échouent avant toute action et ne sont pas
redirigées vers une nouvelle cible. `scripts/0rum status` décrit le statut paper
horodaté ; `logs` lit `state/paper.out`. Les contrôles dashboard restent propres
au dashboard.

Inspection système en lecture seule :

```sh
launchctl print gui/$(id -u)/com.0rum.paper
launchctl print gui/$(id -u)/com.0rum.strategy-accounts
```

Tout contrôle intentionnel cible le service exact après vérification des
comptes/processus ; pas de `pkill` global. Après publication entre deux
invocations `--once`, vérifier un cycle démarré après la publication complète.
Un hash du disque calculé après le cycle ne prouve pas le code déjà importé.

## Contrat des nouvelles expériences

Avant lancement, consigner : hypothèse, code/données, comptes/témoins, règles
figées, coûts/exécution, utilité mesurable, budget d'essais, échéance de revue,
critères d'arrêt et service responsable. À la revue : continuer, conclure
insuffisant/négatif, ou retirer explicitement. Préserver les preuves et traiter
les positions ouvertes avant de retirer un processus. Une implémentation ne
devient pas automatiquement une dépendance du principal.
