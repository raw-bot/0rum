# Recherche d'edges testables pour 0rum — juillet 2026

Exécution du protocole `PROMPT_EDGE_RESEARCH.md`. Statut : **itération 1 de contradiction externe adjugée — sélection révisée (2 survivants sur 5), itération 2/3 en cours**.

Contexte réglementaire vérifié (2026-07-26) :
- Les perps crypto sont accessibles au retail UE **uniquement** via des venues cumulant licence CASP MiCA + agrément MiFID II : Kraken Pro, OKX (X-Perps), Robinhood Europe. **Correction (itération 1)** : le plafond 2:1 est la mesure d'intervention ESMA sur les CFD crypto (cadre MiFID), pas un article de MiCA — les dérivés restent des instruments financiers MiFID II. Le levier exact applicable aux perps non-CFD par venue est **à vérifier officiellement (AMF/ESMA)**. Sans impact sur la sélection : aucun edge retenu ne dépend du levier.
- **Binance a cessé de servir les clients français au 2026-07-01** (retrait de la demande grecque, pas d'agrément UE). Conséquence directe : tout déploiement réel de 0rum doit viser Kraken/OKX, et les historiques de frais/exécution Binance utilisés en backtest doivent être re-stressés aux conditions Kraken/OKX.
- ETF UCITS : accès normal via CTO/PEA. ETF US bloqués (PRIIPs). Micro-futures CME et FX spot accessibles via IBKR.

---

## 1. Génération — 18 hypothèses

| # | Hypothèse | Marché / horizon | Mécanisme économique | Qui paie |
|---|-----------|------------------|----------------------|----------|
| H1 | Réversion post-cascade de liquidations | BTC/ETH perps ou spot, minutes–heures | Les moteurs de liquidation vendent sans sensibilité au prix ; overshoot puis réversion | Les traders sur-leveragés liquidés |
| H2 | Fade du funding extrême | BTC/ETH, 1–5 jours | Funding = prix de la demande de levier directionnel ; extrême = positionnement unilatéral fragile | Les shorts (ou longs) crowdés qui paient le funding |
| H3 | Cash-and-carry basis crypto (spot long + perp short) | BTC/ETH, semaines | Prime de levier structurelle | Longs leveragés |
| H4 | Turn-of-month actions | Indices via UCITS/micro-futures, ~8 jours/mois | Flux de paie et de fonds de pension, insensibles au prix, concentrés autour du changement de mois | Épargnants institutionnels contraints par le calendrier |
| H5 | Réversion post-fix WM/R 16h Londres (fin de mois) | EUR/USD, GBP/USD, USD/JPY, 1–2 h | Flux de rebalancement exécutés mécaniquement dans la fenêtre du fix ; pression puis retour | Asset managers qui exigent l'exécution au fix |
| H6 | Drift overnight actions | Indices, quotidien | Résolution d'incertitude hors séance | — |
| H7 | Front-running des reconstitutions d'indices | Actions EU, jours | Flux indiciels forcés | Fonds indiciels |
| H8 | Momentum cross-sectionnel altcoins | Alts liquides, semaines | Sous-réaction | — |
| H9 | Carry FX filtré vol | G10, mois | Prime de risque de crash | Emprunteurs en devise basse |
| H10 | Vente de volatilité indicielle | Options Eurex, mois | Prime de risque de variance | Acheteurs d'assurance |
| H11 | Trend-following lent multi-actifs | Micro-futures CME, semaines–mois | Sous-réaction initiale + flux de hedgers ; capital lent | Hedgers et retardataires |
| H12 | Spreads saisonniers gaz naturel / produits pétroliers | Futures, mois | Cycles de stockage | Consommateurs contraints |
| H13 | Événements de listing crypto | Alts, heures | Flux d'attention retail | — |
| H14 | Réversion de depeg stablecoins | Stables, heures–jours | Panique + friction d'arbitrage | Vendeurs paniqués |
| H15 | Effet session US sur BTC (post-ETF spot) | BTC, quotidien | Flux de création/rachat d'ETF concentrés en heures US | Flux ETF |
| H16 | Contrarian sur flux ETF extrêmes | UCITS, semaines | Surréaction du sentiment retail | Retail paniqué |
| H17 | Market-making passif heures creuses crypto | BTC/ETH, minutes | Carnets fins, bruit surréagi | Takers impatients |
| H18 | Effets d'expiration (witching, pinning) | Indices, jours | Hedging de gamma des dealers | — |

## 2. Boucle interne de critique

**Cycle 1 — coûts et accès.**
- H6 éliminé : deux transactions/jour, le spread+commission absorbe un edge de quelques bp/jour au niveau retail.
- H7 éliminé : compétition institutionnelle intense, besoin de borrow pour la jambe short, edge documenté en décroissance depuis 2010.
- H12 éliminé : pas de micro-contrat NG liquide adapté, marge et risque météo incompatibles avec un capital de particulier ; espace saturé par les CTA.
- H13 éliminé : non-automatisable proprement, information asymétrique, zone grise réglementaire.
- H17 éliminé : sans rebate maker significatif chez les venues accessibles (Kraken/OKX retail), l'adverse selection mange le spread capturé. C'est un métier de market-maker, pas un edge de bot retail.

**Cycle 2 — biais statistiques et expositions cachées.**
- H8 éliminé : biais de survivance massif dans les données alts (les morts disparaissent des API), liquidité alts en décroissance structurelle, coûts de rotation élevés.
- H9 éliminé : c'est du bêta de crash déguisé, crowdé depuis 20 ans ; le filtre vol est du data mining classique.
- H10 éliminé : risque de queue incompatible avec un petit compte sur marge ; un seul événement type volmageddon efface des années ; complexité opérationnelle options élevée.
- H14 éliminé : le payoff est « petit gain fréquent / perte totale possible » (risque de crédit émetteur type UST) ; sous MiCA les stables non conformes sortent des venues UE, univers instable.
- H16 éliminé : mécanisme flou (les flux ETF UCITS sont lents et peu extrêmes), données de flux fragmentées et de qualité médiocre.
- H18 éliminé : effet faible, largement arbitré, données de positionnement options coûteuses.
- H15 **dégradé puis éliminé** : mécanisme crédible (flux ETF) mais l'échantillon commence en janvier 2024 — ~2,5 ans, un seul régime. Impossible de distinguer l'effet d'un simple bull run US-hours. À revisiter avec 2 ans de données supplémentaires.
- H3 **fusionné dans H2** : le basis harvest pur est comprimé depuis l'arrivée des ETF spot et l'arbitrage institutionnel ; à 2:1 de levier max, le rendement net après frais est marginal en régime calme. En revanche le *niveau* du funding reste exploitable comme signal de positionnement → intégré à H2.

**Cycle 3 — durcissement des spécifications des survivants.**
- H1 : la donnée `forceOrder` de Binance est **tronquée depuis avril 2021** (max 1 ordre/s publié) → le détecteur doit être reconstruit sur des proxys complets : chute d'open interest + pic de volume + rendement extrême. Ceci est vérifiable avec les archives publiques Binance (OI 5 min, aggTrades) et transposable à Kraken/OKX.
- H2 : le côté short (funding extrême positif) est structurellement plus dangereux en crypto (les squeezes haussiers sont plus violents et le funding positif est chronique en bull) → spec asymétrique, long-only au départ.
- H5 : l'edge par événement est petit (5–15 bp) → ne retenir que les fins de mois (effet documenté le plus fort), pooler 3 paires pour l'échantillon, et exiger que le coût aller-retour IBKR (~0,6–1 bp + commission) soit < 20 % de l'edge brut estimé.
- H11 : contrainte de capital réelle (≥ ~20–25 k€ pour 6–8 micro-marchés diversifiés avec vol-targeting) → conservé mais classé « après collecte de capital », pas premier prototype.
- H4 : RAS majeur — l'effet est répliqué sur 19/20 indices, le mécanisme (flux calendaires) est le plus solide de toute la liste. Risque principal : taille d'effet modeste, il faut le comparer honnêtement au buy-and-hold en net.

**Arrêt après 3 cycles** : le cycle 3 n'a plus éliminé d'hypothèse, seulement durci les specs — pas de progrès substantiel attendu d'un 4e cycle interne.

## 3. Sélection provisoire (5 edges)

| Rang | Edge | Horizon | Mécanisme | Données | Accès France | Risque principal |
|------|------|---------|-----------|---------|--------------|------------------|
| 1 | **E1 — Réversion post-cascade de liquidations** (ex-H1) | minutes–heures | Vendeurs forcés insensibles au prix | Gratuites (archives Binance : aggTrades, OI 5 min, funding) | Spot partout ; perps Kraken/OKX 2:1 | Slippage réel pendant la cascade ; dépendance au régime de levier |
| 2 | **E2 — Fade du funding extrême, long-only** (ex-H2+H3) | 1–5 jours | Positionnement short crowdé payant pour tenir | Gratuites (funding historique BitMEX 2016+, Binance 2019+, OKX) | Spot suffit (le funding sert de signal, pas de carry) | Peu d'événements indépendants (~20–30 épisodes) ; signal public |
| 3 | **E3 — Turn-of-month actions** (ex-H4) | ~8 jours/mois | Flux de pension/paie insensibles au prix | Gratuites (indices quotidiens, décennies) | UCITS via CTO/PEA ou MES via IBKR | Taille d'effet modeste ; à battre : buy-and-hold net de frais |
| 4 | **E4 — Réversion post-fix WM/R fin de mois** (ex-H5) | 1–2 h, ~12–36 événements/an | Flux de rebalancement exécutés au fix | Gratuites (ticks Dukascopy/TrueFX 2003+) | FX spot IBKR | Échantillon petit ; edge/coût serré ; décroissance post-réforme 2015 possible |
| 5 | **E5 — Trend lent multi-actifs sur micro-futures** (ex-H11) | semaines–mois | Capital lent + flux de hedgers ; prime documentée sur un siècle | Gratuites/bon marché (daily futures) | IBKR, capital ≥ ~20–25 k€ | Pas original, drawdowns longs ; contrainte de capital |

Complémentarité : E1/E2 crypto court terme (cœur 0rum actuel), E3/E4 non-crypto lents (diversification, quasi-zéro corrélation avec le reste), E5 ballast long terme conditionné au capital.

## 4. Contradiction externe — itération 1 : adjudication

GPT-5.6 Thinking a rejeté les 5 edges. Adjudication objection par objection ci-dessous. Principe appliqué : ni capitulation (le consensus n'est pas une preuve), ni défense à tout prix (une conclusion négative solide vaut mieux qu'un backtest fragile).

### E4 — WM/R fix : objections ACCEPTÉES, edge ÉLIMINÉ définitivement

- Minimum IdealPro (~20–25 k USD/ordre) incompatible avec un petit capital : **valide, rédhibitoire** — en dessous, routage odd-lot à pricing dégradé ; au-dessus, une position = tout le compte sur un trade FX.
- Edge post-réforme 2015 « relativement faible » vs mon estimation 5–15 bp : **valide** — mon estimation reflétait des événements sélectionnés, pas la moyenne conditionnelle disponible à 16h05.
- Pooling de 3 paires dollar = pseudo-réplication (cluster par date obligatoire, N effectif ~12/an) : **valide**.
- Ticks Dukascopy non exécutables quand l'edge est de l'ordre de quelques pips : **valide**.
- Verdict : le pire ratio preuve/exécutabilité des cinq. Rejet propre, aucune itération supplémentaire.

### E5 — Trend micro-futures : objections ACCEPTÉES au capital annoncé, edge RETIRÉ du roadmap bot

- Indivisibilité des contrats : **valide et décisive** — à 20–25 k€ avec cible de vol ~10 %, le budget de risque du compte (~2,5 k€/an) est inférieur à la contribution d'un seul MES. Le vol-targeting est fictif à cette échelle ; l'univers 6–10 marchés sans obligations ne réplique pas la prime documentée (67 marchés chez AQR).
- Marge de stress, corrélations de crise, « zéro optimisation » en réalité une variante : **valides**.
- Révision constructive : si l'objectif est le ballast trend, l'achat passif d'un fonds/ETF UCITS managed-futures (disponibilité France à vérifier) domine l'auto-réplication retail — décision de portefeuille, hors périmètre 0rum. Rejeté comme stratégie bot.

### E2 — Funding extrême : objections MAJORITAIREMENT VALIDES, edge RÉTROGRADÉ en variable de régime

- Absence de catalyseur / causalité inversée (funding négatif = conséquence de la baisse ; acheter du funding négatif ≈ acheter après une chute) : **valide** — le test propre serait un double tri (le funding ajoute-t-il de l'information au-delà du drawdown préalable ?), mais :
- 20–30 épisodes indépendants insuffisants pour toute validation standalone : **valide et décisif**. Aucun protocole ne fabrique de la puissance statistique avec cet échantillon.
- Fragilité des shorts non observable (marge, distance de liquidation invisibles) : **partiellement valide** — l'asymétrie de squeeze n'exige pas des shorts au bord de la liquidation, mais le mécanisme est bien plus faible qu'énoncé.
- Décision : **éliminé comme edge autonome**. Le funding est conservé uniquement comme variable de conditionnement candidate dans l'étude E1 (avec comptabilité explicite du multiple testing) — pas de bot funding.

### E1 — Réversion post-liquidations : objections PARTIELLEMENT VALIDES, edge DÉGRADÉ mais CONSERVÉ

- « Le proxy n'identifie pas causalement une liquidation » : **partiellement valide**. Une règle de trading n'exige pas la pureté causale, elle exige une espérance conditionnelle positive après coûts — c'est testable. Mais la conséquence est acceptée : la thèse est **rétrogradée** de « réversion post-liquidations » à « réversion post-deleveraging extrême », et le protocole intègre un discriminant explicite : si conditionner sur la chute d'OI n'ajoute rien vs une simple réversion post-rendement extrême à vol appariée, la thèse mécanistique est morte et la stratégie rejetée.
- « Attendre la stabilisation = circulaire/look-ahead » : **partiellement valide** — la règle d'entrée (retournement du flux signé 1 min) est exécutable en temps réel sans look-ahead ; le vrai risque est dans les choix de recherche. Mitigation : spec pré-enregistrée avant tout backtest, et le null « toute chute extrême + premier rebond 1 min » comme benchmark obligatoire.
- « Backtest Binance non transférable, pas d'avantage de latence » : **partiellement réfutée**. L'argument de latence vaut pour une capture en secondes, pas pour une réversion à horizon 30 min–4 h : les venues étant couplées par arbitrage en millisecondes, on trade un niveau de prix commun, pas une course inter-venues. Partie valide conservée : frais et microstructure diffèrent → détection désormais sur la venue d'exécution (Kraken/OKX), archives Binance réservées au screening d'hypothèse.
- « La prime d'immédiateté revient aux passifs, pas au taker tardif » : **valide en théorie** — l'edge attendu est révisé à la baisse ; la cible est la décroissance résiduelle de l'overshoot (capital lent), pas la prime instantanée.
- « Coûts endogènes, stress ×2/×3 insuffisant » : **valide** — remplacé par une règle de fill conservatrice (pire prix de la bougie d'entrée + spread modélisé en fonction de la vol réalisée) et arbitrage final par 3 mois de paper trading avec journal de slippage.
- « OI 5 min trop lent + risque d'horodatage » : **valide** — OI laggé conservativement d'une fenêtre complète ; pousse vers la variante lente (confirmation tardive, détention plus longue).
- « Épisodes non indépendants » : **valide** — bootstrap par cluster d'épisode/régime.

### Correction réglementaire

L'attribution « MiCA art. 79 » du plafond 2:1 était erronée (voir note d'en-tête corrigée). Conclusion opérationnelle inchangée : venues Kraken/OKX, Binance hors de France.

## 5. Sélection révisée après itération 1

| Rang | Edge | Statut | Reformulation |
|------|------|--------|---------------|
| 1 | **R1 — Turn-of-month** (ex-E3) | Maintenu, reformulé | Plus une revendication d'alpha : revendication de **concentration du premium actions** (fenêtre TOM ≈ tout le premium avec ~35–40 % du temps d'exposition et drawdown moindre). Défense clé non traitée par GPT : ~18 ans d'out-of-sample post-publication (McConnell & Xu 2008), ~38 ans post-Lakonishok & Smidt (1988) — exactement le type de preuve anti-data-mining exigé ailleurs. Si le premium des jours TOM ≈ premium des autres jours → rejet. Coûts modélisés avec minimums IBKR (~1,25 €/ordre) ; erreur « 24 AR/an » corrigée (12 AR = 24 transactions). |
| 2 | **R2 — Réversion post-deleveraging extrême** (ex-E1) | Dégradé, conservé | Sans revendication causale « liquidations ». Discriminant intégré : l'OI doit ajouter de l'espérance vs réversion post-rendement extrême simple, sinon rejet. Détection sur venue d'exécution. Funding (ex-E2) testé comme unique variable de régime additionnelle. |
| — | E2 funding standalone | Éliminé | Échantillon insoutenable (~20–30 épisodes). |
| — | E4 WM/R fix | Éliminé définitivement | Minimum IBKR + edge/coût + N effectif. |
| — | E5 trend micros | Retiré du roadmap bot | Granularité contractuelle fatale à ce capital ; alternative passive UCITS hors périmètre. |

## 6. Contradiction externe — itération 2 : adjudication et clôture de la boucle

Verdicts GPT-5.6 : R1 et R2 **DÉFENDABLES SOUS CONDITIONS**. Objections (f) latence, (g) prime aux passifs et (h) transitoire/permanent **explicitement retirées**. Les nouvelles objections sont des conditions de protocole, toutes adjugées **valides et acceptées**, sauf précisions ci-dessous. Arrêt de la boucle à l'itération 2/3 : plus d'objection nouvelle et substantielle.

Corrections acceptées les plus importantes :
- **R1 — fenêtre** : T−4→T+3 n'était pas la spec principale de McConnell & Xu. Fenêtre primaire pré-enregistrée corrigée : **−1→+3** (dernier jour ouvré + 3 premiers, ~19 % du temps d'exposition). T−4→T+3 et la décomposition Dash-for-Cash passent en descriptif, sans re-sélection possible.
- **R1 — métriques** : la « part du premium » (ratio instable) est reléguée au descriptif ; statistique principale = différence de rendement quotidien moyen TOM vs non-TOM, erreurs clusterisées par mois global. Comparaison principale figée : stratégie vs buy-and-hold réduit à exposition moyenne égale. Drawdown comparé à une exposition aléatoire de durée identique (sinon mécanique). Cash rémunéré au taux court réel. Coûts au capital réel (rapportés à 5 k€ et 20 k€ : les minimums IBKR pèsent ~0,60 %/an à 5 k€, ~0,15 % à 20 k€).
- **R2 — porte d'entrée** : l'audit d'intégrité de l'OI Kraken/OKX (unités, méthode et fréquence de publication, ruptures, cohérence API historique/temps réel, non-révision rétroactive) devient le **gate n°1** — s'il échoue, R2 est non testable et rejetée opérationnellement.
- **R2 — protocole confirmatoire** : Binance = développement déclaré (le screening a un coût de sélection, assumé) ; période Kraken/OKX intacte = confirmation ; aucun retour aux paramètres. Horizon primaire unique : **2 h**. Marge incrémentale minimale pré-enregistrée pour la contribution OI (δ), pas un simple signe positif. Appariement des événements sur magnitude du choc, vol, volume, régime. Cluster BTC+ETH par épisode global, règle de cooldown fixée ex ante. Paper trading 3 mois = **audit opérationnel** (plomberie, slippage réel vs modèle), pas validation statistique — validation seulement après un nombre minimal pré-enregistré d'épisodes. Stabilité évaluée par leave-one-cluster-out, concentration du P&L sur les 5 meilleurs événements et coupure temporelle, pas par une exigence brutale « positif chaque année ».

