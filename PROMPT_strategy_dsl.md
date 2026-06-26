# PROMPT — Hermes Strategy DSL : mutation structurelle par LLM

## Objectif

Aujourd'hui, le « cerveau » d'Hermes (`reflect.py`) ne peut muter qu'**un scalaire** de
`strategy.yaml` (`one_variable_only: true`). Objectif : permettre au LLM de muter la
**structure** de la stratégie — changer d'indicateur, combiner des conditions, ajouter un
filtre de régime — en émettant un **JSON de conditions validable par schéma**, interprété
par un évaluateur Python pur. Jamais d'exécution de code généré par LLM : uniquement des
données interprétées.

## Invariants — ce qui ne change PAS

1. **La comptabilité ne bouge pas.** `accounting.py`, le sizing, SL/TP/max_hold appliqués
   en code dans `loop.py` restent intacts. Le DSL ne touche que la **génération de signal**
   (entrée/sortie par condition). Leçon transversale des audits 0rum/0xBot/Fincept : le
   point de défaillance est toujours la comptabilité des fills, jamais la stratégie — on ne
   la met pas dans le rayon du LLM.
2. **Les garde-fous de risque restent en dur et hors de portée du LLM** : bornes SL/TP,
   `position_size_r`, `daily_loss_limit`, `emergency_stop_drawdown` (depuis `goal.yaml`),
   cooldown (`cooldown_after_change_trades`). Le schéma du DSL n'expose AUCUN champ de risque.
3. **Le pipeline hypothèse → validation → version → historique** de `reflect.py`
   (`_apply_hypothesis`, `_save_change`, `history/vNNNN.yaml`, `hypotheses.jsonl`) est
   conservé ; seul le contenu mutable s'élargit.
4. Worker / watcher / dashboard restent trois processus ; pas de nouveau démon.

---

## 1. Le DSL (schéma cible de `strategy.yaml`)

```yaml
version: "04"
dsl_version: 1
entry:
  logic: AND            # AND | OR — appliqué à plat sur conditions[]
  conditions:
    - indicator: rsi
      params: {period: 14}
      operator: "<="
      value: 25
    - indicator: regime          # le market_regime.py existant, exposé comme indicateur
      operator: "!="
      value_str: "defavorable"
exit:
  logic: OR
  conditions:
    - indicator: rsi
      params: {period: 14}
      operator: ">="
      value: 60
risk:                   # PRÉSENT dans le fichier, ABSENT du schéma mutable
  stop_loss_pct: 2.0
  take_profit_pct: 3.0
  max_hold_candles: 30
  position_size_r: 0.5
direction: long          # v1 : long-only, non mutable
```

### Objet condition

| Champ | Type | Contrainte |
|---|---|---|
| `indicator` | str | whitelist v1 : `rsi`, `sma`, `ema`, `close`, `bollinger`, `atr`, `regime` |
| `params` | obj | bornes par indicateur (ex. `period` ∈ [2, 200]) |
| `field` | str | sorties nommées (`value` par défaut ; bollinger : `upper/middle/lower/pct_b`) |
| `operator` | str | `>`, `<`, `>=`, `<=`, `crosses_above`, `crosses_below`, `rising`, `falling`, `between`, `==`, `!=` (les 2 derniers réservés à `regime`) |
| `value` / `value2` | num | `value2` uniquement pour `between` |
| `value_str` | str | uniquement pour `regime` |
| `compare_mode` | str | `value` (défaut) ou `indicator` (RHS = autre indicateur, ex. SMA croise SMA) |
| `compare_indicator` / `compare_params` / `compare_field` | — | si `compare_mode: indicator` |

Sémantique des croisements (reprise de Fincept, elle est correcte) :
`crosses_above` ⇔ `prev(lhs) <= prev(rhs) && curr(lhs) > curr(rhs)`.

**Limites structurelles (anti-explosion)** : max **4** conditions par groupe, pas de
groupes imbriqués en v1 (un seul niveau, un seul `logic`), `dsl_version` obligatoire.

## 2. Modules à créer (`hermes_trading/dsl/`)

### `dsl/indicators.py`
- Calculs sur un buffer de bougies OHLCV 1m (l'adapter Binance retourne des klines ;
  conserver/demander **200 bougies** par tick — un appel klines suffit).
- Implémentation **pandas pur** ou à la main, mais chaque indicateur est testé contre des
  **valeurs de référence TA-Lib** figées en fixtures (pas de dépendance TA-Lib au runtime).
