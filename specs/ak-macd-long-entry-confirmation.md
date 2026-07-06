# Spec — AK MACD : confirmation des entrées LONG (anti faux-rebond)

## Objective
Durcir la logique d'entrée LONG de la stratégie `ak_macd_15m_v1` pour éliminer les
faux rebonds MACD (entrées prises sur un tick haussier isolé pendant que le MACD
décélère encore). Le `flip_up` ne déclenche plus une entrée immédiate : il arme un
*candidat* qui doit être confirmé par une montée MACD soutenue, dans un marché qui
n'est pas défavorable. Les entrées SHORT et le reste du système sont inchangés.

## Requirements

1. **flip_up = candidat, pas entrée.** Un `flip_up` (logique actuelle inchangée :
   `macd[t] > macd[t-1] et macd[t-1] <= macd[t-2]`) **arme un candidat LONG** au lieu
   d'autoriser une entrée immédiate.

2. **Confirmation MACD strictement croissante.** Un candidat n'autorise une entrée
   LONG que si les `confirmation_bars + 1` dernières valeurs MACD clôturées sont
   **strictement croissantes**. Défaut `confirmation_bars = 2` →
   `macd[t] > macd[t-1] > macd[t-2]`. Les égalités échouent (`>` strict, pas `>=`).

3. **Fenêtre de validité du candidat.** La confirmation doit survenir dans les
   `candidate_window_bars` bougies clôturées **suivant** le `flip_up` (fenêtre
   inclusive : `flip` au bar `c`, confirmation autorisée pour `c < t <= c + W`).
   Défaut `candidate_window_bars = 2`. Un nouveau `flip_up` alors qu'un candidat est
   déjà armé **ré-arme** le candidat (la fenêtre repart du nouveau flip).

   **Expiration.** Un candidat n'expire **que** dans deux cas : (1) la fenêtre `W`
   est dépassée sans entrée autorisée, ou (2) une condition existante **invalide
   explicitement** le candidat (voir Assumptions pour la définition : rupture de la
   séquence MACD croissante ou `flip_down`). Tant qu'aucun de ces deux cas n'est
   atteint, le candidat **reste actif** et un nouveau `flip_up` n'est pas requis.

3b. **Réévaluation à chaque bougie clôturée.** À **chaque** bougie clôturée dans la
   fenêtre `W`, **toutes** les conditions d'entrée (croissance MACD, `sequenced_long`,
   `macd>0`, `close>baseline`, `volume>vol_ma`, régime) sont réévaluées. L'entrée est
   autorisée dès qu'une bougie de la fenêtre satisfait **toutes** les conditions
   simultanément.

4. **Filtre de régime (blocage *doux*).** À chaque réévaluation, si
   `rolling_return_regime` (rendement glissant 20 bougies) renvoie le label
   **`unfavorable`**, l'entrée LONG est **bloquée pour cette bougie** et journalisée
   `rejected_regime`. Les labels `neutral` et `favorable` (et tout label non
   calculable faute de warm-up) **n'**entraînent **pas** de blocage. Calcul effectué
   **dans le producteur/cerveau** (qui possède le buffer de bougies), **sans modifier
   l'orchestrateur**.

   **Le blocage régime ne consomme PAS le candidat.** Le candidat reste actif jusqu'à
   l'expiration normale de sa fenêtre `W` (cf. Requirement 3). Si, à une bougie
   ultérieure **dans la fenêtre**, le régime redevient `neutral`/`favorable` **et** que
   toutes les autres conditions tiennent (notamment la séquence MACD toujours
   strictement croissante), l'entrée est autorisée. Plusieurs `rejected_regime`
   peuvent donc être journalisés pour un même candidat avant une éventuelle entrée
   ou expiration.

5. **Toutes les autres conditions LONG existantes restent requises** au bar de
   confirmation : `sequenced_long` (tendance→pullback), `macd > 0`,
   `close > baseline`, `volume > vol_ma`.

6. **Bougies clôturées uniquement.** Toute la logique (flip, confirmation, fenêtre,
   régime) opère exclusivement sur des bougies clôturées ; la bougie en cours de
   formation est ignorée (comportement actuel du producteur conservé).

7. **Seuils configurables (mix).** `confirmation_bars`, `candidate_window_bars` et
   l'activation du filtre de régime ont leurs **défauts dans `AkMacdParams`**
   (`ak_macd.py`) et sont **surchargeables via `state/strategy.yaml`** (section
   `ak_macd:`), **lue une seule fois au démarrage du producteur**. Pas de nouveau
   mécanisme de hot reload.

