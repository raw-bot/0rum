# Prompt : Intégration de NVIDIA Nemotron 3 Ultra 550B-A55B dans 0rum

Je veux étudier l’intégration de NVIDIA Nemotron 3 Ultra 550B-A55B dans 0rum comme couche d’analyse contextuelle et comme « learning brain » capable d’améliorer progressivement le filtrage des trades.

Je ne veux pas que Nemotron remplace directement les stratégies quantitatives existantes, ni qu’il puisse ouvrir librement des positions, modifier le risk manager ou réécrire automatiquement le code en production.

Je veux que tu analyses de manière critique la pertinence de cette architecture, les risques, les limites statistiques et la manière la plus propre de l’implémenter dans 0rum sans casser ce qui fonctionne déjà.

## Contexte

Nemotron 3 Ultra est un modèle MoE de 550 milliards de paramètres, avec environ 55 milliards de paramètres actifs par token et un très grand contexte.

Il serait utilisé via une API distante. Il n’est pas question de le faire tourner localement sur mon Mac Studio 96 Go.

Son rôle envisagé serait d’analyser :

* les news économiques et financières ;
* les annonces macroéconomiques ;
* les valeurs réelles comparées au consensus ;
* les réactions déjà observées du marché ;
* les indices et variables de contexte ;
* le régime général du marché ;
* les signaux techniques produits par 0rum ;
* les résultats des décisions précédentes.

Les entrées contextuelles pourraient notamment inclure :

* BTC et ETH ;
* S&P 500 et Nasdaq ;
* DXY ;
* VIX ;
* rendements US 2 ans et 10 ans ;
* funding crypto ;
* open interest ;
* volatilité ;
* volume ;
* VWAP ;
* calendrier économique ;
* heure exacte de publication des informations ;
* position et exposition actuelles du portefeuille.

Le problème central est qu’un LLM peut produire un raisonnement convaincant sans posséder de véritable edge prédictif. Il ne faut donc jamais considérer la qualité apparente de ses explications comme une preuve de performance.

## Fonction initiale envisagée

0rum continuerait à générer les signaux techniques.

Nemotron interviendrait uniquement comme couche de contexte et pourrait produire :

* une direction générale ;
* un régime de marché ;
* une estimation d’incertitude ;
* un veto ;
* une réduction du risque ;
* éventuellement une autorisation normale du trade.

Il ne pourrait initialement pas augmenter le risque au-dessus de la taille prévue par 0rum.

Sortie envisagée :

```json
{
  "decision": "ALLOW | REDUCE | BLOCK",
  "risk_multiplier": 0.5,
  "direction": "LONG | SHORT | NEUTRAL",
  "confidence": 0.62,
  "horizon": "4h",
  "market_regime": "risk_on | risk_off | neutral | uncertain",
  "reason_codes": [
    "macro_regime_conflict",
    "price_action_not_confirmed"
  ],
  "contradictions": [
    "DXY rising while Nasdaq remains strong"
  ],
  "invalidation_conditions": [
    "DXY reversal",
    "BTC reclaims intraday VWAP"
  ],
  "source_ids": [
    "news_184",
    "macro_event_52"
  ]
}
```

Contraintes initiales :

risk_multiplier ∈ {0, 0.5, 1}

Correspondance :

BLOCK  -> 0
REDUCE -> 0.5
ALLOW  -> 1

Nemotron ne pourrait pas :

* augmenter le risque ;
* choisir librement le levier ;
* déplacer un stop ;
* prolonger un trade ;
* créer une nouvelle stratégie en production ;
* modifier son propre prompt ;
* déployer du code ;
* modifier les règles du risk manager ;
* prendre une décision en l’absence de données fraîches et correctement horodatées.

## Objectif du premier test

La première question ne doit pas être :

Nemotron peut-il trader de façon autonome ?

La question doit être :

Lorsque 0rum produit un candidat de trade, Nemotron peut-il distinguer les contextes dans lesquels ce candidat présente une expectancy positive ou négative ?

Le premier test porterait donc sur la capacité de Nemotron à :

* autoriser un trade ;
* le bloquer ;
* réduire son risque.

## Protocole de test proposé

### 1. Enregistrement de toutes les opportunités

Il faut enregistrer chaque candidat de trade produit par 0rum, y compris ceux qui ne sont pas exécutés.

