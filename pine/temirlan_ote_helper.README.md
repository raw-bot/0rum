# Checklist + indicateur d'aide — setup @temirlan.smc (short ICT en OTE)

_Reproduction fidèle de ce que la vidéo montre (48s, reconstruite des frames — aucune voix).
Catalogue : entrée n°19, verdict POUBELLE argumentée / doublon n°11 en continuation.
Ce livrable est un outil de LECTURE manuelle, pas un système. Rien ici n'est validé par
un backtest — voir tv_catalogue.md pour les deux hypothèses extraites (batch niveaux +
profondeur de pullback), qui se testent dans le labo Python._

## Checklist de confluence (séquence exacte de la vidéo)

Contexte requis — biais SHORT uniquement (la symétrie haussière n'est PAS montrée) :

- [ ] **1. Structure.** Range en haut, puis chute impulsive.
- [ ] **2. BOS baissier.** Le prix casse le dernier point de structure vers le bas.
      Marquer le niveau cassé (ligne horizontale).
- [ ] **3. Breaker.** Marquer la zone/bougie à l'origine de la cassure (rectangle
      au-dessus du BOS). C'est la zone de vente au retour.
- [ ] **4. FVG.** Tracer l'imbalance 3-bougies laissée par l'impulsion, juste au-dessus
      du Breaker (les deux doivent se superposer).
- [ ] **5. Displacement.** L'impulsion baissière de référence : du breaker (haut) au
      creux (bas). C'est la jambe sur laquelle se pose le Fibonacci.
- [ ] **6. Trendline Liquidity.** Pendant le retracement haussier, repérer ≥ 2-3 plus-bas
      alignés le long d'une droite ascendante (stops en dessous = liquidité à balayer).
- [ ] **7. Fibonacci / OTE.** Poser le Fib sur la jambe d'impulsion : **1 = swing high
      avant la chute, 0 = creux du displacement**. Zone OTE = **0.618 → 0.786**.
- [ ] **8. Confluence d'entrée.** Attendre le retour du prix dans FVG ∩ Breaker ∩ OTE.
- [ ] **9. Déclencheur.** Balayage de la trendline liquidity puis rejet dans la zone →
      entrée short. ⚠️ Le type exact de confirmation (bougie de rejet ? CHoCH LTF ?)
      est **non déterminé** dans la vidéo.
- [ ] **10. Stop.** Au-dessus de la zone d'entrée, vers 0.786–1. (Vidéo : 0.055% du
      prix — **inexécutable** : sous les frais crypto RT / de l'ordre d'un spread forex.)
- [ ] **11. Target.** Niveau 0 du Fib (creux du displacement) / liquidité en dessous.
      RR affiché ≈ 1:1.6 (chiffre partiellement coupé à l'écran).

Non déterminés (à NE PAS inventer) : instrument, timeframe, session, confirmation
d'entrée, gestion en cours de trade (BE, partiels), variante haussière.

## Indicateur Pine (`temirlan_ote_helper.pine`) — v6, compilé sans erreur (MCP, 2026-07-06)

Trace uniquement l'automatisable :
- **FVG** : détection objective de l'imbalance 3 bougies (baissière par défaut ;
  haussière disponible mais désactivée — non montrée dans la vidéo).
- **Fib/OTE** : niveaux 1 / 0.786 / 0.618 / 0.5 / 0 + zone OTE ombrée, à partir de
  **deux points cliqués par l'utilisateur** (input.time + input.price, confirm) —
  l'ancrage reste un jugement humain, comme dans la vidéo.
- **Table de rappel** de la checklist (repères manuels : BOS, Breaker, Trendline).

Restent MANUELS (outils de dessin TV) : BOS, Breaker, Trendline Liquidity — la vidéo ne
donne aucune définition programmable de ces trois objets.

Usage : ajouter au chart → cliquer le swing high (1) puis le swing low (0) à l'invite →
le Fib et l'OTE se tracent ; les FVG baissiers récents s'affichent automatiquement.
