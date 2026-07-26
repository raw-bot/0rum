# Campagne 2 — Phases 1-2 : hypothèses et critique interne (2026-07-26)

Génération contrainte aux variables PASS de `AUDIT_ONCHAIN.md`. 7 hypothèses générées, 4 éliminées en critique interne, 3 retenues pour la contradiction externe.

## Générées puis éliminées (critique interne)

- **TVL momentum → ETH** : la TVL en USD est mécaniquement le prix des tokens ; même dénominée en natif, le mécanisme (activité DeFi → demande d'ETH) est trop indirect et l'histoire propre trop courte. Éliminée (mécanisme).
- **Frais/congestion mempool comme signal de froth** : mécanisme ambigu (adoption vs spéculation), 3 ans de données. Éliminée (données + mécanisme).
- **Flux de mineurs** : métriques premium uniquement. Interdite (audit).
- **MVRV bands timing** : publiée et popularisée depuis > 10 ans → contrainte 4. Requalifiée en **test d'étalonnage rapide** (réplique du design R1 : le timing MVRV bat-il le buy-and-hold post-2018, net ?) — utile pour calibrer la décroissance post-publication du domaine, pas candidate edge.

## Retenues pour contradiction externe (candidates C1-C3)

### C1 — Émission nette de stablecoins → rendements BTC/ETH (flux de « dry powder »)
- **Mécanisme** : le mint net de stablecoins = fiat net entrant dans l'écosystème = pouvoir d'achat futur ; l'émission répond à une demande de déploiement qui met des jours/semaines à s'exécuter (OTC, allocation par étapes). Qui paie : les acheteurs retardataires dont la demande est annoncée par le mint avant d'être exécutée.
- **Signal (esquisse)** : Δ 30 j de l'offre stablecoin USD agrégée ÷ mcap crypto, z-score ; long BTC/ETH si z élevé, cash sinon ; hebdo avec hystérésis.
- **Risque n°1 (assumé)** : causalité inversée — l'émission suit les rallyes. **Null obligatoire : le signal doit prédire au-delà du momentum prix 30 j.**
- Données : DefiLlama 2017-11→, continu. ~450 semaines dont ~2 cycles complets.

### C2 — Pics d'afflux vers les exchanges → pression vendeuse à 1-2 semaines
- **Mécanisme** : déplacer des coins vers un exchange précède la vente ; un z-spike d'inflows (FlowInExNtv, laggé 2 j) signale une intention de vente des détenteurs. Qui paie : les acheteurs de la période de distribution.
- **Risque n°1** : concept publié et popularisé (CryptoQuant) depuis ~5 ans → décroissance possible ; inflows ≠ ventes (market makers, custody, arbitrage) ; ruptures de couverture CM.
- **Null obligatoire** : rendement passé + vol (les inflows spikent après les mouvements).
- Données : CM 2011→, continu.

### C3 — Tilt market-neutral BTC/ETH par momentum d'activité réseau relative
- **Mécanisme** : la croissance relative d'usage (AdrActCnt, TxCnt, ratio ETH/BTC en variation 60-90 j) précède la revalorisation relative ; les allocateurs suivent l'usage avec retard. Market-neutral (long l'un / short l'autre via perp Kraken 2:1, ou tilt long-only) → **retire le bêta crypto**, rare dans ce domaine.
- **Risque n°1** : AdrActCnt manipulable (spam d'adresses, inscriptions/ordinals 2023+ ont cassé la sémantique de TxCnt BTC) ; relation usage→prix publiée (Metcalfe) depuis longtemps.
- **Null obligatoire** : momentum du ratio de prix ETH/BTC seul.
- Données : CM 2015→ (ETH), continu, ~550 semaines.

## Conformité charte

Les trois candidates : signal continu hebdo (contrainte 1 ✓) ; coûts spot faibles à rotation contrôlée (2 ✓) ; C1/C2/C3 ont chacune un mécanisme qui survit à la dégradation d'une variable (3 ✓ — à challenger) ; aucune ne repose sur une anomalie académique > 10 ans, sauf le fond Metcalfe de C3 (4 ⚠ signalé) ; nulls anti-causalité-inversée intégrés dès la conception (5 ✓) ; développement/confirmation à séparer dans le temps au pré-enregistrement (6 ✓).

**Statut : en attente de la contradiction externe GPT-5.6 (itération 1/3, via l'utilisateur).**