---

# LIVRABLE FINAL

## Edge finaliste n°1 — R1 : Concentration turn-of-month du premium actions

### Thèse
Le premium actions n'est pas uniformément distribué dans le mois : il se concentre autour du changement de mois. Payeurs de la prime : les épargnants et institutions dont les flux (paie, contributions pension, rebalancements à date de reporting) sont exécutés mécaniquement au calendrier, sans sensibilité au prix. Ils continuent de payer parce que le calendrier de ces flux est une contrainte institutionnelle, pas un choix d'investissement — la publication de l'anomalie ne supprime pas les flux. Persistance possible : capter l'effet exige de porter du bêta actions concentré dans le temps, capacité trop faible pour les institutionnels — niche retail par construction. Revendication explicitement limitée : **timing de risque, pas alpha market-neutral**.

### Spécification testable
- Univers : S&P 500 TR (primaire), Euro Stoxx 50 TR (co-primaire) ; FTSE 100 et TOPIX en contrôles directionnels.
- Signal/entrée/sortie : long du close de l'avant-dernier jour ouvré du mois (pour capter le rendement du dernier jour) au close du 3e jour ouvré du mois suivant ; cash rémunéré sinon. Aucun paramètre libre.
- Horizon : 4 jours ouvrés par mois, 12 événements/an. Sens : long-only.
- Invalidation (pré-enregistrée) : (i) différence de rendement quotidien moyen TOM−nonTOM ≤ 0 sur 2009–2026 (test principal post-publication, erreurs clusterisées par mois) ; (ii) signe non conservé sur 2009–2017 ET 2018–2026 ; (iii) avantage net vs buy-and-hold à exposition moyenne égale < 1 %/an au capital réel après tous frottements ; (iv) effet porté par ≤ 4 mois extrêmes (leave-one-out).