Pour chaque opportunité :

* timestamp précis ;
* actif ;
* sens du signal ;
* stratégie source ;
* prix d’entrée théorique ;
* stop ;
* objectif ;
* ATR ;
* volatilité ;
* indicateurs techniques ;
* exposition du portefeuille ;
* contexte intermarket ;
* news disponibles ;
* calendrier macro ;
* décision Nemotron ;
* confiance ;
* version du prompt ;
* version du modèle ;
* résultat réel ou simulé ;
* frais ;
* slippage ;
* résultat en R ;
* MAE ;
* MFE ;
* durée du trade.

Exemple :

```json
{
  "candidate_id": "btc_2026_07_20_10_00_long",
  "asset": "BTC/USDT",
  "strategy": "ak_macd_15m",
  "signal": "LONG",
  "nemotron_decision": "REDUCE",
  "risk_multiplier": 0.5,
  "confidence": 0.68,
  "regime": "risk_off",
  "result_r": -0.42,
  "mae_r": -0.81,
  "mfe_r": 0.17,
  "holding_time_minutes": 190
}
```

### 2. Shadow trades obligatoires

Tous les trades bloqués ou réduits par Nemotron doivent continuer à être simulés comme s’ils avaient été pris normalement.

Il faut conserver simultanément :

* le résultat réel avec intervention Nemotron ;
* le résultat contrefactuel de 0rum seul ;
* le résultat de la taille complète ;
* le résultat de la taille réduite ;
* le résultat du trade bloqué simulé.

Sans cela, il est impossible de savoir si les vetos de Nemotron sont utiles.

### 3. Groupes de contrôle

Comparer au minimum :

A. 0rum seul
B. 0rum + Nemotron
C. Nemotron avec indices mais sans news
D. Nemotron avec news mais sans indices
E. Nemotron avec news mélangées aléatoirement
F. Décisions aléatoires avec le même taux de blocage
G. Règle risk-on/risk-off simple sans LLM
H. Ancienne version du prompt Nemotron
I. Nouvelle version challenger

Une règle simple de comparaison pourrait être :

Risk-off lorsque :
- DXY monte ;
- rendement US 10Y monte ;
- S&P 500 baisse ;
- VIX monte.

Nemotron doit battre une règle simple pour justifier sa complexité, son coût et sa latence.

### 4. Replay chronologique strict

Lors des tests historiques, Nemotron ne doit voir que les informations réellement disponibles à l’instant t.

Il ne doit jamais recevoir :

* des bougies futures ;
* une news publiée après le signal ;
* un résumé rédigé après l’événement ;
* une version d’article mise à jour ultérieurement ;
* une valeur macro révisée non disponible à cet instant ;
* une réaction du marché postérieure à la décision ;
* un indicateur calculé avec des données futures.

Chaque donnée doit avoir au minimum :

event_time
published_at
available_at
ingested_at
source
revision_number

Il faut distinguer l’heure de l’événement, l’heure de publication et l’heure réelle d’ingestion par le système.

### 5. Risque de contamination du modèle

Nemotron peut avoir vu certains événements historiques pendant son entraînement.

Un backtest historique peut donc être artificiellement bon parce que le modèle connaît indirectement la suite.

Il faut privilégier :

* un test forward en paper trading ;
* des événements postérieurs à la période d’entraînement connue ;
* ou des données dont les noms, dates et entités sont masqués pour certains tests expérimentaux.

Le test historique doit être considéré comme exploratoire, pas comme preuve finale.

## Métriques à mesurer

Ne pas limiter l’évaluation au rendement brut.

Mesurer :

* expectancy en R ;
* profit factor ;
* drawdown maximal ;
* Sharpe ;
* Sortino ;
* taux de réussite ;
* rendement net après frais ;
* slippage ;
* turnover ;
* nombre de trades autorisés ;
* nombre de trades réduits ;
* nombre de trades bloqués ;
* résultat moyen des trades autorisés ;
* résultat moyen des trades bloqués en shadow ;
* bons trades bloqués ;
* mauvais trades évités ;
* perte d’opportunité causée par les vetos ;
* stabilité par régime ;
* performance par horizon ;
* performance par niveau de confiance ;
* calibration des probabilités ;
* coût API par décision ;
* latence ;
* taux d’erreur ou de JSON invalide.

