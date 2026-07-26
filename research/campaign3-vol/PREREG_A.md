# Pré-enregistrement A — Information incrémentale de l'erreur ex ante d'ampleur du marché d'options sur le drift post-earnings

**Gelé le 2026-07-26, committé AVANT tout calcul. Boucle de contradiction close (3/3, verdict GPT-5.6 : TESTABLE EN L'ÉTAT). Aucune modification après le premier run — seuls les paramètres explicitement listés « réglables en développement » peuvent être fixés en phase dev, jamais revisités ensuite.**

## Revendication (verbatim itération 1, adoptée)

À taille, signe et contexte de gap comparables, l'erreur ex ante du marché d'options sur l'ampleur du mouvement apporte-t-elle une information incrémentale sur le rendement anormal post-annonce ? — Signal post-événement à référence ex ante ; aucune conclusion comportementale sur la seule prédictibilité ; validité limitée à la population éligible définie ci-dessous.

## Univers (règle figée, construite au gate sans regarder les outcomes)

Sous-jacents du catalogue options LSE avec ≥ 8 ans d'historique, classés par nombre total de prints : **top 300**. Émetteurs domestiques US avec 8-K uniquement (6-K/foreign private issuers exclus — exclusion institutionnelle documentée). Liste snapshotée et committée au gate.

## Éligibilité d'un événement (datation EDGAR, sans fuite)

- Dépôt **8-K contenant l'item 2.02** avec `acceptanceDateTime` EDGAR ∈ (close précédent ; open+10 min].
- Marge de latence de diffusion : l'entrée n'est autorisée que si `acceptanceDateTime` + 20 min ≤ heure d'entrée (open+30). Jamais le champ `filing date`. Aucune inclusion rétroactive d'un dépôt reçu après le cut-off ; classification par les métadonnées `items` du dépôt initial (les 8-K/A sont des anomalies rapportées, jamais des réécritures d'éligibilité).
- AMC/BMO déterminés par le timestamp. La chute d'IV post-annonce = validation ex post uniquement.
- La conclusion porte sur « rendement post-earnings parmi les événements dont un 8-K item 2.02 était publiquement accessible avant le cut-off » — rien de plus large.

## Variables (figées)

- **m_e** (à close T−1, pré-annonce) : deux échéances les plus proches couvrant TOUTES DEUX l'annonce ; TV(τ) = variance totale implicite du straddle ATM (prix de straddle, model-light) ; v_base = (TV_long−TV_short)/(τ_long−τ_short) ; TV_event = TV_short − v_base×τ_short ; m_e = √TV_event. TV_event ≤ plancher → échec de mesure, événement exclu, compté au gate d'insuffisance.
- **Estimateurs de straddle** (3, pré-fixés) : VWAP winsorisé des 2 dernières heures ; médiane des prints admissibles ; dernier print admissible. Admissible = âge < 30 min, jambes C/P < 10 min d'écart, interpolation autour du spot 1 min. **Stabilité** = même quintile de u sur les 3 estimateurs, sinon exclusion ; > 20 % d'exclusions → ABANDON.
- **Gap anormal** : (open_T+1/close_T − 1) − β×gap_SPY, β = 1 primaire ; robustesse obligatoire à β̂ marché+secteur estimés sur 60 j pré-annonce.
- **Variable primaire unique** : u = log(|gap_abn| / m_e). Percentiles de u par cohorte {année × tercile de liquidité options × tercile de capitalisation}. Aucune formulation alternative dans les critères décisionnels.

## Test principal (figé)

- Population primaire : **gaps positifs uniquement** (gaps négatifs = analyse scientifique sans portefeuille).
- Appariement caliper : paires haut-u (tercile sup. de cohorte) vs bas-u (tercile inf.) avec |gap_abn| dans un caliper relatif ≤ 20 %, même {signe, cohorte, secteur, AMC/BMO, décile de RV 60 j}. Effet = différence de rendement anormal (ajusté marché) de open+30 T+1 → close T+11 (**horizon primaire : 10 jours ouvrés**, unique).
- Régression de concordance : rendement anormal ~ spline monotone(|gap_abn|) + contrôles {RV, cap, liquidités, momentum 60 j} + u ; le signe du coefficient de u doit concorder avec l'appariement.
- Table d'équilibre haut/bas u obligatoire {prix, ADV, liquidité options, momentum, vol idiosyncratique} ; déséquilibre matériel → contrôle additionnel pré-listé, pas d'improvisation.

## Portefeuille exécutable (figé)

Long-only, gaps positifs, u dans le tercile supérieur de cohorte ; équipondéré en actions entières ; max 10 positions simultanées ; entrée open+30 T+1, sortie close T+11 ; capitaux 5/10/20 k€ ; coûts = commission IBKR + spread modélisé f(prix, ADV, vol, |gap|) + slippage — formule fixée au gate sur données pré-2014 ou externes, pas sur les outcomes.

## δ (marge économique minimale — exigence externe, règle de charte)

δ = **5 × coût aller-retour modélisé** du portefeuille à 10 k€, calculé au gate et gelé avant tout outcome. Jamais choisi par inspection des effets.

## Inférence et robustesse (figées)

Cluster {semaine, société} ; bootstrap par saison d'earnings ; leave-one-{année, saison, secteur}-out ; **placebo apparié autonome** (charte campagne 2) : le portefeuille bas-u apparié, même cadence et coûts — l'avantage haut-u vs bas-u doit être ≥ δ ; concentration : top-5 semaines < 40 % du P&L ; reporting de l'échantillon effectif (événements, semaines actives, saisons, proportion d'earnings détectés, profil des exclus par cap/secteur/année).

## Périodes et gouvernance

- **Gate n°1 (avant dev)** : audit EDGAR — mapping CIK, couverture 8-K 2.02 vs earnings attendus de l'univers, distribution des délais acceptation-vs-réaction sur échantillon, faisabilité des exports LSE dans les quotas. Échec (< 60 % de couverture ou < 300 événements éligibles/an en dev) → rejet opérationnel.
- **Développement 2014-2019** : seuls réglages autorisés — fenêtres de sélection d'échéances, tolérance ATM, largeur exacte du caliper (≤ 20 %), plancher de TV_event, caps opérationnels. **Abandon en dev si** : Δ_dev apparié < δ ; ou instabilité > 20 % ; ou effet porté par un seul secteur/saison ; ou couverture insuffisante.
- **Confirmation intacte 2020-2026-06** : ouverte seulement si le dev passe ; sous-périodes 2020-2022 / 2023-2026-06 : Δ ≥ 0 dans chacune ET Δ total ≥ δ ; leave-one-block-out et déplacement des frontières ±2 semaines (règles charte).
- Données : exports LSE manifestés SHA-256 ; snapshots EDGAR submissions ; clé API jamais committée ; issues des trois conclusions possibles pré-acceptées (réfutée / pas d'edge / candidate edge prospectif).
