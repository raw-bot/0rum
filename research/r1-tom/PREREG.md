# Pré-enregistrement R1 — Concentration turn-of-month du premium actions

**Date de gel : 2026-07-26. Ce fichier est committé AVANT tout téléchargement de données de prix et tout calcul. Le commit fait foi. Aucune modification de spec après le premier run — toute déviation invalide le test.**

Contexte : protocole `/Applications/0rum/Docs/PROMPT_EDGE_RESEARCH.md`, dossier `/Applications/0rum/Docs/EDGE_RESEARCH_2026-07.md` (itération 2 de contradiction close, conditions GPT-5.6 intégrées).

## Hypothèse

Le rendement quotidien moyen des jours turn-of-month (TOM) est supérieur à celui des autres jours, sur la période post-publication de McConnell & Xu (2008).

## Définitions figées

- **Jours TOM** : dernier jour ouvré du mois + 3 premiers jours ouvrés du mois suivant (calendrier de cotation propre à chaque instrument). 4 rendements quotidiens close-to-close par mois : ceux des jours T−1, T+1, T+2, T+3.
- **Stratégie TOM** : long du close de l'avant-dernier jour ouvré du mois au close du 3e jour ouvré du mois suivant ; cash rémunéré le reste du temps.
- **Échantillon principal** : 2009-01-01 → 2026-06-30.
- **Cluster** : mois calendaire de la date du jour de bourse.

## Instruments figés

| Rôle | Instrument | Source |
|---|---|---|
| Primaire (test scientifique) | ^SP500TR (S&P 500 Total Return) | Yahoo chart API, daily close |
| Co-primaire (tradable EUR) | EXW1.DE adjclose (iShares Core Euro Stoxx 50) | Yahoo |
| Contrôle directionnel 1 | ISF.L adjclose (FTSE 100) | Yahoo |
| Contrôle directionnel 2 | 1306.T adjclose (TOPIX) | Yahoo |
| Descriptif uniquement | ^SP500TR 1988–2008 ; ^STOXX50E ; variante T−4→T+3 ; décomposition sous-fenêtres | — |
| Cash USD | DTB3 (T-bill 3 mois) | FRED |
| Cash EUR | IR3TIB01EZM156N (interbancaire 3 mois zone euro, mensuel) | FRED |

Les éléments « descriptif uniquement » ne peuvent en aucun cas modifier le verdict.

## Statistique principale et inférence

- **Statistique principale** : différence des rendements quotidiens simples moyens, TOM − nonTOM, sur ^SP500TR, 2009-01 → 2026-06.
- **Inférence** : bootstrap par blocs de clusters mensuels (rééchantillonnage des mois avec remise, 10 000 tirages), IC bilatéral à 95 %.
- La « part du premium capturée » (ratio) est rapportée à titre descriptif seulement — jamais comme statistique de décision.

## Analyses figées

1. Test principal (ci-dessus).
2. Sous-périodes : 2009-01→2017-12 et 2018-01→2026-06 — critère de **signe** uniquement, aucune exigence de p<5 % par sous-période.
3. Concentration : retrait des 4 mois à plus forte contribution absolue au P&L de la fenêtre — le signe de la différence doit survivre.
4. Comparaison de stratégie : stratégie TOM vs buy-and-hold réduit à exposition moyenne égale (w = part moyenne de jours détenus ; rendement = w×marché + (1−w)×cash). Métriques : rendement annualisé, volatilité, Sharpe en excès du cash, max drawdown.
5. Benchmark d'exposition aléatoire : 10 000 stratégies détenant chacune un bloc contigu aléatoire de 4 jours ouvrés par mois ; rang percentile du rendement moyen en fenêtre de la stratégie TOM. Évidence de soutien si > 95e percentile (rapporté, non éliminatoire).
6. Couche de coûts, appliquée à la stratégie (le B&H réduit est traité sans coûts, hypothèse défavorable à R1) : 24 exécutions/an ; commission max(1,25 €, 0,05 % du notionnel) par exécution ; demi-spread 1,5 bp (classe S&P UCITS) et 2,5 bp (EXW1) par exécution ; calculée aux capitaux de 5 000 € et 20 000 €.
7. Contrôles directionnels ISF.L et 1306.T : signe de la différence TOM−nonTOM rapporté (généralisation faible), non éliminatoire.

## Critères d'invalidation (un seul suffit → REJET de R1)

- (i) Différence TOM−nonTOM ≤ 0 sur ^SP500TR, 2009-01→2026-06.
- (ii) Signe non conservé dans les DEUX sous-périodes sur ^SP500TR.
- (iii) Avantage net de la stratégie TOM vs B&H à exposition égale < 1 %/an au capital de 20 000 € (le résultat à 5 000 € est rapporté mais ne gouverne pas).
- (iv) Perte du signe après retrait des 4 mois extrêmes.

Dégradation (sans rejet automatique) : signe opposé sur EXW1.DE → R1 requalifié « US uniquement », périmètre réduit.

## Engagements

- Aucune deuxième fenêtre ne sera testée si le résultat déçoit.
- Les URLs, horodatages et empreintes SHA-256 des données seront consignés dans `DATA_MANIFEST.md` au moment du téléchargement.
- Le code d'analyse est committé avec les résultats.