Deux métriques centrales :

Résultat cumulé des trades bloqués en shadow
Résultat cumulé des trades autorisés

Exemple favorable :

Trades bloqués : -18 R
Trades autorisés : +31 R

Exemple défavorable :

Trades bloqués : +12 R
Trades autorisés : +8 R

Dans le second cas, Nemotron détruit probablement une partie de l’edge.

## Test de stabilité

La même situation doit être présentée plusieurs fois avec :

* un ordre différent des news ;
* des titres légèrement reformulés ;
* les sources masquées ;
* des informations non pertinentes ajoutées ;
* plusieurs températures ;
* plusieurs seeds si disponibles ;
* plusieurs exécutions identiques.

Mesurer la distribution des réponses.

Exemple stable :

10 répétitions :
8 ALLOW
2 REDUCE
0 BLOCK

Exemple instable :

10 répétitions :
4 ALLOW
3 REDUCE
3 BLOCK

Une décision trop instable ne doit pas être utilisée pour piloter le risque.

## Tests adversariaux

Créer des cas où :

* le titre est positif mais le contenu est négatif ;
* la news est ancienne ;
* la même dépêche est dupliquée plusieurs fois ;
* la valeur publiée est positive en absolu mais inférieure au consensus ;
* la news concerne un autre actif ;
* la source est douteuse ;
* le prix a déjà absorbé l’information ;
* la réaction du marché contredit le récit macro ;
* deux sources crédibles se contredisent ;
* une annonce est ensuite révisée ;
* une news très importante arrive juste après la décision.

Nemotron doit pouvoir répondre NEUTRAL, REDUCE ou INSUFFICIENT_DATA, plutôt que forcer une direction.

## Learning brain envisagé

Je ne veux pas que Nemotron modifie ses propres poids après chaque trade.

Je veux une boucle d’apprentissage contrôlée :

Signal 0rum
    ↓
Snapshot complet du contexte
    ↓
Décision Nemotron
    ↓
Trade réel ou shadow
    ↓
Résultat enregistré
    ↓
Analyse périodique des erreurs
    ↓
Hypothèse proposée
    ↓
Backtest walk-forward
    ↓
Shadow mode
    ↓
Promotion ou rejet

Nemotron servirait principalement à :

* analyser les erreurs ;
* rechercher les anciens cas similaires ;
* identifier des régimes ;
* proposer de nouvelles variables ;
* expliquer les faux vetos ;
* expliquer les mauvais trades autorisés ;
* proposer des règles candidates ;
* détecter une éventuelle dérive du comportement du marché.

Il ne déciderait pas seul qu’une règle doit être activée.

### Trois niveaux d’apprentissage

**Niveau 1 — Mémoire des expériences**

Créer une base structurée des situations passées.

Pour une nouvelle opportunité, récupérer les cas les plus similaires selon :

* stratégie ;
* actif ;
* direction ;
* régime ;
* volatilité ;
* DXY ;
* taux ;
* indices ;
* funding ;
* open interest ;
* type de news ;
* proximité d’un événement macro ;
* structure technique ;
* session de marché.

Nemotron recevrait un nombre limité de cas similaires avec leurs résultats.

Il faudrait éviter un RAG naïf uniquement basé sur des embeddings textuels. Les filtres temporels et les variables numériques doivent être prioritaires.

**Niveau 2 — Meta-modèle statistique**

Nemotron extrait des variables structurées des news et du contexte.

Un modèle statistique plus petit apprend ensuite :

P(trade rentable | signal 0rum, régime, news, indicateurs)

Modèles envisageables :

* régression logistique ;
* gradient boosting ;
* modèle bayésien ;
* random forest ;
* contextual bandit ;
* petit réseau neuronal si le volume de données le justifie.

Le meta-modèle, et non Nemotron seul, pourrait produire le score final de filtrage.

Nemotron agirait comme extracteur de caractéristiques complexes et analyste.

**Niveau 3 — Challenger périodique**

Toutes les quelques centaines de décisions, Nemotron analyse :

* les faux vetos ;
* les mauvais trades autorisés ;
* les situations où sa confiance était incorrecte ;
* les variables devenues inutiles ;
* les régimes où la politique échoue ;
* les nouvelles hypothèses possibles.