- Chaque fonction retourne la **série complète** (avec NaN de warm-up en tête) ; l'évaluateur
  lit `[-1]` et `[-2]`. Interdiction de retourner une valeur scalaire « bricolée ».
- `regime` : wrapper de `market_regime.py` existant, retourne une série de labels.

### `dsl/schema.py`
- Schéma `jsonschema` strict (`additionalProperties: false` partout).
- Validation **sémantique** après le schéma : whitelist indicateurs, bornes des params,
  compatibilité opérateur/field (`between` ⇒ `value2` ; `value_str` ⇒ `regime` seulement),
  et **warm-up requis ≤ bougies disponibles** (ex. `sma(200)` + `crosses_*` ⇒ 201 bougies).

### `dsl/evaluator.py` (~150 lignes)
- `evaluate(group: dict, candles: list[Candle]) -> EvalResult`
- `EvalResult = {triggered: bool, details: [{condition, lhs, rhs, met, error}], errors: [str]}`
- **Toute erreur d'évaluation (warm-up insuffisant, NaN) est remontée dans `errors`,
  écrite dans le heartbeat et visible au dashboard.** Une condition en erreur ⇒
  `triggered = False` pour le groupe AND, et l'erreur est journalisée — jamais avalée.

## 3. Intégration `loop.py`

- `entry_signal_fired(strategy, rsi, market)` → `entry_signal_fired(strategy, candles, market)` :
  appelle `evaluator.evaluate(strategy["entry"], candles)`. Idem pour la sortie par condition
  (les sorties SL/TP/max_hold **restent en code**, évaluées avant le DSL).
- `signal_id` : hash du **JSON canonique** (clés triées) de `entry` + `version` — remplace
  la concaténation actuelle indicator/direction/threshold.
- Le RSI « legacy » de `loop.py:_rsi` reste pendant la phase d'ombre (voir §6) puis meurt.

## 4. Pipeline de mutation (`reflect.py`)

Contrat de sortie LLM élargi — l'hypothèse devient :

```json
{
  "action": "change" | "no_change",
  "proposed_entry": { "logic": "AND", "conditions": [ ... ] },
  "proposed_exit":  { "logic": "OR",  "conditions": [ ... ] },
  "rationale": "justification ancrée dans les trades fournis",
  "expected_effect": "métrique attendue et horizon",
  "issue": "étiquette du problème adressé"
}
```

Chaîne de validation, dans cet ordre, chaque étape pouvant `_reject(reason)` :

1. **jsonschema** (`dsl/schema.py`).
2. **Validation sémantique** (whitelist, bornes, warm-up).
3. **Limite de diff structurel** : remplaçant de `one_variable_only` → `one_block_only: true`
   dans `goal.yaml` = au plus **une** condition ajoutée OU supprimée OU modifiée par
   mutation (diff calculé sur les JSON canoniques entry+exit).
