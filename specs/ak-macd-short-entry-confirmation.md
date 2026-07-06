# Spec — AK MACD : confirmation des entrées SHORT (miroir exact du LONG)

## Objective
Appliquer aux entrées SHORT le **même durcissement** que celui déjà livré pour les
LONG (specs/ak-macd-long-entry-confirmation.md), en miroir. Aujourd'hui un SHORT
entre immédiatement sur un `flip_down`, sans confirmation ni filtre de régime — la
même faiblesse de faux signal qu'on a corrigée côté LONG. Après cette modif, le
`flip_down` **arme un candidat SHORT** qui doit être confirmé par une descente MACD
soutenue, dans un marché qui n'est pas `favorable`. La logique LONG reste
identique ; les deux côtés deviennent symétriques.

## Requirements

1. **flip_down = candidat SHORT, pas entrée.** Un `flip_down` (`macd[t] < macd[t-1]`
   et `macd[t-1] >= macd[t-2]`, inchangé) **arme un candidat SHORT** au lieu d'une
   entrée immédiate. (Miroir de : `flip_up` arme un candidat LONG.)

2. **Confirmation MACD strictement décroissante.** Un candidat SHORT n'autorise une
   entrée que si les `confirmation_bars + 1` dernières valeurs MACD clôturées sont
   **strictement décroissantes** : `macd[t] < macd[t-1] < macd[t-2]` (défaut
   `confirmation_bars = 2`). `<` strict (les égalités échouent).

3. **Fenêtre de validité du candidat.** La confirmation doit survenir dans les
   `candidate_window_bars` bougies clôturées suivant le `flip_down` (inclusive :
   `c < t <= c + W`, défaut 2). Un nouveau `flip_down` ré-arme le candidat SHORT.
   **Expiration** uniquement si : (1) la fenêtre `W` est dépassée, ou (2) invalidation
   explicite (séquence MACD décroissante rompue : `macd[t] >= macd[t-1]`, ou `flip_up`).

3b. **Réévaluation à chaque bougie clôturée** — toutes les conditions SHORT
   réévaluées à chaque bougie de la fenêtre ; entrée dès qu'une bougie satisfait tout.

4. **Filtre de régime (miroir) — blocage doux.** À chaque réévaluation, si
   `rolling_return_regime` renvoie **`favorable`** (marché haussier), l'entrée SHORT
   est bloquée pour cette bougie et journalisée `rejected_regime`. Les labels
   `unfavorable`, `neutral` (et tout label non calculable) n'entraînent pas de
   blocage. Le blocage **ne consomme pas** le candidat : il reste actif jusqu'à
   l'expiration de `W` et peut entrer si le régime cesse d'être `favorable` dans la
   fenêtre. (Miroir de : LONG bloqué si `unfavorable`.)

5. **Autres conditions SHORT existantes requises à la confirmation** : `sequenced_short`
   (tendance baissière → pullback), `macd < 0`, `close < baseline`, `volume > vol_ma`.

6. **Bougies clôturées uniquement** (inchangé).

7. **Candidat unique porteur d'une direction.** À tout instant, au plus un candidat
   est actif, avec une direction (`long` via `flip_up`, `short` via `flip_down`). Un
   `flip` dans la direction **opposée** ré-arme le candidat dans la nouvelle direction
   (l'ancien est abandonné, sans entrée, et journalisé `candidate_armed` du nouveau
   côté). `flip_up` et `flip_down` étant exclusifs, il n'y a jamais deux candidats.

8. **Seuils configurables (mix, partagés).** `confirmation_bars`,
   `candidate_window_bars` et `regime_filter` (déjà dans `AkMacdParams` + surcharge
   `strategy.yaml` `ak_macd:`) gouvernent **les deux côtés**. Aucune nouvelle clé.

9. **Journalisation du cycle de vie** dans `state/ak_macd_local_shadow.jsonl`, mêmes
   actions que le LONG (`candidate_armed`, `candidate_confirmed`, `candidate_expired`,
   `rejected_regime`, `rejected_macd_not_rising`), plus un champ **`side`**
   (`"long"`/`"short"`) indiquant la direction du candidat. Chaque entrée inclut les
   valeurs MACD, le régime, le rendement glissant, `remaining_window`, la raison.

10. **LONG inchangé.** La logique LONG (confirmation croissante, régime `unfavorable`)
    reste strictement identique ; tous ses tests existants continuent de passer.

## Constraints

