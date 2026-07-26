# Campagne 3 — Famille volatilité/options : hypothèses et critique interne (2026-07-26)

Charte : celle de la campagne 2 (`../campaign2-onchain/CHARTER.md`) + amendements de `PHASE4_VERDICT.md` (placebo apparié autonome, leave-one-block-out, breadth). Données auditées : `AUDIT_LSE.md` — prints d'options US 2014-2026 (3 186 sous-jacents), actions 2003+, nos archives crypto. Exécution : actions US et options US via IBKR (non-PRIIPs ; permissions du compte à confirmer par l'utilisateur), spot crypto MiCA.

## Contrainte de données structurante (issue de l'audit)

La tape LSE contient les **prints uniquement — pas de quotes NBBO**. Conséquences gelées : (a) impossible de signer les trades par la règle des quotes → toute hypothèse exigeant la direction des flux d'options est interdite ; (b) les grandeurs implicites se calculent en **prix de straddle pondérés volume près du close** (model-free) plutôt qu'en IV de modèle ; (c) les coûts d'exécution options se modélisent sans spread observé → hypothèses à exécution *actions* privilégiées, options en signal.

## Générées puis éliminées (critique interne, 2 cycles)

- **Flux d'options signés → rendements du sous-jacent** (Pan-Poteshman) : tué au cycle 1 — non-signable sans quotes ; l'originalité reposerait entièrement sur une variable non mesurable proprement (règle n°3 de la charte, leçon C2/R2).
- **Dispersion index vs constituants** : institutionnel, dizaines de jambes, infaisable au capital retail.
- **Timing par structure par terme VIX** : VIX ETP bloqués PRIIPs ; l'expression SPY-options est redondante avec la candidate B ; futures VX non couverts par LSE.
- **Ratio put/call agrégé comme timing** : publié depuis 40 ans, faible, décroissance attendue — pas digne d'un slot.
- **VRP inconditionnel mensuel** : 12 décisions/an (< plancher de 20-30) et, en format defined-risk, la jambe de protection rachète précisément la prime récoltée. Absorbé comme NULL de la candidate B.
- **Achat de straddles pré-earnings** : le run-up d'IV est l'anomalie la plus arbitrée du retail moderne ; coûts single-name élevés. Rejeté ; le cycle earnings survit côté post-événement (candidate A).

## Candidates retenues (3) — pour contradiction externe GPT-5.6

### A — Drift post-earnings conditionné au move implicite (« PEAD moderne »)
- **Mécanisme** : le marché d'options price le move attendu (straddle ATM ÷ spot, model-free) avant chaque publication. Un gap réalisé **au-delà** du move implicite est une surprise au-delà des attentes payantes — la sous-réaction résiduelle (PEAD) devrait se concentrer sur ces cas. Qui paie : les investisseurs qui ancrent sur l'attente pré-publication.
- **Spec (esquisse)** : univers = sous-jacents optionables liquides (≈ 500-1 500 noms) ; à chaque earnings, ratio |gap| / move implicite ; long (short éventuel plus tard) les décile(s) extrêmes dans le sens du gap, détention 5-20 jours ; **des centaines d'événements/an** (breadth maximale de toute la recherche 0rum).
- **Null pré-conçu** : PEAD brut (tri sur gap seul, sans conditionnement options) — attendu mort ou mourant (publié depuis 50 ans) ; la revendication est la **valeur incrémentale du conditionnement**, testée avec la puissance que R2 n'avait pas (~10² événements/an vs ~1,5 pari).
- **Coûts** : exécution actions US (~1 $/ordre IBKR, fractionnables) ; rotation contrôlée par cap de positions simultanées.
- **Risques assumés** : PEAD documenté en décroissance ; capital minimal pour la diversification des positions ; qualité du move implicite sur les noms peu tradés (filtre de liquidité options requis).

### B — Récolte conditionnelle du VRP sur SPY en structures à risque défini
- **Mécanisme** : l'IV du SPY excède systématiquement la vol réalisée future (prime d'assurance payée par les hedgers) ; mais en format retail à risque défini, la prime nette n'est positive que quand le VRP est large. Récolter uniquement alors ; sinon cash.
- **Spec (esquisse)** : signal hebdo = (vol implicite ATM 30 j, des straddle prints) − (prévision RV simple type EWMA) ; si prime > seuil, vendre un condor/credit spread défini 30-45 DTE sur SPY ; 52 décisions/an.
- **Nulls pré-conçus** : (1) récolte inconditionnelle (même structure chaque semaine) ; (2) placebo à même fréquence d'engagement aléatoire ; (3) B&H SPY à bêta moyen égal.
- **Risques assumés** : crowding massif du vol-selling post-2020 ; le defined-risk peut rendre la prime nette ≈ 0 après coûts (c'est précisément ce que le test tranche) ; modélisation du spread d'exécution sans NBBO (conservatrice : mid des prints ± borne) ; tail risk borné par construction mais présent.

### C — Tilt cross-sectionnel par skew de puts (long-only)
- **Mécanisme** : un smirk raide sur un nom (puts OTM chers vs ATM) signale une demande d'assurance/information négative ; littérature (Xing-Zhang-Zhao) : ces noms sous-performent. Version long-only : surpondérer les noms à skew plat. Qui paie : les acheteurs d'assurance single-name informés tardifs.
- **Spec (esquisse)** : hebdo, univers liquide, skew = (P 25Δ − ATM)/ATM depuis prints pondérés volume ; long panier décile plat ; breadth cross-sectionnelle.
- **Risques assumés** : anomalie publiée 2010 (>15 ans → règle n°4, décroissance attendue) ; version long-only diluée (l'edge documenté est surtout côté short) ; 20-40 positions simultanées → contrainte de capital et de coûts au capital réel ; skew depuis prints = bruyant sur les noms peu liquides.

## Ordre de préférence interne avant contradiction

A > B > C. A domine par breadth (le mur des campagnes 1-2 était le manque de décisions indépendantes — A en a des centaines/an), coûts (actions, pas options), et null intégré. B est le test honnête d'une croyance répandue (issue binaire utile quoi qu'il arrive). C est en sursis (règle des >10 ans).

**Statut : en attente de contradiction externe GPT-5.6 (itération 1/3, via l'utilisateur).**
