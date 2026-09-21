# Comptes paper de recherche par stratégie

Autorisation : comptes virtuels séparés et visualisation dashboard (2026-09-05).

1. Figer la configuration active ; créer huit comptes à 10 000 USD et un témoin partagé neuf. Tous utilisent le risque nominal, sans DDscale/kill. Conserver les autres plafonds, sorties et coûts. Vérifier la comparaison et l'isolation par tests.
2. Réutiliser PaperEngine et son outbox par compte, avec six chemins persistants distincts. Figer les bougies clôturées et le COT avant toute exécution ; reprendre un cycle interrompu sur ces mêmes entrées. Vérifier crash/reprise, erreurs et absence de doublons.
3. Publier une synthèse atomique et une carte dashboard (équité, rendement, DD, positions, trades, risque nominal, état et courbe). Afficher explicitement données incomplètes, ancienneté, erreurs et changement de méthode.
4. Relecture adversariale, tests ciblés puis intégration. Déployer avec sauvegarde et contrôle des hashes ; lancer un service dédié 300 s et recharger le dashboard. Vérifier le premier cycle et la carte visible.

Risques : observation commune ne signifie pas exécution réelle ; le témoin a un seul capital de 10k contre huit expériences indépendantes. L'ordre d'enchères est figé. Le compte partagé de recherche n'est pas le portefeuille paper principal. Les frais et limites existants restent ceux du simulateur ; aucun résultat de validation broker. Les empreintes du code sont conservées par cycle ; un changement signale une rupture de comparabilité sans bloquer les sorties des positions ouvertes.

Inventaire : PaperEngine écrit positions (incluant curseurs et outbox), fills, equity, dynamic exits, regime shadow et risk shadow via chemins injectés. GoldCot lit le gate injecté, DynamicRiskShadow lit l'artefact figé. Aucun autre accès persistant trouvé dans les stratégies natives. Les providers gardent leur cache de données, sans accès aux comptes.

Relecture proposition : (1) reprise de fills déjà garantie par outbox existant : contexte manquant ; test additionnel. (2,5) entrées durablement figées avant comptes : action. (3) témoin même politique DD : action. (4) inventaire et tests d'isolation : action. (6) code partagé nécessaire aux correctifs, rupture signalée plutôt qu'arrêt : compromis documenté. (7,8) ordre figé, enchères/raisons/prix/coûts/complétude conservés : action.

Rollback : décharger uniquement com.0rum.strategy-accounts, conserver son état pour analyse, restaurer les fichiers dashboard sauvegardés et recharger uniquement le dashboard. Ne pas toucher aux services paper/LLM, ni à leur configuration ou historique.

Relecture code, cycle 2 : quatre constats retenus et corrigés — horloge COT injectée, marques de valorisation communes sans changer les prix d'exécution natifs, rupture de méthode persistante incluant les hashes par compte, détails des positions. Cycle 3 : inventaire de requêtes découplé du checkpoint de référence ; test vérifiant que sa perte n'empêche pas les huit comptes de poursuivre. Aucun quatrième passage ; constats résolus par preuves exécutables.
