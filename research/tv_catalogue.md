# Catalogue indicateurs TradingView — soumissions de cube

_Analyse une par une des transcriptions vidéo (~/Documents/00-code/VideosTranscriptions/)._
_Grille 5Q : (1) repaint ? (2) backtest indépendant ? (3) survit aux frais ? (4) info nouvelle
vs signaux du bot ? (5) complexité justifiée ?_
_Verdicts : CERVEAU / FILTRE / EXIT candidat, IDÉE (hypothèse extraite), DOUBLON, POUBELLE._

| # | Source | Indicateur / concept | Asset vidéo | 5Q (résumé) | Verdict | Hypothèse extraite |
|---|---|---|---|---|---|---|
| 1 | @nolan.vader2 (76s) | Oscillateur de valorisation or vs dollar (histogramme z-score probable, bandes d'extrême) + confluence zones offre/demande manuelles | XAU/USD | 1:? formule cachée · 2:non · 3:intestable · 4:**oui** (intermarché) · 5:reconstructible en ~20 lignes | IDÉE (filtre, a priori faible) | z-score XAU/DXY sur N périodes : les extrêmes ±2σ prédisent-ils la réversion de l'or ? À tester en **filtre** (pas signal autonome). Version automatisable de la méthode : pivots Livermore (zones) + oscillateur d'étirement (contexte) + règle de patience |
| 2 | @nolan.vader1b (26s) | POC du volume profile de la veille = niveau de décision intraday (rejet OU cassure-retest), biais directionnel exogène | non déterminé (intraday) | 1:**non** (POC de la veille figé) · 2:partiel (héritage Market Profile, peu de quant) · 3:dépend de l'implémentation (rien de chiffré) · 4:**oui** (volume-par-prix, orthogonal à nos indicateurs temporels) · 5:oui (POC = calcul simple) | IDÉE (couche de niveaux objective) | H1 : le POC de la veille produit-il une réaction mesurable vs niveaux placebo ET vs niveaux plus simples (PDH/PDL, close veille, VWAP) ? Si oui → couche "niveaux" pour affiner entrées/sorties. Telle que vendue : non falsifiable (niveau bidirectionnel + biais fourni par la communauté) |
| 3 | @nolan.vader (66s) | Oscillateur COT : Commercials (cyan) vs Retail (rouge), bandes d'extrême 0-100 (COT-index %-rank probable) — valide/invalide les zones supply-demand | non déterminé | 1:⚠️ piège du lag de publication (voir détails) · 2:**oui — littérature académique réelle** (mitigée) · 3:oui (biais hebdo, quasi 0 coût de trade) · 4:**oui++** (seule donnée exogène de toutes les soumissions) · 5:oui (COT index = %-rank simple, données CFTC gratuites depuis 1986) | **FILTRE/BIAIS candidat — meilleure soumission** | Sur l'or (GC) : Commercials à l'extrême bas du COT-index + Retail à l'opposé → le prix baisse-t-il sur 1-4 semaines ? Utilisable en porte de biais hebdo (gate les longs/shorts du bot). BTC : COT des futures CME dispo depuis 2018 (catégories TFF différentes) |
| 4 | @nolan.vader 2 (48s) | Correlation Coefficient intégré TV (XAUUSD↔DXY) < 0 comme pré-filtre + confluence miroir zones (demande Or + offre DXY) | XAU vs DXY | 1:non (CC rétrospectif) · 2:corrélation = fait descriptif ; comme filtre : aucun backtest mais trivialement testable · 3:oui (filtre) · 4:partiel — régime oui, confluence miroir = double comptage · 5:oui (built-in, 1 ligne à répliquer) | FILTRE de régime candidat (partie confluence à jeter) | Gater les trades Or sur corr(rendements XAU,DXY ; N jours) < seuil améliore-t-il les stats ? ⚠️ tester sur RENDEMENTS, pas sur prix (le built-in TV calcule sur les prix → corrélations fallacieuses sur séries non stationnaires) |
| 5 | @nolan.vader 3 (60s) | Même mécanique que n°4 transposée NASDAQ↔ZB1! (T-Bond 30Y) : CC < 0 + résistance NQ / support bonds → short NQ | NASDAQ vs ZB1! | Identique n°4 (mêmes forces, mêmes failles) | DOUBLON mécanique de n°4 — mais confirme le framework généralisé | Le SIGNE de corr(actions, bonds) est lui-même un détecteur de régime macro (négatif = régime risk-on/off, positif = régime inflation, cf. bascule 2022). Pour nous : corr(BTC,NQ) et corr(NQ,ZB) comme features de régime du bot — BTC est un actif corrélé NASDAQ depuis 2020 |
| 6 | @nolan.vader 4 (51s) | Fixed Range Volume Profile (outil intégré TV) sur un range 30m choisi à l'œil → POC + VAH/VAL projetés → rejet dans le sens du "daily bias" | non déterminé (TF 30m) | Identique n°2, en pire : le range est choisi subjectivement ("any range you see") vs la veille (objective) | DOUBLON de n°2, variante affaiblie | Seul ajout : tester VAH/VAL en plus du POC dans le protocole placebo de n°2. Confirme que la couche exécution du compte = niveaux volume profile + biais externe (jamais montré) |
| 7 | @aabandzfx (94s) | Anchored VWAP bandes ±2σ/±3σ (outil intégré, réglages montrés) : zone 2σ-3σ = extrémité → sweep Asian high/low + bougie de confirmation 5m → entrée, stop à l'extrême de la bougie, cible VWAP/extrémité opposée, RR 1:2-1:3 | or probable, TF 5m | 1:⚠️ ancre manuelle = fuite de hindsight (le reste ne repaint pas) · 2:VWAP institutionnel réel ; combo : aucune preuve ; famille MR intraday = D chez Oxfordstrat · 3:douteux (5m, stops serrés) mais cibles 2-3R · 4:oui — structure de SESSION (Asian range) + VWAP ancrée, absentes du catalogue · 5:oui, tout est calculable | **STRATÉGIE backtestable** (1ère soumission complète) — a priori défavorable, test bon marché | (a) Prix en [2σ,3σ] de la VWAP ancrée session + sweep Asian H/L → réversion vers VWAP sous X barres ? (coûts inclus, BTC + or) ; (b) indépendamment : l'Asian range comme feature de régime intraday pour le bot. "90% win rate" à 1:2 RR = +1.7R/trade d'espérance = mathématiquement absurde, pur marketing |
| 8 | @casper_smc (114s) | "First Candle Rule" : ORB sur la 1re bougie M5 de 9:30 EST → cassure du H/L exigeant un FVG (displacement) → retest du FVG + engulfing en M1 → entrée, TP 3:1 RR fixe, 1 trade/jour | indice US probable (schémas purs) | 1:non (tout objectif une fois défini) · 2:mixte — ORB = D chez Oxfordstrat MAIS Zarattini/Aziz 2023 (ORB 5m QQQ) = résultats forts débattus ; engulfing seul = C/D ; FVG-magnet = zéro preuve · 3:plausible (1 trade/jour, session US) · 4:oui — FVG utilisé en CONTINUATION (filtre de displacement, aligné volatility clustering !) + ancre US open · 5:oui, 3 briques codables | **STRATÉGIE backtestable — a priori moyen, le meilleur des soumissions intraday** | (a) ORB 1re bougie M5 + exigence de displacement (FVG) vs ORB simple : le filtre améliore-t-il ? (NQ/or, coûts inclus) ; (b) l'ancre 9:30 EST testée sur BTC (effets de session US documentés en crypto) ; (c) entrée sur retest vs entrée immédiate. ⚠️ stop non chiffré, fenêtre de validité non précisée → inputs |
| 9 | @casper_smc 2 (50s) | Glossaire : break & retest, swing points (fractal 3 bougies), market structure (HH/HL vs LH/LL), FVG (version aimant), volume profile (POC/VAH/VAL) | aucun (schémas) | Sans objet — définitions, pas de système | GLOSSAIRE — rien à tester | Aucune hypothèse nouvelle : tout est déjà au catalogue sous d'autres noms (swings = pivots Livermore ; structure = Dow Theory, C chez Oxfordstrat ; FVG et POC déjà traités). Note : le même créateur enseigne ici le FVG version "aimant" (zéro preuve) alors que sa vidéo n°8 l'utilise en displacement/continuation — les deux récits coexistent sans que l'auteur voie la contradiction |
| 10 | @casper_smc 3 (104s) | First Candle Rule v2 — même stratégie que n°8 au détail près (hook différent, pas de CTA) | idem n°8 | idem n°8 | DOUBLON exact de n°8 | Rien de neuf. Seule utilité : confirme la fidélité de l'extraction de n°8 (mêmes règles, mêmes chiffres dans les deux tournages) |
| 11 | @horiizon.fx (165s, FR) | Playbook ICT complet : sweep liquidité Asian + FVG 1H + zone OTE Fibonacci (0.618-0.79) + iFVG/V-shape en 3min → reversal, RR ~1:2 | EURUSD | 1:**oui, massivement** — suppression de zones en direct ("je l'enlève"), re-ancrage roulant ("dernier FVG 3min"), V-shape subjectif = 5 degrés de liberté discrétionnaires · 2:zéro (ICT/Fib : aucune étude ne montre que les ratios Fib battent des niveaux quelconques) · 3:intestable en l'état · 4:quasi nul — chaque brique est déjà au catalogue (sessions=n°7, FVG=n°8, multi-TF=nolan.vader) ; seuls objets nouveaux : OTE et iFVG · 5:non — empilement de confluence = machine à non-falsifiabilité | POUBELLE argumentée (méthode) ; extraction mineure | Squelette sous le jargon : sweep session + pullback profond (62-79%) + bascule LTF = contre-tendance intraday (famille Turtle Soup, D avec frais), déjà couverte par n°7. iFVG = définissable objectivement (FVG clôturé au travers → bascule de polarité), testable un jour comme déclencheur LTF vs engulfing — priorité basse |
| 12 | @horiizon.fx 2 (143s, FR) | CBDR (range 14:00-20:00 NY) + projections "standard deviations" ±1..4 (indicateur nommé : ICT Session Killzone Boxes & Deviations, Shanksia, réglages capturés) + confluence liquidité (low London) + bougie 15m à l'open NY → reversal RR 1:2 | forex (paire non identifiée) | 1:niveaux figés à la clôture du CBDR ✓, mais 3 choix subjectifs (quel STD, "liquidité proche", "belle bougie") · 2:zéro pour CBDR/STD ; ⚠️ costume statistique FAUX (multiples de range ≠ σ, la gaussienne 68/95/99.7% est du déguisement) · 3:marginal (1/jour, RR 1:2, spreads forex) · 4:une brique : projection de multiples de range d'une fenêtre de session (famille ADR/measured move) · 5:l'indicateur oui (trace des niveaux), la méthode hérite de la discrétion habituelle | IDÉE faible — 1 niveau de plus pour le protocole placebo | Les multiples du range CBDR marchent-ils mieux que des niveaux aléatoires comme S/R intraday ? → à ajouter au batch de test niveaux (POC, VAH/VAL, PDH/PDL, VWAP, STD-CBDR). Indicateur nommé + réglages capturés = extractible via MCP TradingView pour comparaison directe |
| 13 | @erickjablonski (42s) | ORB classique 30 min : range des 2 premières bougies 15m → clôture 15m hors range = entrée, stop = extrémité opposée, **TP fixe 10 points** (profil inversé : petits gains, grosses pertes) — "variation Tom Hougaard", codée en bot, claim 81% (94/116) | TSLA + ES futures | 1:non — 100% mécanique, zéro discrétion (la mieux spécifiée de toute la pile) · 2:ORB mixte (D Oxfordstrat daily / Zarattini+ sur QQQ 5m) ; le profil TP-mini/stop-large : aucune preuve, et leçon RSI-2 (91% win, gains ≈ 0) s'applique · 3:LA question — TP 10 pts vs commissions+slippage sur micros, et une perte pleine efface 3-6 gains · 4:profil de risque à skew négatif = inédit dans la pile ; seul créateur qui code et botte lui-même · 5:minimale (≈30 lignes Python) | **STRATÉGIE backtestable — test le moins cher de toute la pile** (une demi-journée) | A/B parfait avec n°8 : même couche niveau (opening range), philosophies de sortie opposées → tester ORB+TP-fixe-mini vs ORB+displacement+3:1 sur mêmes données (ES, BTC ancré open US et open UTC). ⚠️ 81% sur 116 trades = IC ±7%, échantillon faible ; ancrage des "2 premières bougies" non précisé → input |
| 14 | @hamiltonrigby (26s) | POC d'une jambe swing low→high (FRVP intégré) comme niveau d'exécution + biais HTF externe | non déterminé | Identique n°2/n°6 — 3e variante d'ancrage du POC (veille / range manuel / jambe de swing) | DOUBLON de n°2 — ⚠️ révèle un écosystème de clones | Script quasi mot-à-mot identique à nolan.vader1b ("X, volume profile across it, find the POC… simple, right?"), persona calqué ("London Exchange Veteran" vs "Retired floor manager"), même CTA. → La convergence inter-créateurs observée jusqu'ici est en partie une CHAÎNE DE COPIE marketing, pas une validation indépendante. Seul ajout testable : l'ancrage swing-leg dans le batch niveaux (objectivable via fractals N) |
| 15 | @liquidity_poll (18s) | Playbook SMC télégraphique : low sweep → CHoCH bas TF → entrée au "rejection block" (+ breaker block, inducement) → cible swing high, RR 7.54 affiché | non déterminé | Sous-ensemble de n°11 : sweep + bascule LTF + reversal, en 18s, zéro définition, 4 objets de jargon non définis, 1 exemple gagnant | POUBELLE — rien d'extractible qui ne soit déjà au catalogue | CHoCH = cassure de structure LTF (≈ notre flip de momentum) ; rejection/breaker blocks = variantes de vocabulaire zones sans preuve (famille order blocks). Aucune hypothèse nouvelle. ⚠️ Leçon d'hygiène : le RR 7.54 vient d'un stop à 0,047% du prix — plus fin que spread+slippage réels → RR d'affichage, inexécutable. Toujours demander d'où vient un RR |
| 16 | @bennnytrades (101s) | "TradeX QuantORB" : ORB 9:30-9:45 ET sur MNQ 15m + filtres de régime ATR & volume relatif (seuils cachés, "optimisés"), Pine strategy auto, deep backtest +271% 2020-2026 (2177 trades, 51.4%, DD 15%) | MNQ 15m | 1:non (Pine sur clôtures) · 2:ORB+RVOL = LA recette Zarattini/Aziz — configuration académiquement testée · 3:2177 trades sur micros, frais non montrés · 4:**oui — la couche contexte qui manquait aux ORB n°8/13** (filtres ATR/RVOL) · 5:oui · ⚠️ **RED FLAG : optimisation de paramètres IN-SAMPLE puis courbe montrée sur le même historique** — le +271% "sans variance" est la signature d'un overfit, pas d'un edge | **STRATÉGIE backtestable (framework, pas les claims)** | Complète le batch ORB en étude d'ablation propre : ORB × {sans filtre, displacement/FVG (n°8), ATR seuil, RVOL seuil} × {TP-mini (n°13), 3:1, trailing} sur MNQ + BTC + or, avec walk-forward — jamais d'optimisation in-sample. Stats de forme crédible (51.4%, RR>1) mais tous les seuils cachés derrière le funnel "comment orb" |
| 17 | @trademachineoff (62s) | SMC reversal éducatif : anti-FOMO (ne pas chasser l'impulsion) → CHoCH (cassure du dernier LH) → pullback dans le dernier order block (dernière bougie baissière avant l'impulsion) → long, cible = liquidité (swing high) | non déterminé (schémas) | 1:discrétionnaire mais objectivable (CHoCH = fractals + cassure ; OB = "dernière bougie opposée avant impulsion ≥ k×ATR") · 2:zéro · 3:intestable (rien de chiffré) · 4:non — mais MIROIR de notre bot : impulsion → cassure de structure → pullback → entrée = notre séquence AK MACD en vocabulaire SMC · 5:n/a | DOUBLON conceptuel (famille n°11/15) — intérêt : valide notre architecture par convergence | Confirme l'axe de design "entrée sur pullback vs chasse du breakout" (déjà dans le batch ORB via n°8). Extraction mineure : définition codable de l'order block ("dernière bougie opposée avant impulsion ≥ k×ATR") = un type de zone de plus pour le batch niveaux, priorité basse |

## Observation transversale (après 12 entrées)

**Obsession des sessions chez tous les créateurs intraday** : Asian range (n°7, 11),
open US 9:30 (n°8), low de London + open NY + CBDR 14:00-20:00 NY (n°12). Le folklore
retail est saturé d'ancres de session — et il y a un fait réel derrière : la saisonnalité
intraday (vol en U, effets d'open) est documentée académiquement, y compris en crypto.
→ **Étude transversale à faire une fois, proprement, sur nos données** : saisonnalité
intraday de la volatilité et des rendements sur BTC (Binance) et or (Dukascopy), par heure
UTC. Elle éclairera d'un coup toutes les hypothèses de session du catalogue et dira si les
"killzones" ont un contenu mesurable sur NOS actifs.

## Détails par entrée

### 1. @nolan.vader2 — Gold valuation oscillator
- Dossier : `~/Documents/00-code/VideosTranscriptions/@nolan.vader2/` (.watch.md + frames)
- Format funnel marketing (indicateur = appât, communauté en bio). Aucune formule, aucun
  seuil, pas de timeframe ni stop/target, 1 exemple cherry-picked.
- Visuel : histogramme centré 0, vert>0 = or surévalué, rouge<0 = sous-évalué, bandes
  d'extrême fixes, labels Overvalued/Undervalued. Très probablement spread/ratio normalisé.
- Intérêt réel : dimension **intermarché** (famille Pathfinder/Oxfordstrat) absente du bot.
- Contre-données : Volatility Clustering (continuation > réversion aux extrêmes) ;
  mean-reversion fragile aux frais.
- Action si retenu : test Python z-score XAU vs DXY (données Dukascopy 20 ans), en filtre
  directionnel. Si le nom réel de l'indicateur est trouvé via sa communauté → extraction
  directe des valeurs via MCP TradingView.

### 2. @nolan.vader1b — Volume Profile Execution Model (POC veille)
- Dossier : `~/Documents/00-code/VideosTranscriptions/@nolan.vader1b/` (.watch.md + frames)
- Contenu : volume profile fixed-range sur le range de la veille, POC en ligne rouge étendue,
  boîtes de projection R:R orientées par un "directional bias" décidé ailleurs.
- **Force** : le POC est un objet objectif, non-repaint (figé à la clôture de la veille),
  calculable depuis nos données (aggTrades Binance → volume par bin de prix). Volume-par-prix
  = information réellement orthogonale à nos indicateurs (MACD/EMA/ATR = temporels ;
  notre seul usage du volume est le filtre vol>SMA9, invalidé par Oxfordstrat).
- **Faille rhétorique** : système non falsifiable tel que vendu — le niveau "marche" en rejet
  ET en cassure-retest, et le biais directionnel est exogène (posté par la communauté = le
  produit vendu). Si le trade échoue → "mauvais biais", jamais "mauvais niveau".
- Crypto 24/7 : "range de la veille" exige une définition de session (jour UTC ? session US ?)
  — paramètre à calibrer, effet potentiellement important.
- Protocole de test si retenu : fréquence/amplitude de réaction au toucher du POC vs
  (a) niveaux placebo aléatoires, (b) PDH/PDL/close de veille, (c) VWAP session. Le POC doit
  battre les DEUX pour justifier son coût de calcul.
- **Synthèse émergente vidéos 1+2** : niveaux objectifs (POC ou pivots Livermore) + contexte
  (oscillateur d'étirement) + biais directionnel — et le biais, c'est exactement ce que notre
  bot sait produire (filtre HTF/Supertrend). Le morceau que le funnel vend est celui qu'on a.

### 3. @nolan.vader — Oscillateur COT (Commercials vs Retail)
- Dossier : `~/Documents/00-code/VideosTranscriptions/@nolan.vader/` (.watch.md + frames)
- Contenu : 2 lignes normalisées entre bandes d'extrême (COT-index %-rank 0-100 probable,
  style Briese). Règle : une zone supply/demand n'est tradée que si les Commercials sont à
  l'extrême dans le sens de la zone ET le Retail à l'opposé ; sinon la zone "saute".
- **C'est le générateur de biais du système nolan.vader** (le pilier que vendait le funnel).
- Réalité des données COT (à connaître avant tout test) :
  - CFTC, hebdo : données du mardi publiées le **vendredi 15h30 ET → lag de 3 jours**.
  - Catégories Legacy : Commercials / Non-Commercials (large specs) / Non-Reportables
    (~"retail"). Le "Retail" de la vidéo = probablement non-reportables. À fixer au test.
  - Or (GC) : les Commercials (miniers/dealers) sont structurellement short → tout signal
    doit être RELATIF (percentile sur N semaines), jamais le net brut.
  - Littérature académique réelle et mitigée (Briese, Wang, Sanders/Irwin) : un certain
    pouvoir prédictif des extrêmes de Commercials sur certaines commodities, à horizon
    semaines ; nul comme outil de timing. → biais lent, pas signal.
  - Nuance : les Commercials sont des hedgers CONTRARIENS (short dans les hausses). "Suivre
    le smart money" au sens de la vidéo = en réalité suivre un signal contrarien aux extrêmes.
- **Piège n°1 à l'implémentation : le lookahead**. Beaucoup de scripts TV COT plottent le
  rapport à la date des données (mardi) au lieu de la date de publication (vendredi) =
  3 jours de futur gratuit dans tout backtest. Toujours aligner sur la date de RELEASE.
- Test proposé (données CFTC gratuites 1986→, or GC) : COT-index Commercials sur N semaines ;
  extrême bas + retail opposé → rendement or sur 1-4 semaines ? Si effet → **porte de biais
  hebdomadaire** au-dessus du bot (gate longs/shorts sur l'or). BTC : COT CME depuis 2018.
- **Architecture complète du compte identifiée** : COT = biais (hebdo) → valorisation
  or/dollar = contexte (journalier) → POC/zones = exécution (intraday). Trois horizons
  emboîtés — structure saine en soi, chaque brique restant à valider indépendamment.

### 4. @nolan.vader 2 — Correlation Coefficient XAU/DXY + confluence miroir
- Dossier : `~/Documents/00-code/VideosTranscriptions/@nolan.vader 2/` (.watch.md + frames)
- Contenu : indicateur INTÉGRÉ TradingView (Correlation Coefficient, Pearson glissant,
  longueur défaut 20) lié au DXY. Règle : CC < 0 = corrélation négative "confirmée" →
  chercher long Or si Or en demande ET DXY en offre ; sinon attendre.
- **Analyse critique — la confluence miroir est un double comptage** : si XAU et DXY sont
  fortement anticorrélés, une zone de demande Or EST mécaniquement une zone d'offre DXY
  (images miroir). La "confirmation" recompte la même information. Et si la corrélation est
  faible, la méthode dit de ne pas trader. Contenu réel de la vidéo réduit à une ligne :
  "ne trader les setups Or dollar-driven que quand l'anticorrélation Or/DXY tient".
- **Le nugget légitime** : la corrélation Or/DXY est réelle mais INSTABLE (régimes — en
  crise/dé-dollarisation, or et dollar montent ensemble, cf. épisodes 2024-2026). Un CC
  glissant comme détecteur de régime est une pratique pro légitime et quasi gratuite :
  quand la corrélation casse (CC → 0/+), l'or est piloté par autre chose et les setups
  indexés dollar perdent leur logique.
- **Piège technique** : le built-in TV calcule Pearson sur les PRIX (séries non
  stationnaires → corrélations fallacieuses élevées). Tout test sérieux se fait sur les
  RENDEMENTS (log-returns, fenêtre N jours).
- Test proposé (1 ligne dans le backtest or) : trades Or gatés par corr(rendements
  XAU,DXY ; 20-60j) < seuil (0, -0.3, -0.5) vs non gatés. Éclaire aussi la vidéo n°1
  (le z-score de valorisation n'a de sens qu'en régime anticorrélé).
- Résout la question de la vidéo n°1 : la brique "contexte" du compte = 2 outils distincts
  (valorisation custom + CC intégré), tous deux subordonnés au régime de corrélation.

### 5. @nolan.vader 3 — Correlation Coefficient NASDAQ/ZB1! (doublon de n°4)
- Dossier : `~/Documents/00-code/VideosTranscriptions/@nolan.vader 3/` (.watch.md + frames)
- Mécanique identique à n°4 (CC intégré < 0 + confluence miroir résistance/support) sur
  NASDAQ↔T-Bonds. Mêmes critiques : confluence = double comptage ; CC sur prix ; timeframe
  et gestion non déterminés. Confirme que le compte a UN framework intermarché généralisé.
- **Faille supplémentaire propre à ce couple** : "NASDAQ et bonds bougent en sens opposés"
  est historiquement FAUX comme constante — la corrélation actions/obligations est le régime
  macro le plus documenté qui soit : négative en régime risk-on/off (2000-2021, bonds =
  hedge), POSITIVE en régime inflation (pré-2000, et depuis 2022 : actions et bonds chutent
  ensemble). La vidéo vend comme "truc" une relation qui s'inverse par décennies.
- **Le nugget (meilleur que la vidéo)** : le SIGNE de la corrélation actions/bonds est en
  soi un détecteur de régime macro. Applications pour nous : (a) corr(NQ,ZB) > 0 = régime
  inflation → prudence sur les hedges classiques ; (b) BTC est fortement corrélé au NASDAQ
  depuis 2020 → corr(BTC,NQ) glissante = feature de régime risk-on/off pour le bot BTC.
  Données NQ/ZB gratuites (Yahoo/Dukascopy) ; test = même infra que le filtre n°4.
- Bilan du compte nolan.vader (5 vidéos analysées) : 4 outils réels — COT (biais hebdo),
  valorisation or/dollar (contexte), CC intermarché (régime), POC veille (exécution) —
  emballés en ~40 vidéos. Extraction terminée sauf "daily bias" éventuel.

### 7. @aabandzfx — Anchored VWAP ±2σ/3σ + sweep Asian (stratégie complète)
- Dossier : `~/Documents/00-code/VideosTranscriptions/@aabandzfx/` (.watch.md + frames)
- Première soumission avec règles COMPLÈTES : contexte (zone 2σ-3σ de l'AVWAP, mode Std Dev,
  bandes 2 et 3 cochées — réglages montrés à l'écran), déclencheur (sweep d'un Asian
  high/low dans la zone), confirmation (bougie 5m), stop (extrême de la bougie), cible
  (VWAP ou extrémité opposée), RR 1:2-1:3. Exemples : RR 1.92 et 5.78.
- **Trou béant : le point d'ancrage de la VWAP n'est jamais montré** — or toute la
  stratégie en dépend. Ancre discrétionnaire = hindsight leak (on ancre là où ça marche).
  Pour tout test : ancre OBJECTIVE obligatoire (open de session daily, 00:00 UTC ou
  minuit NY — en input).
- Croisements avec nos données :
  - Famille mean-reversion intraday avec stops serrés = exactement ce qu'Oxfordstrat
    condamne aux frais (Turtle Soup D, %b D). Le sweep d'Asian high = faux breakout d'un
    niveau de session = Turtle Soup transposé intraday. A priori défavorable.
  - MAIS contrairement à nolan.vader, tout est spécifié → backtest en une journée de
    travail, coûts inclus, sur BTC (Binance) + or (Dukascopy). Verdict par les données.
  - "90% win rate" avec RR 1:2 → espérance +1.7R/trade : si c'était vrai, personne ne le
    vendrait en commentaire TikTok. Marketing pur, à ne pas coder.
- **Briques réutilisables indépendamment de la stratégie** (le vrai butin) :
  - **Structure de session (Asian range)** : 1ère apparition au catalogue. Le cycle
    Asie-range → Londres/NY-expansion est une microstructure documentée, y compris en
    crypto. Candidate comme feature de régime intraday du bot (volatilité attendue,
    fenêtres à éviter/privilégier).
  - **VWAP ancrée + bandes σ** : 3e incarnation du concept "étirement" (après z-score
    valorisation et bandes COT) — mesure d'extension volume-weighted, complémentaire ATR.
- **Méta-observation inter-créateurs** : 2e compte, même template — contexte (étirement)
  + niveau (liquidité) + déclencheur (confirmation). Le template retail-guru converge vers
  une architecture à 3 couches ; notre bot a le déclencheur (flip MACD), un contexte faible
  (EMA30), et AUCUNE couche niveau. C'est la couche niveau qui manque partout chez nous.

### 8. @casper_smc — First Candle Rule (ORB M5 9:30 + FVG + engulfing)
- Dossier : `~/Documents/00-code/VideosTranscriptions/@casper_smc/` (.watch.md + frames)
- Règles quasi complètes : 1re bougie M5 de 9:30 EST → H/L = niveaux du jour → en M1,
  cassure exigeant un FVG (gap 3 bougies = displacement) → retest du FVG + engulfing →
  entrée, TP 3:1 fixe, 1 trade/jour. Manquent : placement exact du stop, fenêtre de
  validité, définitions programmatiques FVG/engulfing (exposables en inputs).
- **Première soumission NON mean-reversion** : c'est du breakout/continuation intraday.
- Lecture critique importante : ici le FVG n'est PAS le conte "le prix revient combler le
  gap" (zéro preuve) — il sert de **filtre de displacement sur la cassure** : n'accepter
  que les breaks impulsifs. Ça, c'est aligné avec Volatility Clustering (les mouvements
  forts continuent). Le même objet SMC, utilisé dans le sens supporté par les données.
- Preuves mitigées côté ORB : Oxfordstrat (daily futures 1980-2011) = **D** ; mais
  Zarattini & Aziz 2023 (ORB 5 min sur QQQ, 2016-2023) = résultats forts (débattus :
  levier, coûts, période). Le displacement-filter est peut-être ce qui sépare les deux.
- Structure vs template 3 couches : niveau (H/L 1re bougie) + déclencheur (break impulsif
  puis retest) — mais AUCUN contexte/biais (Zarattini filtrait par volume relatif). Trou
  symétrique à celui de nolan.vader (qui avait le biais mais pas les règles).
- Le pattern retest+engulfing = entrée sur pullback après impulsion — structurellement le
  même motif que notre séquence AK MACD trend→pullback→flip, transposé intraday.
- Tests : (a) ORB 1re bougie M5 ± filtre FVG sur NQ/or ; (b) ancre 9:30 EST sur BTC
  (effets de session US documentés en crypto) ; (c) retest-entry vs entrée immédiate.
- Marketing : "9 years in the markets", "comment class" → cours en live. Schémas purs,
  aucun trade réel montré, claim "consistent winning" non chiffré.
