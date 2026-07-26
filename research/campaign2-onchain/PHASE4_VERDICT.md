# Campagne 2 — Verdict final C1 après itération 3/3 (2026-07-26)

## Verdict : **REJETÉE COMME EDGE DÉMONTRÉ — archivée en observation prospective, sans capital, sans statut privilégié**

Adjudication de l'itération 3 (boucle close, 3/3 utilisées) :

- **Critère placebo mal conçu : VALIDE, concédé.** Le critère (i) exigeait percentile < 60 ET avantage < 1 %/an — le ET l'a vidé de sa fonction. Le placebo apparié teste la seule question qui compte (la compétence d'emplacement temporel) et devait être un critère autonome. Au 36e percentile, C1 l'échoue. **Le passage formel des 9 gates n'a plus de force probante** (objection J) : c'est un faux positif procédural, documenté ici sans réécriture du run.
- **Null momentum insuffisant comme juge principal : VALIDE.** L'avantage de +13,4 %/an mélange qualité du signal, différence de turnover (2 vs 14 bascules) et placement des blocs. Le +29,4 %/an de CAGR sur-vend ~1,5 pari indépendant.
- **Sous-périodes : objection E RÉSOLUE** — la coupure 2024-07 était pré-enregistrée dans `PREREG_C1.md` avant le run (vérifiable au commit `849b06a`).
- **Failles additionnelles acceptées** : sélection en bord de grille (z_in=0,25) non ancrée ; hystérésis non testée avec 2 transitions ; leave-one-block-out absent (et vraisemblablement dévastateur — c'est précisément le manque d'information) ; « 89 stablecoins » ≈ 2-3 sources effectives (USDT+USDC = 91 %).

## Amendements à la charte (applicables à toute campagne future)

1. **Placebo apparié (blocs, exposition, coûts, longueurs) = critère d'abandon AUTONOME et benchmark principal** : < 50e percentile = rejet ; 50-70 non concluant ; > 90 début de preuve. Jamais combiné en ET avec un autre critère.
2. Sélection de paramètres en bord de grille = critère de fragilité pré-enregistré (inspection de surface obligatoire).
3. Leave-one-block-out et décalage des décisions (±1/±2/±4 semaines) obligatoires pour toute stratégie à faible nombre de bascules.
4. Le CAGR n'est jamais présenté sans : dates des blocs, rendement par bloc, contribution au P&L, sensibilité au déplacement des frontières.
5. Nombre effectif de constituants rapporté (pas le nombre nominal d'actifs).
6. Un signal dont le rythme de décision rend la validation prospective plus longue que ~5 ans doit être signalé comme potentiellement invérifiable à l'échelle d'un particulier dès la conception.

## Protocole d'observation prospective (adopté, seuils GPT)

- Durée ≥ 5 ans ET ≥ 6 cycles complets (cash→long→cash) sur ≥ 3 régimes ; aucun cycle > 40 % du P&L prospectif.
- Métrique principale : excès net vs médiane de placebos prospectifs pré-générés et gelés (seed fixée, règles committées avant observation).
- **Upgrade** (intégration expérimentale) : 6 cycles + percentile ≥ 90 + excès ≥ 3 %/an vs benchmark apparié + 2 sous-périodes positives + aucun paramètre modifié.
- **Rejet définitif** (après ≥ 4 cycles) : percentile < 50, ou excès ≤ 0, ou P&L mono-bloc, ou nécessité d'ajuster les seuils.
- Logger v2 : hash SHA-256 des données brutes, version du code (commit), prix BTC théorique d'exécution, colonne de rendement futur remplie a posteriori, erreurs de source loggées. Recommandation non implémentée automatiquement : push distant ou signature externe pour l'antériorité forte (le commit local est réécrivable).

## Conclusion de la campagne 2

Deux candidates tuées avant calcul (C2, C3), une testée et **rejetée comme edge démontré** (C1). Comme la campagne 1 : aucune stratégie déployable — mais le pipeline s'est encore durci (placebo autonome, leçon du faux positif procédural) et une expérience prospective à coût nul tourne désormais toute seule.