- **Scope strict** : `orum/external/ak_macd.py` (machine unifiée long/short),
  `orum/external/ak_macd_producer.py` (passage du champ `side` au log), les
  tests. **Ne pas** modifier `orchestrator.py`, `validate.py`, `bracket.py`, `loop.py`,
  ni le contrat de payload (event `SELL_CANDIDATE` inchangé). `goal.yaml allow_short`
  reste le maître interrupteur des shorts.
- **Pas de LLM** ; **pas de nouveau hot reload** ; cerveau déterministe/offline.
- Le SHORT ne doit plus entrer immédiatement sur `flip_down` (suppression du chemin
  d'entrée immédiate `_short_setup`).
- `allow_short=False` doit toujours supprimer toute entrée SHORT (aucun candidat
  SHORT confirmé).

## Edge Cases

- **Égalité MACD** (`macd[t] == macd[t-1]`) côté short → décroissance stricte échoue →
  pas de confirmation ; au bar courant c'est une invalidation (`macd[t] >= macd[t-1]`).
- **Candidat SHORT non confirmé dans la fenêtre** → `candidate_expired` à `c+W+1`.
- **Nouveau `flip_down` pendant un candidat SHORT armé** → ré-armement (fenêtre repart).
- **`flip_up` pendant un candidat SHORT armé** → bascule : candidat LONG armé
  (`candidate_armed`, `side="long"`), le short est abandonné sans entrée. Et miroir :
  `flip_down` pendant un candidat LONG armé → bascule vers un candidat SHORT.
- **Régime non calculable (<20 barres)** → pas de blocage short (seul `favorable` bloque).
- **Confirmation MACD OK mais régime `favorable`** → `rejected_regime`, candidat SHORT
  reste actif, réévalué jusqu'à la fin de `W`.
- **Régime `favorable` puis cesse de l'être dans la fenêtre** (devient `neutral`/
  `unfavorable`), MACD toujours décroissant, autres conditions OK → `candidate_confirmed`
  + `SELL_CANDIDATE`.
- **Séquence MACD décroissante rompue** (`macd[t] >= macd[t-1]`) ou `flip_up` →
  invalidation (expiration ou bascule long).
- **Autre condition SHORT retombée** (`close` repassé au-dessus de `baseline`, volume
  insuffisant) → pas d'entrée, candidat conservé (blocage doux), loggé.
- **`allow_short=False`** → aucun candidat SHORT n'est armé ni confirmé.
- **LONG** : tous les comportements LONG existants restent inchangés (non-régression).

## Definition of Done

- [ ] Un `flip_down` seul n'ouvre plus d'entrée SHORT ; il produit `candidate_armed`
      avec `side="short"`.
- [ ] Entrée SHORT seulement si `macd[t] < macd[t-1] < macd[t-2]` dans la fenêtre `W`,
      **et** `sequenced_short`, `macd<0`, `close<baseline`, `volume>vol_ma`, **et**
      régime ≠ `favorable`.
- [ ] Test : un faux breakdown (down-tick isolé puis remontée) n'entre **pas**
      (candidat seulement) ; une descente confirmée entre.
- [ ] Test : candidat SHORT non confirmé dans `W` → `candidate_expired`.
- [ ] Test : confirmation OK mais régime `favorable` → `rejected_regime`, candidat actif.
- [ ] Test : régime `favorable` puis `neutral`/`unfavorable` dans la fenêtre → entrée.
- [ ] Test : invalidation explicite (MACD remonte / `flip_up`) → expiration ou bascule
      vers candidat LONG.
- [ ] Test : `allow_short=False` → aucun candidat/entrée SHORT.
- [ ] Test : non-régression LONG (tous les tests LONG existants passent inchangés).
- [ ] Chaque action loggée porte `side`, les valeurs MACD, le régime, le rendement
      glissant, `remaining_window`, la raison.
- [ ] `orchestrator.py`/`validate.py`/`bracket.py`/`loop.py` et le contrat de payload
      inchangés.
- [ ] La suite complète passe (`pytest -q`).

## Assumptions

- **Régime miroir** : LONG bloqué si `unfavorable`, SHORT bloqué si `favorable`,
  `neutral` autorisé des deux côtés (symétrie exacte du seuil `threshold_pct`).
- **Candidat unique à direction** : le SHORT ne s'implémente pas en parallèle du LONG
  mais via la **même** machine, avec un état candidat `(index, side)`. C'est ce qui
  rend les deux côtés « exactement pareils ».
- Les seuils sont partagés entre long et short (pas de réglages séparés par direction)
  — cohérent avec « exactement la même chose ».