Il produit alors une nouvelle politique candidate.

Cette politique doit :

1. être figée ;
2. être testée en walk-forward ;
3. passer en shadow mode ;
4. battre la version active ;
5. respecter les limites de risque ;
6. être promue seulement après validation explicite.

## Architecture envisagée

Market Data / News / Macro
        ↓
Event Normalizer
        ↓
Point-in-time Feature Store
        ↓
0rum Strategy Engine
        ↓
Trade Candidate
        ↓
Nemotron Context Analyzer
        ↓
Structured Decision JSON
        ↓
Statistical Meta-model
        ↓
Policy Engine
        ↓
Immutable Risk Manager
        ↓
Execution or Shadow Execution
        ↓
Outcome Store
        ↓
Evaluator
        ↓
Champion / Challenger Registry

Composants souhaités :

news_ingestor
event_normalizer
point_in_time_store
context_builder
nemotron_client
nemotron_schema_validator
decision_logger
shadow_trade_engine
outcome_resolver
similar_case_retriever
meta_model
evaluation_runner
champion_challenger_manager
policy_registry

## Séparation stricte des responsabilités

**0rum**
Produit le signal technique original.

**Nemotron**
Analyse :
* news ;
* macro ;
* indices ;
* régime ;
* contradictions ;
* cas similaires ;
* contexte du signal.

**Meta-modèle statistique**
Évalue si les variables produites par Nemotron ont réellement une valeur prédictive.

**Policy engine**
Transforme les scores en :
ALLOW
REDUCE
BLOCK
selon des seuils versionnés.

**Risk manager**
Conserve le dernier mot.
Ses règles sont non modifiables par Nemotron.

**Evaluator**
Compare :
* baseline ;
* champion actif ;
* challenger ;
* règles simples ;
* contrôles aléatoires.

## Règles de sécurité

Le système doit fonctionner en fail-safe.

En cas de :
* timeout API ;
* JSON invalide ;
* source manquante ;
* news trop ancienne ;
* modèle indisponible ;
* contradiction majeure ;
* confiance insuffisante ;
* latence excessive ;
* version inconnue du modèle ;
* problème de timestamp ;

le comportement par défaut doit être clairement défini.

Je pense que le fallback le plus propre est :

Nemotron indisponible ou données invalides
→ 0rum continue selon sa politique baseline

Mais analyse aussi si un mode REDUCE par défaut serait plus prudent dans certains cas.

Nemotron ne doit jamais être un point de panne bloquant pour l’ensemble du bot.

## Phase de déploiement

Phase 0 — Observation
Nemotron analyse les candidats, mais sa décision n’a aucun effet.

Phase 1 — Shadow veto
Les décisions sont enregistrées et comparées, mais jamais appliquées.

Phase 2 — Réduction uniquement
Nemotron peut réduire un trade à 50 %, mais pas le bloquer.

Phase 3 — Veto limité
Nemotron peut bloquer uniquement certains cas précisément définis.

Phase 4 — Policy engine avec meta-modèle
La décision combine :
* Nemotron ;
* résultats historiques ;
* meta-modèle statistique ;
* règles de risque.

Phase 5 — Éventuelle autonomie élargie
Seulement si les phases précédentes montrent un edge robuste.

## Critères de promotion

Un challenger ne doit pas être promu uniquement parce que son PnL est supérieur.

Il doit au minimum :
* ne pas augmenter le drawdown au-delà de la limite ;
* améliorer ou maintenir l’expectancy ;
* rester performant sur plusieurs périodes ;
* rester performant sur plusieurs régimes ;
* survivre aux frais et au slippage ;
* ne pas dépendre de quelques trades extrêmes ;
* conserver une stabilité décisionnelle acceptable ;
* avoir une confiance correctement calibrée ;
* battre une règle simple ;
* battre un contrôle aléatoire ;
* ne pas utiliser d’information future ;
* passer un test forward.

Prévoir également :
* bootstrap des trades ;
* intervalles de confiance ;
* analyse de sensibilité ;
* permutation tests ;
* walk-forward ;
* purged cross-validation ;
* embargo temporel ;
* séparation stricte train/validation/test.

## Questions auxquelles je veux que tu répondes

