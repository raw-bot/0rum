# Prompt — Recherche d'edges testables pour 0rum

## Mission

Tu agis comme un chercheur quantitatif senior spécialisé en stratégies systématiques, microstructure, économétrie, validation statistique et trading automatisé.

Ta mission est d'identifier des edges réellement testables pour mon bot 0rum, accessibles à un particulier résidant en France.

Je ne veux ni stratégies populaires simplement recyclées, ni indicateurs techniques standard assemblés artificiellement, ni promesses de rendement. Je privilégie ta propre réflexion : utilise tes connaissances en finance, microstructure, statistiques et comportement des marchés pour concevoir toi-même des hypothèses d'edge originales, cohérentes et falsifiables. Les travaux existants servent de matière première, de contrainte et d'inspiration, jamais de catalogue à recopier. Tu peux combiner, transformer ou prolonger des mécanismes documentés lorsqu'ils renforcent ton raisonnement, mais l'objectif est une thèse issue de ta propre analyse, potentiellement rentable après tous les coûts réels.

## Périmètre

Ne te limite pas à BTC, ETH ou à l'or. Explore notamment :

- crypto spot et dérivés accessibles en Europe ;
- ETF UCITS ;
- indices, actions et futures européens ;
- devises et matières premières ;
- volatilité, carry, basis et roll yield ;
- saisonnalités ;
- effets de sessions et de calendrier ;
- flux institutionnels contraints ;
- rebalancements, expirations et fixings ;
- stratégies cross-asset ;
- market-neutral et relative value ;
- niches trop petites ou trop lentes pour les grands acteurs.

Contraintes non négociables : légal et accessible depuis la France ; compatible avec un capital de particulier ; automatisable sans avantage de latence ; testable avec des données accessibles ; réaliste après spread, commissions, slippage, financement et fiscalité opérationnelle.

## Sources prioritaires

- Robot Wealth et la méthode Edge Alchemy ;
- AQR Research ;
- Man Group, Man AHL et Man Institute.

Ces sources servent à identifier des mécanismes économiques et des hypothèses, pas à prouver qu'un edge fonctionne encore. Pour chaque idée, remonte autant que possible vers : l'étude originale ; les données utilisées ; les réplications indépendantes ; les résultats récents ; les critiques ou résultats contradictoires.

## Méthode

### 1. Génération

Produis entre 15 et 20 hypothèses d'edge. Pour chacune, indique brièvement : marché et instrument ; horizon ; mécanisme économique ; acteurs à l'origine de l'anomalie ; raison possible de sa persistance ; coûts principaux ; données nécessaires ; accessibilité depuis la France.

### 2. Boucle interne de critique

Travaille par cycles successifs de génération, critique et révision — maximum cinq cycles, arrêt anticipé si aucun progrès substantiel. À chaque cycle, agis en reviewer hostile de tes propres hypothèses et recherche systématiquement :

- data mining ;
- look-ahead ;
- biais de survivance ;
- faible échantillon ;
- coûts oubliés ;
- dépendance à un régime ;
- exposition cachée au bêta, carry, volatilité ou liquidité ;
- changement de microstructure ;
- crowded trade ;
- difficulté réelle d'exécution ;
- problème réglementaire ou d'accès au produit.

Élimine toutes les idées dont le mécanisme ou la validation sont insuffisants. Conserve uniquement celles dont le mécanisme économique, la faisabilité opérationnelle et le protocole de validation se renforcent de cycle en cycle.

### 3. Sélection provisoire

Conserve au maximum 5 edges, classés selon : solidité économique ; potentiel après coûts ; robustesse statistique ; simplicité ; disponibilité des données ; facilité d'automatisation ; accessibilité depuis la France ; concurrence ; risque opérationnel ; risque de sur-optimisation.

### 4. Contradiction externe — GPT-5.6 Thinking

N'intervient qu'après la sélection provisoire, jamais pendant la génération, afin de ne pas biaiser la conception initiale.

Transmets à GPT-5.6 Thinking chaque edge provisoirement retenu avec : son mécanisme économique ; les acteurs supposés créer l'opportunité ; la spécification du signal ; les données nécessaires ; les coûts et contraintes d'exécution ; les preuves disponibles ; les principales incertitudes ; le protocole de validation envisagé.

Demande-lui d'agir comme un chercheur quantitatif hostile chargé de réfuter les hypothèses — pas d'en proposer de nouvelles. Il doit rechercher en priorité :

- mécanisme économique incohérent ou insuffisant ;
- anomalie déjà connue, arbitrée ou surpeuplée ;
- exposition cachée à un facteur classique ;
- biais de sélection, look-ahead ou data mining ;
- coûts, slippage ou contraintes d'exécution sous-estimés ;
- insuffisance de l'échantillon ;
- dépendance excessive à un régime ;
- changements récents de microstructure ou de réglementation ;
- impossibilité pratique pour un particulier en France ;
- explication alternative plus simple des performances attendues.

Analyse ensuite chaque objection toi-même — sans l'accepter automatiquement ni défendre ton idée à tout prix. Classe chaque critique : valide ; partiellement valide ; non démontrée ; réfutée par les données ou le raisonnement. Révise, dégrade ou élimine les edges en conséquence.

Maximum trois itérations ; arrête quand la critique ne produit plus d'objection nouvelle et substantielle. Le consensus entre modèles ne constitue jamais une preuve : la décision finale reste fondée sur la qualité du mécanisme, des données et du protocole de réfutation.

## Format exigé pour chaque edge finaliste

### Thèse

La source de l'edge ; quel acteur paie la prime ; pourquoi il continue à la payer ; pourquoi l'edge pourrait encore exister.

### Spécification testable

Univers ; signal ; entrée ; sortie ; horizon ; sens (long, short ou market-neutral) ; paramètres indispensables ; critères précis d'invalidation.

### Données

Sources possibles ; fréquence ; profondeur historique ; coût ; limites de qualité.

### Validation

Au minimum : in-sample / out-of-sample ; walk-forward ; stress tests de frais et slippage ; stabilité des paramètres ; analyse par régime ; comparaison à un benchmark simple ; correction du multiple testing ; bootstrap ou Monte Carlo ; Deflated Sharpe Ratio si pertinent ; test sur plusieurs actifs ou périodes.

### Faisabilité française

Disponibilité réelle de l'instrument ; restrictions PRIIPs ; réglementation MiCA si applicable ; accès via broker ou exchange autorisé ; contraintes européennes pertinentes. N'invente aucune information juridique ; signale les points nécessitant une vérification officielle.

### Verdict

Note sur 10 pour : crédibilité du mécanisme ; facilité de test ; coût de mise en œuvre ; concurrence ; robustesse potentielle ; potentiel réaliste après coûts.

Décision finale : TESTER IMMÉDIATEMENT ; TESTER APRÈS COLLECTE DE DONNÉES ; À SURVEILLER ; REJETER.

## Conclusion

Termine par :

1. les 3 edges prioritaires ;
2. l'ordre de test ;
3. les données à obtenir ;
4. le premier prototype à implémenter dans 0rum ;
5. les critères go/no-go ;
6. un plan de recherche sur 30 jours ;
7. les idées à rejeter définitivement.

Ne force jamais une conclusion positive : si aucune idée ne résiste sérieusement à la critique, conclus explicitement qu'aucun edge suffisamment crédible n'a été identifié et précise quelles données, connaissances ou recherches supplémentaires seraient nécessaires. Une conclusion négative mais solide vaut mieux qu'une stratégie séduisante construite sur un backtest fragile.