4. **Backtest de non-régression** : rejouer la stratégie proposée sur les **7 derniers jours
   de bougies 1m** (déjà accessibles via l'adapter ; sinon cache local) avec le simulateur
   de fills le plus simple possible (fill au close de la bougie de signal, SL/TP intrabar
   conservateur : stop prioritaire). Rejet si : zéro signal sur la fenêtre, ou violation de
   `daily_loss_limit` simulée, ou nombre de trades > 10× la stratégie courante (dégénérescence).
5. **Cooldown** : inchangé.

Fallback déterministe (`_fallback`) : conserve son comportement actuel, reformulé comme une
mutation DSL qui n'ajuste qu'un `value` (il reste un nudge de paramètre — c'est sa force).

## 5. Prompt LLM (à intégrer dans `_hermes_prompt`)

```text
Tu es le module de réflexion d'un bot de paper-trading BTC/USDT (bougies 1m, long-only).
Tu peux proposer UNE modification structurelle de la stratégie, exprimée UNIQUEMENT dans
le format JSON ci-dessous. Tu ne peux PAS toucher au risque (stop, taille, limites) ni à
la direction.

STRATÉGIE COURANTE (JSON) :
{current_dsl}

CATALOGUE AUTORISÉ :
- indicateurs : rsi(period 2-50), sma(period 2-200), ema(period 2-200), close,
  bollinger(period 5-50, std_dev 1-3 ; fields upper/middle/lower/pct_b), atr(period 2-50),
  regime (labels: favorable|neutre|defavorable ; opérateurs == / != seulement)
- opérateurs : >, <, >=, <=, crosses_above, crosses_below, rising, falling, between
- max 4 conditions par groupe, logic AND ou OR, pas d'imbrication
- au plus UNE condition ajoutée/supprimée/modifiée par rapport à la stratégie courante

DONNÉES (tu dois justifier toute modification à partir d'elles ; sinon réponds no_change) :
- 30 derniers trades fermés : {trades}
- score courant et historique : {score}
- répartition des trades par régime de marché : {regime_stats}
- hypothèses passées et leurs effets mesurés : {hypotheses}

RÈGLES :
- "no_change" est une réponse de qualité si les données ne justifient rien.
- Une modification = une hypothèse falsifiable : indique la métrique attendue et l'horizon
  (en trades) dans expected_effect.
- N'optimise pas pour les {n} derniers trades exactement (sur-ajustement) : la justification
  doit invoquer un mécanisme (ex. « les pertes se concentrent en régime défavorable »),
  pas une coïncidence.

Réponds avec UN SEUL objet JSON, sans markdown :
{contrat de sortie du §4}
```

## 6. Ordre d'exécution (phases commit-par-commit)

| Phase | Contenu | Critère de done |
|---|---|---|
| 1 | `dsl/` (indicators, schema, evaluator) + tests, **aucun changement de comportement** | tests verts, dont fixtures TA-Lib |
| 2 | Migration auto de `strategy.yaml` v03 → DSL équivalent (RSI ≤ 25 entry / RSI ≥ 60 exit) ; `loop.py` calcule les DEUX décisions (legacy + DSL) et **logue tout désaccord** (mode ombre) | 24 h de run sans désaccord |
| 3 | Bascule : la décision vient du DSL, suppression du chemin legacy ; `signal_id` canonique | tests d'intégration verts |
| 4 | `reflect.py` : contrat de sortie élargi, chaîne de validation §4, `one_block_only` | mutation structurelle rejetée/acceptée correctement en test |
| 5 | Activer le backtest de non-régression (étape 4 de la chaîne) | rejets observables dans `hypotheses.jsonl` |

## 7. Tests exigés (suivre la discipline existante de `tests/`)

- `test_dsl_schema.py` — rejets : champ inconnu, params hors bornes, `between` sans `value2`,
  risque injecté dans le DSL, 5 conditions, imbrication.
- `test_dsl_indicators.py` — chaque indicateur vs valeurs de référence TA-Lib (fixtures) ;
  **cas warm-up insuffisant ⇒ erreur explicite, jamais silencieuse**.
- `test_dsl_evaluator.py` — sémantique crosses (incluant le cas prev == rhs), AND/OR,
  erreur d'une condition ⇒ `errors` non vide + `triggered` correct.
- `test_dsl_mutation.py` — chaîne complète : schéma → sémantique → diff `one_block_only` →
  backtest ; chaque étape de rejet exercée.
- `test_loop_dsl_shadow.py` — équivalence legacy/DSL sur la config RSI migrée.
- Property test (hypothesis) : tout DSL valide aléatoire ⇒ l'évaluateur ne lève jamais,
  retourne toujours un `EvalResult` bien formé.

## 8. Pièges connus — bugs réellement observés dans Fincept, à NE PAS reproduire

1. **Amorce NaN** (`IndicatorEngine.cpp:46-58`) : EMA d'une série préfixée de NaN (MACD,
   DEMA) amorcée en sommant les NaN ⇒ série entièrement NaN ⇒ indicateur mort en silence.
   → Amorcer toute moyenne au **premier index non-NaN** ; tester explicitement les
   indicateurs composés.
2. **Fallback silencieux** (%D stochastique ≡ %K) : ne JAMAIS remplacer une valeur en erreur
   par une autre valeur plausible. Erreur ⇒ `errors[]` ⇒ visible.
3. **`previous == current`** (croisements DI impossibles) : `prev` doit être un vrai calcul
   sur la barre n-2, jamais une copie de la valeur courante.
4. **Erreurs d'évaluation avalées en live** (comptées en backtest, ignorées en live chez
   Fincept) : même évaluateur, même remontée d'erreurs dans les deux contextes.
5. **Confusion placé/exécuté** : hors périmètre ici (paper), mais le backtest de
   non-régression doit utiliser le même modèle de fill que `loop.py`, pas un modèle idéal.