1. Cette architecture est-elle pertinente pour 0rum ou inutilement complexe ?
2. Nemotron apporte-t-il quelque chose qu’un modèle plus petit ou moins coûteux ne pourrait pas faire ?
3. Quel rôle exact lui donnerais-tu :
    * analyste de news ;
    * classificateur de régime ;
    * veto ;
    * extracteur de features ;
    * générateur d’hypothèses ;
    * mémoire des expériences ;
    * combinaison de plusieurs rôles ?
4. Quelles parties de cette proposition sont statistiquement fragiles ?
5. Quels risques de look-ahead, contamination, survivorship bias ou overfitting vois-tu ?
6. Comment garantir un véritable point-in-time dataset ?
7. Comment gérer les news mises à jour, corrigées ou révisées ?
8. Quelle structure de base de données utiliserais-tu ?
9. Quels schémas de tables ou objets proposerais-tu ?
10. Quelles interfaces faut-il ajouter à 0rum ?
11. Où intégrer cette couche sans modifier les stratégies existantes ?
12. Comment gérer proprement le mode shadow ?
13. Comment simuler les trades bloqués sans interférer avec le portefeuille réel ?
14. Comment comparer les résultats à la baseline ?
15. Quel meta-modèle statistique utiliser en premier ?
16. Quelles variables Nemotron devrait-il produire ?
17. Quelles variables doivent rester purement quantitatives ?
18. Comment empêcher Nemotron d’inventer des faits ou des sources ?
19. Comment vérifier chaque source et chaque timestamp ?
20. Comment tester la stabilité de ses décisions ?
21. Quelle fréquence de décision utiliser :
* chaque candidat ;
* toutes les heures ;
* uniquement sur événement ;
* combinaison des trois ?
22. Comment gérer le coût et la latence de l’API ?
23. Faut-il mettre en cache les analyses de régime ?
24. Comment versionner :
* modèle ;
* prompt ;
* données ;
* règles ;
* schéma JSON ;
* meta-modèle ;
* policy engine ?
25. Quelles métriques et seuils de promotion recommandes-tu ?
26. Combien de décisions faut-il approximativement avant de tirer une première conclusion ?
27. Quel serait le MVP le plus petit permettant de tester l’idée sans construire immédiatement tout le système ?
28. Quels tests unitaires, d’intégration et de non-régression faut-il créer ?
29. Quels composants existants de 0rum peuvent être réutilisés ?
30. Quels composants doivent absolument rester isolés ?

## Ce que j’attends de ta réponse

Je veux une analyse critique, pas une validation de principe.

Commence par me dire franchement :
* ce qui est pertinent ;
* ce qui est inutile ;
* ce qui est dangereux ;
* ce qui est trop complexe ;
* ce qui manque.

Ensuite, propose un MVP minimal.

Le MVP doit idéalement :
* ne modifier aucune logique de stratégie existante ;
* ne modifier aucun stop ;
* ne modifier aucun take-profit ;
* ne modifier aucun sizing baseline ;
* ne modifier aucune règle du risk manager ;
* fonctionner d’abord uniquement en shadow ;
* enregistrer les décisions de Nemotron ;
* simuler les trades bloqués ou réduits ;
* comparer automatiquement 0rum seul et 0rum + Nemotron.

Donne ensuite :
1. l’architecture ;
2. les nouveaux modules ;
3. les interfaces ;
4. les schémas de données ;
5. les fichiers potentiellement concernés ;
6. les étapes d’implémentation ;
7. les tests ;
8. les critères de validation ;
9. les risques ;
10. un plan de rollback.

Ne code rien immédiatement.

Inspecte d’abord le dépôt 0rum, identifie son architecture réelle et vérifie notamment :
* comment sont générés les candidats de trades ;
* où intervient le paper broker ;
* comment le sizing est calculé ;
* comment sont enregistrés les fills ;
* comment fonctionne le portefeuille ;
* comment sont gérés les événements ;
* où intégrer un observer sans modifier le comportement actuel ;
* comment éviter toute interaction avec le worker mono-actif existant ;
* comment préserver la séparation entre les stratégies BTC, ETH et or.

Après inspection, propose une implémentation par étapes, avec la modification minimale possible et sans casser ce qui fonctionne déjà.

Le point essentiel à faire défendre par Codex est un MVP shadow indépendant : Nemotron observe, décide et apprend, mais n’a encore aucun effet sur les ordres.