### Données
Indices total return quotidiens (Stooq/issuers, gratuit, 30+ ans) ; taux courts €STR/T-bill (BCE/FRED, gratuit) ; grille tarifaire IBKR. Limites : tracking difference de l'ETF réel vs indice TR, heures de cotation UCITS vs sous-jacent US — modélisées dans la couche coûts.

### Validation
Pré-enregistrement de la spec dans git AVANT tout backtest ; test principal 2009–2026 (~210 événements) ; le backtest centenaire est du contexte, pas de la preuve ; sous-périodes ; bootstrap d'expositions aléatoires de durée identique (benchmark drawdown/Sharpe) ; stress coûts à 5 k€ et 20 k€ ; pas d'exigence de significativité à 5 % par sous-période, mais rejet si économiquement négligeable même avec p < 0,05.

### Faisabilité française
ETF UCITS S&P 500/Euro Stoxx via CTO (ou PEA pour l'éligible) — aucun obstacle PRIIPs ; MES via IBKR en alternative à plus gros capital. Fiscalité CTO des rotations mensuelles : réduit l'intérêt patrimonial net, à chiffrer — règles PFU/prélèvements sociaux de l'année fiscale **à vérifier officiellement**.

### Verdict
Mécanisme 7/10 · Facilité de test 9/10 · Coût de mise en œuvre 9/10 · Concurrence 6/10 · Robustesse potentielle 7/10 · Potentiel net après coûts 5/10 (dépend du capital ; faible à 5 k€, honnête à 20 k€+).
**Décision : TESTER IMMÉDIATEMENT.**

## Edge finaliste n°2 — R2 : Réversion post-deleveraging extrême (BTC/ETH)

### Thèse
Après un épisode conjoint {chute extrême, volume extrême, contraction d'OI}, les vendeurs sous contrainte (mélange non identifié de liquidations forcées, réductions de risque, retraits de liquidité) paient une prime d'immédiateté ; le capital d'absorption arrive lentement ; l'overshoot décroît sur des heures. Payeurs : les leveragés contraints de sortir. Persistance possible : fournir cette liquidité exige de porter la queue gauche des vrais crashs — la prime est une compensation de risque, pas une inefficience gratuite. Toute l'originalité repose sur la **valeur incrémentale du conditionnement OI** vs réversion générique — c'est fragile et assumé.

### Spécification testable
- Univers : BTC-PERP et ETH-PERP (ou spot côté long) sur la venue d'exécution.
- Signal : rendement < seuil sur fenêtre N + volume > k× médiane + ΔOI < quantile q, OI laggé d'une fenêtre de publication complète ; entrée au close de la première minute positive suivante (aucune confirmation ultérieure) ; sortie à l'horizon primaire 2 h ; cooldown d'épisode fixé ex ante.
- Sens : long après chocs baissiers d'abord ; short non symétrique, éventuel, jamais présumé.
- Invalidation (pré-enregistrée) : (i) espérance nette ≤ 0 après coûts venue d'exécution ; (ii) le benchmark sans condition OI (chute extrême + premier rebond, apparié sur magnitude/vol/volume/régime) capture la même espérance ; (iii) contribution OI < δ (marge économique minimale pré-enregistrée) ; (iv) contribution concentrée sur 2020–2021 ou ≤ 5 épisodes (leave-one-cluster-out) ; (v) échec de l'audit d'intégrité OI → non testable, rejet opérationnel.

### Données
Développement (déclaré comme entraînement) : archives Binance Vision (aggTrades, OI 5 min, funding), gratuit, 2020+. Confirmation : historiques Kraken Futures/OKX (API), profondeur et intégrité à auditer — **gate n°1**. Funding multi-venues comme unique variable de régime additionnelle (multiple testing comptabilisé). Limites connues : OI publié parfois incohérent entre venues (unités, retards, révisions) — c'est précisément l'objet de l'audit.

### Validation
Séparation stricte développement/confirmation sans retour aux paramètres ; appariement strict des événements ; cluster BTC+ETH par épisode global ; bootstrap par cluster ; fills au pire prix de la bougie d'entrée + spread croissant avec la vol réalisée ; concentration du P&L mesurée ; 3 mois de paper trading = audit opérationnel (latence, disponibilité OI, slippage réel vs modèle), validation statistique seulement après le nombre minimal d'épisodes pré-enregistré.

### Faisabilité française
Kraken Pro / OKX X-Perps (CASP MiCA + MiFID II) pour les perps ; côté long réalisable en spot sur toute venue MiCA. Levier inutile à la stratégie. Cadre exact du levier perps retail **à vérifier (AMF/ESMA)**.

### Verdict
Mécanisme 6/10 · Facilité de test 5/10 · Coût de mise en œuvre 6/10 · Concurrence 5/10 · Robustesse potentielle 4/10 · Potentiel net après coûts 4/10.
**Décision : TESTER APRÈS COLLECTE DE DONNÉES** (gate : audit OI Kraken/OKX).

## Conclusion opérationnelle

1. **Edges prioritaires** : R1 (turn-of-month), R2 (post-deleveraging). Il n'y a pas de 3e edge — forcer un troisième serait exactement le biais que le protocole combat. Candidat en veille : effet session US/flux ETF sur BTC (ex-H15), à réévaluer vers 2028 avec 2 ans de données supplémentaires.
2. **Ordre de test** : R1 d'abord (données triviales, spec sans paramètre libre, verdict en ~2 semaines), R2 ensuite (gate audit OI).
3. **Données à obtenir** : indices TR + taux courts (immédiat, gratuit) ; audit API OI/trades Kraken Futures et OKX ; archives Binance Vision pour le développement R2.
4. **Premier prototype 0rum** : R1 dans le moteur de backtest walk-forward (Docker :8008, `~/Documents/00-code/0rum` — pas dans l'atelier TV ni dans le bot live). Règle calendaire sur barres quotidiennes : trivial à implémenter, la difficulté est dans la couche coûts/cash, pas dans le signal.
5. **Go/no-go** : critères d'invalidation pré-enregistrés ci-dessus, committés dans git avant le premier run — le commit fait foi.
6. **Plan 30 jours** : S1 — pré-enregistrement R1 + collecte + test principal 2009–2026. S2 — robustesse R1 (sous-périodes, bootstrap d'exposition aléatoire, couche coûts 5 k€/20 k€, fiscalité) → verdict R1. S3 — audit OI Kraken/OKX (gate R2) + screening développement Binance déclaré. S4 — pré-enregistrement du protocole confirmatoire R2 + prototype R1 dans 0rum si GO.
7. **Rejets définitifs** : E4 (fix WM/R — minimum IBKR, edge/coût, N), E5 (auto-réplication trend à ce capital — granularité contractuelle), E2 (funding standalone — échantillon), et les 13 hypothèses des cycles internes (overnight, front-running indiciel, momentum alts, carry FX, vente de vol, saisonnalités NG, listings, depegs, flux ETF contrarian, MM passif, witching, session US pour l'instant, basis pur).

Rappel final du protocole : « défendable sous conditions » signifie **digne d'un test propre**, pas edge établi. Si R1 échoue son test principal post-2008, la conclusion de la mission devient : aucun edge suffisamment crédible identifié dans cette itération — et ce serait un résultat valide.

## 7. Verdict empirique R1 (2026-07-26) : REJET

Test exécuté selon le pré-enregistrement (`~/Documents/00-code/0rum/research/r1-tom/`, commits `1b7cb46` spec puis `e8e120e` résultats — la spec a été committée avant toute donnée).

**L'effet turn-of-month est mort après sa publication.** Sur ^SP500TR 2009–2026 (210 mois d'out-of-sample post-McConnell & Xu) : différence TOM−nonTOM de +0,95 bp/jour, IC95 [−6,89 ; +8,68], contre +8,01 bp/jour sur 1988–2008. La fenêtre TOM capture 21,5 % du rendement pour 19,1 % des jours — aucune concentration ; 61e percentile face à des blocs aléatoires de 4 jours. Critère (iii) déclenché : avantage net à 20 k€ = −1,42 %/an (brut : +0,14 %/an seulement, avant même les coûts de 1,56 %/an). Même constat sur EXW1.DE (brut négatif). Conformément aux engagements : pas de deuxième fenêtre, pas de re-spécification.

Décision de mission mise à jour : R1 rejoint les rejets définitifs. **Le seul candidat restant est R2** (gate : audit d'intégrité OI Kraken/OKX). Si R2 échoue, conclusion officielle de la mission : aucun edge suffisamment crédible identifié dans cette itération — données et méthodes à faire évoluer avant toute campagne 2 (cf. contraintes durcies, section 6 de la discussion).

## 8. Verdict empirique R2 (2026-07-26) : ABANDON EN DÉVELOPPEMENT — et conclusion de mission

Chronologie (tout dans `~/Documents/00-code/0rum/research/r2-deleveraging/`, commits `4adb2bf` audit, `28f0b04` plan gelé, `e8c243e` résultats) :

1. **Gate d'audit OI : passé** — mais pas comme prévu : OKX a échoué (OI 5 min limité à ~30 jours), c'est Kraken qui a fourni un historique OI 5 min impeccable depuis mars 2023 (cadence parfaite, OHLC cohérent, re-téléchargement stable). Design : développement sur Binance 2020-09→2023-12, confirmation Kraken 2024→2026 intacte.
2. **Plan de développement gelé avant analyse** : grilles fermées (270 cellules), benchmark apparié sans-OI obligatoire, 5 critères d'abandon fixés à l'aveugle.
3. **Verdict : ABANDON** (critères 2 et 5). La meilleure cellule sur 270 — sélection maximale assumée — a une espérance nette à 2 h **négative** (−2,9 bp), et la contribution incrémentale de l'OI s'inverse entre 2020-21 (+153 bp) et 2022-23 (−109 bp). Sur l'ensemble de la grille, le pattern de base « premier rebond après chute extrême » perd de −20 à −70 bp net par épisode. Les données Kraken de confirmation n'ont jamais été ouvertes.

### Conclusion officielle de la mission

**Aucun edge suffisamment crédible n'a été identifié dans cette itération.** Les 18 hypothèses générées ont toutes été éliminées : 13 en critique interne, 3 en contradiction externe (E2/E4/E5), et les 2 finalistes par réfutation empirique pré-enregistrée (R1 : effet mort post-publication ; R2 : espérance négative après coûts et incrément OI instable). Les deux prédictions hostiles clés de GPT-5.6 (décroissance post-publication pour R1 ; prime captée avant l'entrée taker + fragilité de l'incrément OI pour R2) sont confirmées par les données.

Conformément au prompt de mission : cette conclusion négative et documentée vaut mieux qu'une stratégie séduisante sur backtest fragile. Elle a coûté une session au lieu de mois de développement d'un bot sur du bruit.

### Acquis réutilisables pour une campagne 2

- **Infrastructure** : archives Binance 2020-2023 (metrics OI 5 min, klines 1 min, funding), collecte Kraken 2024-2026 vierge (utilisable comme holdout d'une future hypothèse), scripts de pré-enregistrement/screening, pipeline de test en ~7 min.
- **Contraintes de génération durcies** (leçons payées) : ≥ 20-30 événements indépendants/an ; données auditées avant de raisonner ; edge brut estimé ≥ 5× le coût aller-retour au capital réel ; jamais de thèse dont l'originalité repose entièrement sur une variable incrémentale non auditée ; méfiance par défaut envers toute anomalie publiée depuis > 10 ans.
- **Territoire non exploré** identifié : données on-chain crypto (flux stablecoins, comportement des holders) — seul espace neuf noté pour la campagne 2, à instruire avec les contraintes ci-dessus.

## 9. Campagne 2 (on-chain) — exécutée et close (2026-07-26)

Dossier complet : `~/Documents/00-code/0rum/research/campaign2-onchain/` (charte `447b505` → verdict final `8b2fb92`). Déroulé : audit de données AVANT génération (inversion de processus) → 7 hypothèses → 3 candidates → 3 itérations de contradiction GPT-5.6 → 1 test empirique pré-enregistré.

- **C3** (activité réseau relative BTC/ETH) : rejetée — métriques incomparables entre chaînes, rupture L2, paire unique.
- **C2** (afflux exchanges) : tuée avant backtest — labels Coin Metrics non point-in-time (leçon d'audit : la rétroactivité des *labels* est un risque distinct de la révision des *valeurs*).
- **C1** (croissance de l'offre stablecoin fiat-backed) : testée sur pré-enregistrement. Les 9 gates formels sont passés (+13,4 %/an vs null momentum en confirmation 2022-2026) mais le verdict final est **REJETÉE COMME EDGE DÉMONTRÉ** : le critère placebo était mal conçu (ET au lieu de OU) et le placebo apparié — seul test de la compétence de timing — place la stratégie au 36e percentile ; 2 bascules en 4 ans ≈ 1,5 pari indépendant ; dev t=0,35. **Faux positif procédural documenté** sans réécriture du run ; charte durcie en conséquence (placebo autonome, fragilité de bord de grille, leave-one-block-out, règles de présentation du CAGR).
- **Observation prospective en cours** : LaunchAgent `com.0rum.c1logger` (lundis 08h05) logge la décision hebdomadaire du signal avec hash de données, version du code et prix d'exécution théorique, auto-committé. Seuils d'upgrade/rejet définitifs pré-fixés (≥ 6 cycles complets, percentile placebo prospectif ≥ 90, excès ≥ 3 %/an) — horizon réaliste : 5-10 ans. Baseline 2026-07-26 : z=−1,24, cash.

### Bilan des deux campagnes

25 hypothèses instruites, 0 edge déployable — deux conclusions négatives propres, un pipeline de réfutation réutilisable (pré-enregistrement git, placebo apparié, séparation venue/temps, boucle de contradiction cross-AI), et une leçon structurelle : à l'échelle d'un particulier avec données gratuites, les signaux assez lents pour être accessibles sont souvent trop lents pour être vérifiables, et les signaux assez rapides pour être vérifiables sont mangés par les coûts. Toute campagne 3 devra viser des mécanismes à ≥ 20-30 décisions indépendantes/an ET coûts < 1/5 de l'edge brut — ou accepter l'attente prospective.

## Sources consultées

- MiCA / accès dérivés retail UE : [coinperps — Best Crypto Perpetual Futures Exchanges in Europe (2026)](https://www.coinperps.com/learn/best-crypto-futures-platforms-in-europe), [Finance Magnates — Europe's Crypto Market After July 1](https://www.financemagnates.com/cryptocurrency/regulation/europes-crypto-market-after-july-1-who-stays-who-leaves-and-what-changes-under-mica/), [bleap — Is Binance MiCA Licensed?](https://www.bleap.finance/en-us/blog/is-binance-mica-licensed)
- Fix WM/R : [Evans et al., NBER w23327 — Did the reform fix the London fix problem?](https://www.nber.org/system/files/working_papers/w23327/w23327.pdf), [FCA Occasional Paper 46 — Fixing the Fix?](https://www.fca.org.uk/publication/occasional-papers/occasional-paper-46.pdf), [ScienceDirect — Did the reform fix the London fix problem?](https://www.sciencedirect.com/science/article/abs/pii/S0261560617302024)
- Funding rates : [BitMEX — 9 Years of XBTUSD Funding Rate Analysis](https://www.bitmex.com/blog/2025q2-derivatives-report), [The Block — K33 Research sur les funding rates négatifs](https://www.theblock.co/post/314382/bitcoin-perpetual-futures-funding-rates-pessimism), [CoinDesk — funding le plus négatif depuis 2023 (avril 2026)](https://www.coindesk.com/markets/2026/04/16/bitcoin-funding-rates-hit-most-negative-since-2023-history-suggests-bottom-is-in)
- Turn-of-month : [Quantpedia — Turn of the Month in Equity Indexes](https://quantpedia.com/strategies/turn-of-the-month-in-equity-indexes), [McConnell & Xu — Equity Returns at the Turn of the Month](https://www.chesler.us/resources/academia/turn_of_the_month_stock_returns.pdf)
