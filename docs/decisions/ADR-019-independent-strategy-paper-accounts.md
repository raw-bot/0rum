# ADR-019 — Comptes virtuels par stratégie

Statut : accepté, demande utilisateur du 5 septembre 2026 (comptes simulés et dashboard).

## Décision

Ajouter un laboratoire prospectif, sans compte externe ni ordre broker. Huit stratégies natives disposent chacune de 10 000 USD fictifs. Un neuvième compte de référence répartit 10 000 USD entre les huit stratégies. Il ne faut pas comparer la somme des huit équités à celle du témoin : les budgets sont différents. Les comptes LLM et le portefeuille principal restent autonomes.

Les neuf comptes utilisent les paramètres natifs actifs figés à l'initialisation, le risque nominal, sans DDscale/kill. Le témoin reçoit la même politique afin de distinguer l'effet de la concurrence pour le capital. Les plafonds de risque/exposition et les sorties restent actifs, notamment une position BTC et un notionnel BTC 1× **par compte**. Le risque nominal est une demande, susceptible d'être plafonnée par ces autres contraintes. L'enchère conserve le merit_order et les scores du moteur existant ; décisions, refus et grants sont conservés par cycle. DynamicRiskShadow reste purement observationnel et son artefact est copié dans l'expérience.

Le simulateur conserve observed_mark_v2 et les frais figés de 0,1 % aller-retour. Il n'introduit pas de nouveau modèle de slippage, liquidité ou marge : les résultats sont une comparaison de recherche dans les conventions paper existantes, sans validation de rentabilité réelle. Aucun réglage du dashboard principal ne modifie rétroactivement l'expérience.

## État et reprise

Racine `state/strategy_accounts/v1`, manifeste de configuration immuable et service `com.0rum.strategy-accounts`, toutes les 300 s. Chaque compte a ses propres positions/curseurs/outbox, fills, equity, dynamic exits et deux journaux shadow. Aucun fichier financier principal n'est écrit. Les providers publics sont partagés, les comptes ne le sont pas.

Avant toute exécution, le cycle collecte une fois chaque couple marché/timeframe/limite et fige les bougies dont la clôture précède le cutoff commun, ainsi que le COT. Les entrées, erreurs de collecte comprises, sont conservées. Les stratégies reçoivent des copies. La fraîcheur COT utilise aussi le cutoff du cycle, y compris après reprise. Toutes les équités utilisent la dernière clôture disponible commune par actif ; les prix d’exécution et de sortie restent issus du monitor natif de chaque stratégie. L’inventaire des flux ne dépend d’aucun checkpoint financier. Le checkpoint pending permet de reprendre les comptes non terminés avec les mêmes entrées. L'outbox existante garantit la publication idempotente des fills ; un checkpoint manquant avec historique existant refuse une remise à zéro. Une panne de compte est explicite, les autres continuent et le compte retente au cycle suivant. Les logs d'équité ne sont pas une base comptable transactionnelle ; leur corruption est signalée.

Le code reste partagé pour bénéficier des corrections. Les empreintes initiale et par cycle rendent tout changement visible ; le drapeau de rupture reste acquis même après retour au code initial. Une rupture n'arrête pas les sorties des positions ouvertes. Les analyses doivent alors séparer les périodes/méthodes, ou démarrer une nouvelle expérience explicitement. Les données archivées sont des observations au cutoff, pas une garantie que les sources de marché ne révisent jamais leurs historiques. Ne pas ajuster des paramètres sur cette fenêtre puis la présenter comme un test indépendant.

## Dashboard et vérification

Page dédiée `/strategy-accounts`, accessible par « Comptes de recherche » dans la barre supérieure du dashboard ; aucun compte de recherche affiché dans la page principale : equity avec latent si valorisation complète, rendement, courbe à échelle propre, DD maximum, positions ouvertes/trades clos, risque nominal, état, frais et décisions. Détails des positions disponibles. Le témoin est distingué. Aucune valorisation manquante n'est affichée comme un zéro ; retard de plus de 900 s et erreurs sont signalés.

Tests : concurrence BTC séparée/partagée, données communes et clôturées, état isolé, configuration figée, risque DD nominal, panne d'outbox et reprise, crash entre comptes, doublons, empreinte, corruption et dashboard. Activation : sauvegarde/hash-guard, service dédié puis rechargement dashboard et contrôle visuel/API. Le service paper principal ne nécessite aucun redémarrage.

Rollback : décharger uniquement le service de recherche ; conserver tous ses états/fills. Restaurer les fichiers dashboard sauvegardés et recharger le dashboard. Aucune restauration d'un ancien état financier par-dessus de nouveaux fills.

Le 5 septembre, à la demande de l’utilisateur, le rendu est déplacé vers une page autonome et une lecture `/api/strategy-accounts` limitée à la synthèse locale. Les comptes et paramètres ne changent pas. L’empreinte actuelle inclut le code du dashboard : ce changement d’interface explique sa variation et ne constitue pas une modification des signaux, sorties, risques ou coûts.