8. **Journalisation du cycle de vie** dans `state/ak_macd_local_shadow.jsonl`. Actions
   minimales requises : `candidate_armed`, `candidate_confirmed`, `candidate_expired`,
   `rejected_regime`, `rejected_macd_not_rising`. Chaque entrée inclut : les valeurs
   MACD pertinentes (au moins `macd[t]`, `macd[t-1]`, `macd[t-2]`), le label de
   régime, la valeur du rendement glissant utilisée, la fenêtre restante
   (`remaining_window`), et la raison du verdict. Pas d'ajout dans `events.jsonl`
   pour cette version (sauf si c'est déjà la convention existante du module).

9. **Tests.** Couverture au minimum : le cas `232 → 176 → 138 → 157` n'ouvre **pas**
   d'entrée (candidat seulement), et une valeur suivante `> 157` la **confirme** ;
   plus les cas d'expiration, de blocage régime, et de non-régression SHORT.

10. **SHORT inchangés.** Aucune modification de la logique d'entrée SHORT.

## Constraints

- **Scope strict** : modifications limitées à `orum/external/ak_macd.py`,
  `orum/external/ak_macd_producer.py` (lecture config + logs lifecycle),
  les tests (`tests/`), et un ajout de section `ak_macd:` dans `state/strategy.yaml`.
  **Ne pas** modifier `orchestrator.py`, `validate.py`, `bracket.py`, `loop.py`,
  ni le contrat de payload externe.
- **Pas de LLM** ni d'appel réseau/IA dans la logique d'entrée.
- **Pas de nouveau système de hot reload.** Réutiliser un mécanisme de relecture
  existant uniquement s'il est déjà propre ; sinon, lecture au démarrage.
- Le cerveau (`evaluate_ak_macd`) doit rester **déterministe et testable hors-ligne**
  (pas d'I/O caché). Tout état de cycle de vie nécessaire au logging peut être tenu
  par le producteur, ou dérivé de façon déterministe du buffer de bougies.
- La logique de confirmation/fenêtre doit être déterministe et reproductible en
  backtest (pas de dépendance à l'horloge ou au timing des polls).
- `confirmation_bars >= 1` et `candidate_window_bars >= 1` ; les valeurs hors bornes
  ou non entières dans `strategy.yaml` sont ignorées au profit du défaut.

## Edge Cases

- **MACD en NaN pendant le warm-up** (buffer trop court pour `macd[t-confirmation_bars]`)
  → pas de confirmation possible, aucune entrée, aucun crash ; loggé comme
  `rejected_macd_not_rising` (ou silencieux si même la bougie courante est en warm-up).
- **Égalité de valeurs MACD** (`macd[t] == macd[t-1]`) → la croissance stricte échoue
  → pas de confirmation.
- **Candidat non confirmé dans la fenêtre** → `candidate_expired` au bar
  `c + candidate_window_bars + 1` ; un nouveau `flip_up` est nécessaire.
- **Nouveau `flip_up` pendant qu'un candidat est armé** → ré-armement, la fenêtre
  redémarre, log `candidate_armed`.
- **Régime non calculable** (buffer < 20 bougies) → label non-`unfavorable` →
  **n'**entraîne **pas** de blocage (seul `unfavorable` explicite bloque).
- **Confirmation MACD OK mais régime `unfavorable`** → entrée bloquée **pour cette
  bougie**, log `rejected_regime` avec label + rendement glissant ; le candidat
  **reste actif** et est réévalué à la bougie suivante tant que la fenêtre `W` n'est
  pas dépassée. Plusieurs `rejected_regime` consécutifs sont possibles pour un même
  candidat.
- **Régime bloque, puis redevient OK dans la fenêtre** → si à une bougie ultérieure
  (`c < t <= c + W`) le régime est `neutral`/`favorable`, la séquence MACD toujours
  strictement croissante, et les autres conditions valides → `candidate_confirmed` +
  entrée.
- **Régime reste `unfavorable` jusqu'à la fin de la fenêtre** → `candidate_expired`,
  aucune entrée.
- **Séquence MACD croissante rompue dans la fenêtre** (`macd[t] <= macd[t-1]`) ou
  `flip_down` → invalidation explicite du candidat (`candidate_expired`), même si la
  fenêtre `W` n'est pas encore atteinte.
- **Confirmation MACD OK mais une autre condition LONG retombée** (ex. `close` repassé
  sous `baseline`, volume insuffisant) → pas d'entrée ; verdict loggé avec la raison.
- **Section `ak_macd:` absente ou malformée dans `strategy.yaml`** → fallback complet
  sur les défauts `AkMacdParams`, sans crash.
- **Bougie en cours de formation** → jamais utilisée pour armer/confirmer/expirer.
- **SHORT** : un setup SHORT valide doit continuer de produire `SELL_CANDIDATE`
  exactement comme avant (non-régression).

## Definition of Done

- [ ] Un `flip_up` seul **n'**ouvre plus d'entrée LONG ; il produit un
      `candidate_armed` dans `ak_macd_local_shadow.jsonl`.
- [ ] L'entrée LONG n'est émise que si `macd[t] > macd[t-1] > macd[t-2]` (avec
      `confirmation_bars=2`) **dans** la fenêtre `candidate_window_bars=2` après le flip,
      **et** `sequenced_long`, `macd>0`, `close>baseline`, `volume>vol_ma`, **et**
      régime ≠ `unfavorable`.
- [ ] Test : la séquence `232 → 176 → 138 → 157` ne déclenche **aucune** entrée
      (candidat uniquement) ; une valeur suivante `> 157` (dans la fenêtre) déclenche
      `candidate_confirmed` + `BUY_CANDIDATE`.
- [ ] Test : un candidat non confirmé dans `candidate_window_bars` bougies produit
      `candidate_expired` et **aucune** entrée.
- [ ] Test : confirmation MACD valide mais régime `unfavorable` → `rejected_regime`
      (avec label + rendement glissant) et **aucune** entrée à cette bougie ; le
      candidat **reste actif**.
- [ ] Test : régime `unfavorable` à une bougie de la fenêtre puis `favorable` à la
      bougie suivante (toujours dans `W`, MACD encore croissant, autres conditions OK)
      → `candidate_confirmed` + `BUY_CANDIDATE` (le blocage régime n'a pas consommé le
      candidat).
- [ ] Test : candidat invalidé explicitement (rupture de la séquence MACD croissante
      ou `flip_down`) avant la fin de `W` → `candidate_expired`, **aucune** entrée.
- [ ] Test : un setup SHORT valide produit toujours `SELL_CANDIDATE` (non-régression).
- [ ] `confirmation_bars`, `candidate_window_bars` et l'activation du régime sont
      lisibles depuis `AkMacdParams` (défauts) et surchargés si présents sous
      `ak_macd:` dans `strategy.yaml` (vérifié par test).
- [ ] Chaque action loggée (`candidate_armed`, `candidate_confirmed`,
      `candidate_expired`, `rejected_regime`, `rejected_macd_not_rising`) contient
      les valeurs MACD, le régime, le rendement glissant, `remaining_window` et la
      raison.
- [ ] `orchestrator.py`, `validate.py`, `bracket.py`, `loop.py` et le contrat de
      payload externe sont **inchangés** (diff vide sur ces fichiers).
- [ ] La suite de tests complète passe (`pytest -q`), nouveaux tests inclus.

## Assumptions

- Le **rendement glissant** journalisé est la valeur renvoyée par
  `rolling_return_regime` (rendement sur 20 bougies) déjà utilisée par le moteur ;
  aucun nouveau calcul de régime n'est introduit.
- Un candidat bloqué par le régime n'est **pas** consommé : il reste actif et
  réévalué à chaque bougie clôturée jusqu'à l'expiration de sa fenêtre `W`, et peut
  entrer si le régime redevient `neutral`/`favorable` dans cette fenêtre (décision
  explicite de l'utilisateur).
- **Définition de l'« invalidation explicite »** d'un candidat (cas (2) de
  l'expiration) : la séquence MACD strictement croissante est rompue
  (`macd[t] <= macd[t-1]` à une bougie de la fenêtre) **ou** un `flip_down` survient.
  Les autres conditions non structurelles (`close>baseline`, `volume`, `macd>0`,
  `sequenced_long`) qui retombent transitoirement **ne** consomment **pas** le
  candidat : elles bloquent seulement l'entrée à cette bougie, comme le régime. À
  confirmer si l'utilisateur veut traiter l'une d'elles comme invalidation dure.
- La lecture de la surcharge `strategy.yaml` se fait au **démarrage du producteur** ;
  changer les seuils nécessite un redémarrage du producteur (cohérent avec « pas de
  hot reload »).
- Le spec/code est créé dans le projet `.sandbox/0rum-one-shot-home/0rum-trading/`.
