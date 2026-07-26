# Campagne 3 — Contradiction externe, itération 1 adjugée (2026-07-26)

Verdicts GPT-5.6 : A sous conditions, B rejetée, C rejetée. Adjudication :

## B (VRP conditionnel defined-risk) — objections ACCEPTÉES, candidate ÉLIMINÉE

1. **Le condor ne mesure pas le VRP** : valide — exposition composite (skew, kurtosis, chemin, ailes) ; un résultat, positif ou négatif, ne trancherait pas la croyance revendiquée. C'est un défaut de *design de test*, rédhibitoire dans notre protocole.
2. **52 entrées hebdo ≈ 10-15 blocs indépendants** avec 30-45 DTE chevauchants : valide — c'est notre propre règle retournée contre la candidate, correctement.
3. **Prints sans NBBO = simulation à 4 jambes non crédible** : valide et opérationnellement fatal — une pénalité uniforme ne modélise pas un crédit net de combo dépendant du régime.
Sa référence (alphas d'options indicielles ≈ 0 depuis 15 ans, Chicago Fed) charge la barque. Éliminée sans itération.

## C (tilt skew long-only) — objections ACCEPTÉES, candidate ÉLIMINÉE

1. **« 25Δ » viole notre propre contrainte model-free** : valide — un delta exige modèle + synchronisation ; auto-contradiction de ma spec, attrapée par la revue. Consigné comme leçon : *chaque variable d'une spec doit être re-vérifiée contre les contraintes gelées de la campagne*.
2. **Le bid-ask bounce devient le signal lui-même** (put OTM à l'ask vs ATM au bid = pente artificielle) : valide — la variable centrale est précisément la moins identifiable dans nos données.
3. **Long-only abandonne la jambe porteuse de l'anomalie publiée** : valide — change la proposition, expositions défensives résiduelles inexplicables.
Éliminée sans itération.

## A (PEAD conditionné au move implicite) — conditions ACCEPTÉES, revendication RESSERRÉE

Revendication adoptée (formulation GPT, verbatim) : *« À taille, signe et contexte de gap comparables, l'erreur ex ante du marché d'options sur l'ampleur du mouvement apporte-t-elle une information incrémentale sur le rendement anormal post-annonce ? »* — pas « dépasser le move ⇒ PEAD ».

Intégration des 9 conditions dans la spec révisée :

1. **Null incrémental apparié** : événements appariés sur {signe et décile du |gap anormal|, RV pré-annonce, taille, secteur, AMC/BMO}, différant par le percentile de move implicite ; l'effet testé est la différence de drift entre appariés + version régression flexible. Marge économique minimale δ pré-enregistrée.
2. **Event variance, pas straddle brut** : m_e = √max(TV_front − var_base×d, 0), var_base extraite de l'échéance suivante ne couvrant pas l'annonce (interpolation à deux échéances, model-light, prix de straddles uniquement).
3. **Robustesse aux prints** : 3 estimateurs pré-fixés du prix de straddle (VWAP winsorisé dernières 2 h, médiane, dernier print admissible) ; événement exclu si le rang de s diverge entre estimateurs ; si exclusions > seuil → données insuffisantes, abandon.
4. **Dates point-in-time — GATE N°1 de la phase empirique** : datation par signature de marché définie ex ante (gap anormal overnight > seuil ET crush de la variance front) recoupée avec les feeds de rapports ; aucune heure corrigée ex post. Si l'audit de datation échoue → candidate non testable, rejet opérationnel (précédent : gate OI de R2).
5. **Gap anormal** : gap − β×gap marché (β fixé ex ante), AMC/BMO séparés par construction.
6. **Expositions cachées** : rapporté brut / sector-neutral / apparié ; contribution des microcaps et des queues ; aucun verdict sur le brut seul.
7. **Inférence** : cluster par {semaine, société} ; bootstrap par saison d'earnings ; leave-one-{saison, secteur, année}-out ; l'unité de décision est le portefeuille hebdomadaire d'événements, pas l'annonce.
8. **Portefeuille exécutable au capital réel** : long-only primaire (short = extension ultérieure sous disponibilité d'emprunt) ; actions entières ; cap de positions ; testé à 5/10/20 k€ ; horaire d'exécution réaliste (open+30 min du jour post-réaction).
9. **Périodes** : développement 2014-2019 ; confirmation intacte 2020-2026-06, sous-périodes 2020-2022 / 2023-2026 ; critères d'abandon EN développement pré-fixés avant tout calcul (δ minimal, stabilité, non-concentration) — la confirmation n'est ouverte que si le développement passe.

## Statut

Spec révisée de A transmise pour itération 2/3 via l'utilisateur. B/C concédées, non re-soumises.
