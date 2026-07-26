# Campagne 2 — Edges on-chain (charte) — 2026-07-26

Héritage : protocole de `/Applications/0rum/Docs/PROMPT_EDGE_RESEARCH.md` (génération → cycles de critique interne → sélection → contradiction GPT-5.6 via l'utilisateur, max 3 itérations → tests pré-enregistrés committés avant tout calcul). Bilan campagne 1 : 18 hypothèses, 0 survivante — conclusion négative documentée (`Docs/EDGE_RESEARCH_2026-07.md` §8).

## Périmètre

Données on-chain et para-chain crypto uniquement : flux et émissions de stablecoins, comportement des cohortes de détenteurs, flux d'exchanges, frais/congestion, activité réseau, TVL et flux DeFi. Instruments d'exécution : BTC/ETH spot (venues MiCA) — inchangé.

## Inversion de processus (leçon R2)

**Phase 0 = audit des données AVANT toute hypothèse.** On ne génère que des hypothèses dont chaque variable est déjà auditée : source gratuite/pérenne, profondeur ≥ 4-5 ans, cadence connue, méthodologie stable, pas de révision rétroactive silencieuse, réplicabilité locale. Toute variable non auditée = hypothèse interdite.

## Contraintes de génération durcies (payées en campagne 1)

1. ≥ 20-30 événements indépendants/an, ou signal continu à horizon ≥ hebdomadaire.
2. Edge brut estimé ≥ 5× le coût aller-retour au capital réel.
3. L'originalité ne peut pas reposer entièrement sur une seule variable incrémentale — le mécanisme doit survivre à la dégradation de sa variable la plus fragile.
4. Méfiance par défaut envers toute anomalie publiée depuis > 10 ans (décroissance post-publication démontrée sur R1).
5. Le null-benchmark « même stratégie sans la variable on-chain » est obligatoire dès la conception (leçon du discriminant OI).
6. Développement/confirmation séparés dans le temps ; le holdout Kraken 2024-2026 déjà collecté peut servir de période de confirmation prix ; critères d'abandon fixés avant tout calcul.

## Livrables

Phase 0 : `AUDIT_ONCHAIN.md` (sources auditées, verdict par variable). Phase 1 : hypothèses (uniquement sur variables PASS). Phase 2 : critique interne + sélection. Phase 3 : contradiction GPT-5.6 (via l'utilisateur). Phase 4 : pré-enregistrement + test. Une conclusion négative reste un résultat valide.
