# Rapport d'audit — Hermes Trading (paper-mode self-improving trading worker)

## 1. Résumé exécutif

Ce que fait l'application : un agent de trading papier (aucun ordre réel) sur BTC/USDT. Un worker interroge Binance toutes les 60 s, calcule un RSI 14 sur bougies 1 minute, ouvre/ferme des positions simulées (mean-reversion long-only), et journalise tout dans des fichiers (state/*.jsonl, *.yaml, *.json). Un « cerveau » de réflexion (LLM Gemini/Ollama via le CLI externe hermes, ou un fallback déterministe) ajuste une variable de stratégie tous les 10 trades fermés. Un dashboard HTTP local (127.0.0.1:8787) visualise l'état. Le code vit dans .sandbox/hermes-one-shot-home/hermes-trading/ — c'est le produit d'un « replay sandbox » d'un prompt one-shot, documenté dans sandbox-plan/.

État général : la boucle fonctionne réellement (16 trades réels enregistrés, une mutation de stratégie par LLM effectivement appliquée le 2026-06-02). Mais le système souffre de trois défauts structurels : (1) une comptabilité PnL incohérente qui fait paraître gagnant un bot qui perd net ; (2) un historique de versions de stratégie corrompu (preuve sur disque) ; (3) un fallback offline qui fabrique de faux signaux de trading. Les garde-fous décrits dans goal.yaml ne sont pas implémentés dans le code.

Préparation production : non prête, même pour du paper-trading long-durée non supervisé. Acceptable comme démonstrateur local supervisé.

## 2. Architecture réelle

Modules (tous dans hermes_trading/) :

```text
┌───────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│        Module         │                                                        Rôle                                                        │
├───────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ run.py                │ Point d'entrée du worker (lit state/goal.yaml, lance loop.run_loop)                                                │
├───────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ loop.py               │ Boucle infinie 60 s : fetch données → RSI → décision → écrit position/trades/heartbeat                             │
├───────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ adapters/             │ price (Binance klines 1m), onchain (blockchain.info), news (CoinDesk — API morte), macro (Stooq DXY) — chacun avec │
│                       │  fallback offline                                                                                                  │
├───────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ reflect.py            │ CLI de réflexion : --fallback (déterministe) ou --hermes (subprocess vers le CLI LLM hermes) ; mute strategy.yaml  │
├───────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ hermes_watch.py       │ Watcher (boucle 1800 s) : déclenche reflect --hermes tous les 10 trades fermés                                     │
├───────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ score.py              │ Score composite (retour/drawdown/sharpe) consommé par reflect et dashboard                                         │
├───────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ market_regime.py      │ Classification favorable/neutre/défavorable sur retour glissant 20 bougies                                         │
├───────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ dashboard.py +        │ Serveur HTTP local, GET /api/state, POST /api/reflect                                                              │
│ static/               │                                                                                                                    │
└───────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Flux principal :

```text
                    ┌─────────────────────────────────────────────┐
 Binance/blockchain │  worker (run.py → loop.py), toutes les 60s  │
 .info/Stooq ──────▶│  RSI → open/close position papier           │
 (fallback offline) └──────┬──────────────┬───────────┬───────────┘
                           ▼              ▼           ▼
                  open_position.json  trades.jsonl  heartbeat.json
                           ▲              │
                           │              ▼ (≥10 trades depuis dernière hypothèse)
            strategy.yaml ◀── reflect.py ◀── hermes_watch.py (toutes les 1800s)
                  │  ▲          │   ▲
                  │  └──────────┘   └── CLI `hermes` → LLM (Gemini / Ollama)
                  ▼
            history/vNNNN.yaml + hypotheses.jsonl
                           ▲
       dashboard.py ───────┘ (lit tout, POST /api/reflect peut AUSSI lancer reflect)
```

Persistance : fichiers plats uniquement, pas de base de données, pas de verrou, pas d'écriture atomique. Trois processus indépendants (worker, watcher, dashboard) lisent/écrivent les mêmes fichiers — c'est la zone principale d'incohérence d'état. Le Dockerfile ne lance que le worker ; watcher et dashboard se lancent manuellement.

État au moment de l'audit : le système est arrêté depuis le 2026-06-02 (heartbeat vieux de 9 jours) avec une position papier encore « ouverte » dans open_position.json.

## 3. Fonctionnalités

**Fonctionnalité: Boucle de trading papier**
État: Fonctionnelle mais fragile
Chemin d'exécution: run.py → loop.run_loop
Risque principal: trades fantômes en fallback offline ; duplication au crash
Certitude: Haute (16 trades réels)

**Fonctionnalité: Fetch prix Binance**
État: Fonctionnelle
Chemin d'exécution: adapters/price.fetch
Risque principal: fallback silencieux sur données synthétiques
Certitude: Haute

**Fonctionnalité: Adaptateurs onchain/news/macro**
État: Inutilisées
Chemin d'exécution: fetchées dans loop.py:256 mais aucune décision ne les lit ; seul source va au heartbeat
Risque principal: code mort déguisé ; news appelle une API CoinDesk disparue (heartbeat confirme offline_fallback permanent)
Certitude: Haute

**Fonctionnalité: Réflexion fallback déterministe**
État: Fonctionnelle (testée) mais jamais branchée au runtime — le watcher n'appelle que --hermes
Chemin d'exécution: reflect._fallback
Risque principal: cooldown/évidence existent ici mais pas en mode hermes
Certitude: Haute

**Fonctionnalité: Réflexion LLM (hermes)**
État: Fonctionnelle mais fragile
Chemin d'exécution: hermes_watch.maybe_reflect_once → reflect._hermes
Risque principal: aucune validation des bornes côté code ; corruption de l'historique
Certitude: Haute (1 changement réel appliqué)

**Fonctionnalité: Score composite**
État: Probablement faux dans son intention
Chemin d'exécution: score.score
Risque principal: unités incohérentes (voir §4 B2)
Certitude: Haute

**Fonctionnalité: Dashboard**
État: Fonctionnelle
Chemin d'exécution: dashboard.build_snapshot
Risque principal: PnL affiché surévalué ~4× ; bougies fabriquées
Certitude: Haute

**Fonctionnalité: Garde-fous drawdown (stop strategy, kill-switch)**
État: Non implémentés
Chemin d'exécution: n'existent que dans goal.yaml et l'affichage _guardrail_status ; loop.py ne lit jamais le drawdown
Risque principal: le bot continue de trader quel que soit le drawdown
Certitude: Haute

**Fonctionnalité: Retry adaptateurs / fail-closed**
État: Code quasi mort
Chemin d'exécution: loop._retry ne peut presque jamais retry : chaque adaptateur attrape ses propres erreurs et renvoie un fallback valide
Risque principal: le commentaire « fail closed » (loop.py:324) est trompeur — le système fails open sur données factices
Certitude: Haute

**Fonctionnalité: Déploiement Docker/Railway**
État: Impossible à confirmer
Chemin d'exécution: Dockerfile présent, ne lance que le worker
Risque principal: watcher et dashboard absents du conteneur
Certitude: Moyenne

## 4. Bugs confirmés

**Priorité: P0**
Problème: Le fallback offline du prix fabrique un signal d'achat : série synthétique 65000−12.5×i strictement décroissante → RSI = 0 → entry_fired toujours vrai
Fichier / zone: adapters/price.py:_offline_payload + loop.py:268
Scénario: Binance inaccessible quelques minutes → le bot ouvre une position à ~64 260 $ fictifs ; au retour du réseau, le prix réel (~68 000–71 000 $ dans les données) déclenche un take-profit fantôme de +5 à +10 %
Conséquence: trades fantômes mélangés à l'historique réel, polluant score, réflexion LLM et stats ; rien ne les filtre (seuls price_source_at_entry/exit les marquent)
Vérification: Calcul : tous les deltas négatifs ⇒ avg_gain=0 ⇒ RSI=0 ≤ seuil 25. Le test test_offline_candle_timestamp_does_not_crash_exit_logic confirme que ce chemin est assumé

**Priorité: P1**
Problème: Historique de stratégie corrompu : la stratégie est mutée avant archivage, donc history/vNNNN.yaml enregistre la version déjà modifiée sous l'ancien numéro
Fichier / zone: reflect.py : _apply_hypothesis/_fallback mutent strategy in place, puis _save_change:37 archive
Scénario: toute réflexion qui change une variable
Conséquence: l'audit trail est faux ; impossible de savoir quelle stratégie a réellement produit quels trades
Vérification: Preuve sur disque : state/history/v0001.yaml contient threshold: 25.0 alors que tous les trades strategy_version: 01 montrent entry_reason: "... threshold=30.00"

**Priorité: P1**
Problème: Comptabilité PnL incohérente : score() et _portfolio() composent pnl_pct (variation de prix) comme si 100 % du solde était investi, alors que le sizing (loop._sizing) n'engage que 25 % du solde (2 500 $/10 000 $) ; les frais sont ignorés
Fichier / zone: score.py:27, dashboard.py:46-61 vs loop.py:101-111,171-200
Scénario: en continu
Conséquence: le solde affiché et le score sont ~4× surévalués et ignorent les frais. Le bot perd net mais paraît gagnant : trade réel pnl_pct=+0.000319 / net_pnl_usd=−1.28 $
Vérification: Preuve sur disque : 16 trades, somme des net_pnl_usd majoritairement négative tandis que pnl_compound du dashboard est positif

**Priorité: P1**
Problème: La réflexion LLM applique new_value sans aucune validation des bornes ; les limites (threshold 10–45, stop 0.5–5, size 0.5–0.75) n'existent que dans le texte du prompt
Fichier / zone: reflect.py:_apply_hypothesis:150-164
Scénario: le LLM renvoie stop_loss_pct: 0.0 ou threshold: 90 (hallucination ou JSON mal extrait par la regex \{.*\} de _extract_json)
Conséquence: stratégie aberrante appliquée silencieusement ; aussi float(None) → crash si changed=true sans new_value
Vérification: Lecture du code : aucun clamp entre l'extraction JSON et STRATEGY_PATH.write_text

**Priorité: P2**
Problème: Garde-fous de goal.yaml non implémentés : max_drawdown → « stop_strategy », emergency_stop → « neutralize_positions », daily_loss_limit, failure_conditions — rien dans loop.py ne les lit
Fichier / zone: goal.yaml vs loop.py (aucune référence)
Scénario: drawdown > 6 %
Conséquence: le worker continue de trader ; seul l'affichage (dashboard._guardrail_status) et le score (−1.0) changent
Vérification: grep : max_drawdown/emergency_stop n'apparaissent que dans score.py et dashboard.py

**Priorité: P2**
Problème: Fallback de _held_candles cassé : quand candle_ts n'est pas numérique (mode offline), il compare opened_index à len(closes)−1 — mais la fenêtre Binance est glissante de taille fixe 60
Fichier / zone: loop.py:137-144
Scénario: position ouverte en mode offline (ts "offline-59")
Conséquence: held_candles reste ~0 pour toujours → max_hold ne déclenche jamais → position offline ne sort que par stop/TP/RSI
Vérification: Lecture : opened_index=59, current_index=59 à chaque itération

**Priorité: P2**
Problème: Duplication de trade au crash : le trade est appendé à trades.jsonl puis open_position.json est supprimé ; un crash entre les deux re-ferme le même trade au redémarrage
Fichier / zone: loop.py:276-277
Scénario: kill/crash dans la fenêtre entre les deux écritures
Conséquence: trade compté deux fois dans PnL, score, déclenchement de réflexion
Vérification: Lecture du code (ordre des opérations, pas de transaction)

**Priorité: P2**
Problème: Aucune écriture atomique ni verrou : strategy.yaml, heartbeat.json, open_position.json écrits en mode w direct, lus en parallèle par 3 processus
Fichier / zone: loop.py:58-65, reflect.py:39, dashboard.py (ThreadingHTTPServer)
Scénario: lecture pendant écriture
Conséquence: YAML/JSON partiel → exception : dans le worker c'est compté comme « failure » (5 d'affilée = mort du process) ; dans do_GET (sans try/except) la connexion est coupée
Vérification: Lecture du code

**Priorité: P2**
Problème: Course entre deux réflexions : le watcher et POST /api/reflect (bouton UI, aucun rate-limit) peuvent lancer reflect --hermes simultanément
Fichier / zone: dashboard.py:333-349 + hermes_watch.py:88
Scénario: clic utilisateur pendant une fenêtre de réflexion du watcher
Conséquence: double bump de version, hypothèse écrasée, deux appels LLM payants concurrents
Vérification: Lecture du code (aucun lock fichier)

**Priorité: P3**
Problème: Compteur de réflexion du dashboard faux : len(trades) % reflection_every alors que le watcher compte les trades depuis la dernière hypothèse
Fichier / zone: dashboard.py:264-266 vs hermes_watch.py:61
Scénario: dès la première réflexion non-multiple de 10
Conséquence: barre de progression UI incohérente avec le déclenchement réel
Vérification: Lecture + données : 16 trades / 3 hypothèses

**Priorité: P3**
Problème: daily_loss_limit (perte journalière) comparé au pire trade individuel
Fichier / zone: reflect.py:93
Scénario: un seul trade à −1.5 %
Conséquence: déclencheur de réduction de risque sémantiquement faux (trop laxiste : 10 trades à −1 % le même jour ne déclenchent rien)
Vérification: Lecture du code

**Priorité: P3**
Problème: Cooldown et exigence d'évidence (cooldown_after_change_trades, evidence_reflections_required) implémentés uniquement dans _fallback, jamais en mode hermes — qui est le seul mode branché au watcher
Fichier / zone: reflect.py:77-118 vs _hermes
Scénario: chaque réflexion LLM
Conséquence: le LLM peut changer la stratégie à chaque cycle de 10 trades sans périodes d'observation, contrairement à la politique de goal.yaml
Vérification: Lecture du code

## 5. Risques probables (non observés directement)

**Priorité: P1**
Risque: Reprise après 9 jours d'arrêt : la position ouverte du 2026-06-02 sera fermée au redémarrage avec held_candles énorme (delta de candle_ts ÷ 60000 ≈ 13 000) → max_hold au prix actuel
Fichier / zone: loop.py:close_position_if_needed
Scénario: redémarrer le worker maintenant
Conséquence: un trade aberrant de 9 jours entre dans les stats et peut déclencher la réflexion sur une donnée absurde
Certitude: Haute (état sur disque vérifié)

**Priorité: P2**
Risque: Mort définitive du worker : après 5 échecs consécutifs, raise tue le process ; Dockerfile sans politique de restart, pas de supervision locale
Fichier / zone: loop.py:324-328
Scénario: 5 erreurs de schéma ou écritures corrompues d'affilée
Conséquence: arrêt silencieux du trading ; seul le heartbeat vieillissant le révèle, et rien ne le surveille
Certitude: Moyenne

**Priorité: P2**
Risque: Lecture concurrente de trades.jsonl pendant un append : ligne partielle → json.loads lève → reflect crashe (watcher status error) ou snapshot dashboard échoue
Fichier / zone: reflect.py:_load_jsonl, dashboard.py:_read_jsonl
Scénario: trade fermé exactement pendant une réflexion
Conséquence: réflexion ratée (retentée 30 min plus tard) — transitoire
Certitude: Moyenne

**Priorité: P2**
Risque: signal_id change quand la version de stratégie change → la déduplication par candle peut laisser rouvrir une position sur la même bougie juste après une réflexion
Fichier / zone: loop.py:75-91
Scénario: réflexion entre deux itérations sur la même bougie signal
Conséquence: doublon d'entrée ponctuel
Certitude: Faible-moyenne

**Priorité: P3**
Risque: Stop-loss évalué une fois par minute sur les clôtures uniquement : la perte réelle peut dépasser le stop (et le PnL enregistré au prix de clôture, pas au prix du stop)
Fichier / zone: loop.py:159
Scénario: bougie volatile
Conséquence: les pertes enregistrées sous-estiment/surestiment ce qu'un vrai stop aurait donné — biais du paper-trading
Certitude: Haute (par construction)

## 6. Analyse métier et stratégies

**Stratégie: RSI(14) 1 m mean-reversion long-only** (entrée RSI≤25, sortie RSI≥55 / TP 3 % / SL 2 % / max 30 bougies)
Objectif: +7 %/30 j, drawdown <5 %
Utilisée ?: Oui (16 trades réels)
Mesurable ?: Oui mais mal mesurée (frais ignorés par le score)
Défauts: Voir analyse ci-dessous
Confiance: Haute

**Stratégie: Régime de marché** (retour glissant 20 bougies, ±0.3 %)
Objectif: contexte d'analyse
Utilisée ?: Enregistrée dans les trades, jamais utilisée en décision (ni filtre d'entrée ni sortie)
Mesurable ?: Oui
Défauts: seuils arbitraires ; horizon 20 minutes très court pour parler de « régime »
Confiance: Haute

**Stratégie: Réflexion fallback déterministe**
Objectif: ajustement prudent avec évidence + cooldown
Utilisée ?: Non branchée (le watcher n'appelle que --hermes)
Mesurable ?: Oui (testée)
Défauts: la seule politique disciplinée du système est celle qui ne tourne pas
Confiance: Haute

**Stratégie: Réflexion LLM (Gemini/Ollama)**
Objectif: ajuster 1 variable/cycle
Utilisée ?: Oui (1 changement réel : threshold 30→25 le 2026-06-02)
Mesurable ?: Partiellement
Défauts: optimise le score qui repose sur le pnl brut ; pas de bornes, pas de cooldown, non-déterministe et non-reproductible
Confiance: Haute

Le défaut métier central — stratégie plausible mais structurellement perdante telle qu'implémentée, et le système d'auto-amélioration ne peut pas le voir :

- Sur bougies 1 minute avec un hold max de 30 minutes, BTC ne bouge presque jamais de ±2–3 % : dans les 16 trades réels, aucun stop/TP n'a déclenché — toutes les sorties sont rsi_reversion ou max_hold, avec des gains bruts de 0.03–0.12 %.
- Les frais simulés (0.04 % × 2 = 0.08 % du notionnel = 2 $ par trade) dépassent le gain brut médian → espérance nette négative, visible dans les données (net_pnl_usd < 0 sur la majorité des trades récents).
- Or score() et la réflexion LLM ne voient que pnl_pct brut. Le système peut donc « s'améliorer » indéfiniment sur une métrique qui ignore précisément ce qui le fait perdre. C'est le cas typique de la stratégie qui semble fonctionner alors que ses mesures sont fausses.
- Le changement appliqué par Gemini (seuil 30→25, justification « persistent losses and max_hold exits ») était raisonnable au vu des données qu'on lui montre, mais ces données excluent les frais.
- Cibles non vérifiables : min_sharpe: 1.3 est comparé à mean/std·√n (score.py:30), qui est une statistique t, pas un ratio de Sharpe annualisé. target_return_30d est comparé à un retour composé toutes-périodes, sans fenêtre de 30 jours.

## 7. Données et base de données

Pas de SGBD — fichiers plats dans state/. Conséquences :

- Pas de transactions : la paire « append trade + delete position » n'est pas atomique (bug P2 ci-dessus). Pas de schéma imposé : les anciens trades n'ont pas rsi_at_entry/market_regime_* (visible dans trades.jsonl — le schéma a évolué silencieusement, le code lit avec .get() partout, donc tolérant mais sans validation).
- Timestamps : cohérents (UTC isoformat partout, datetime.now(UTC)), comparaisons lexicographiques de chaînes ISO (reflect._trades_after, hermes_watch._trades_after) — correctes tant que le format reste identique, fragiles si un « Z » apparaît. candle_ts Binance en ms epoch, mais "offline-59" (chaîne) en mode fallback : type instable sur le même champ.
- Données manquantes pour le diagnostic : le solde courant n'est jamais persisté (recalculé de deux façons contradictoires) ; les trades fermés ne stockent pas opened_at (seulement dans la position, supprimée à la fermeture — l'heure d'entrée est perdue) ; aucune trace des itérations sans décision (heartbeat écrasé à chaque tour : pas d'historique du RSI ni des sources) ; les erreurs d'adaptateurs ne sont pas journalisées (fallback silencieux) ; pas de compteur de trades « offline » vs « binance ».
- Archives : state/archive/invalid-minute-sampler-20260601T182500Z/ documente un incident réel passé (trades ouverts/fermés sur la même bougie, entry==exit, pnl≈0) — corrigé ensuite par le mécanisme signal_id. Bonne pratique d'archivage manuel, mais la migration s'est faite à la main, sans outillage.

## 8. Tests manquants

16 tests, tous unitaires, tous verts en 0.056 s. Couvert : décision de trade, sizing, dédup par signal, fallbacks d'adaptateurs (schéma seulement), politique fallback de réflexion, score négatif, statut watcher, snapshot dashboard.

Manquant — précisément les zones où j'ai trouvé les bugs :

- run_loop lui-même n'a aucun test (orchestration, ordre append/unlink, compteur d'échecs).
- Aucun test de redémarrage/reprise (position ouverte ancienne, fichier corrompu, trade dupliqué après crash).
- Aucun test de concurrence (reflect pendant écriture du worker, double réflexion).
- Aucun test vérifiant que le fallback offline ne génère pas de signal d'entrée — le test existant vérifie seulement le schéma, et un autre teste que le chemin offline ne crashe pas, légitimant le comportement dangereux.
- Aucun test de _apply_hypothesis avec valeurs hors bornes / new_value=None / JSON LLM malformé.
- Aucun test de cohérence comptable (somme net_pnl_usd vs portfolio dashboard) — il aurait immédiatement révélé le bug P1.
- Aucun test d'intégration du watcher → reflect (subprocess), ni du POST /api/reflect.

## 9. Sécurité

Contexte : outil local, paper-mode, dashboard sur 127.0.0.1 — surface limitée. Néanmoins :

- Clé API en clair : .sandbox/hermes-local-llm-home/.gemini_api_key contient une clé lue par hermes_watch.py:84-86 et injectée dans l'environnement. Un auth.json existe aussi. Le projet n'est pas un dépôt git (pas de risque de commit immédiat), mais la clé vit dans l'arborescence du projet, sans permissions restreintes vérifiées. (Valeurs non reproduites ici.)
- POST /api/reflect : aucune authentification, aucun rate-limit — chaque requête lance un subprocess + un appel LLM payant (Gemini). Localhost seulement, mais n'importe quel processus local ou page web visitée (requête same-machine via JS vers 127.0.0.1:8787, pas de vérification d'Origin) peut déclencher des dépenses ou la course de réflexion. CORS n'est pas configuré (pas d'en-têtes), ce qui bloque la lecture cross-origin mais pas l'envoi du POST.
- reflect._hermes passe le prompt entier en argument de ligne de commande (hermes -z <prompt>) : visible dans ps par tout utilisateur local, et le flag --ignore-rules désactive explicitement des garde-fous du CLI hermes.
- Le contenu de trades.jsonl/hypotheses.jsonl est injecté tel quel dans le prompt LLM, et la réponse LLM est appliquée à la stratégie : chaîne d'injection indirecte théorique, bornée par le fait que seules 3 variables numériques sont applicables — mais sans validation de bornes (bug P1).
- .env présent avec emplacements pour clés d'exchange — vides conformément au runbook (non révélées). Dockerfile : uv installé via curl | sh (supply-chain classique), COPY state ./state embarque l'état (et l'historique de trades) dans l'image.

## 10. Performance et robustesse

- Volumétrie actuelle triviale (16 trades). Mais tout est relu intégralement à chaque usage : trades.jsonl relu par le worker à chaque itération (_load_recent_trades), par le dashboard toutes les 2.5 s (polling dashboard.js:404 → build_snapshot relit 7 fichiers), par le watcher toutes les 30 min. À ×10–×100 trades c'est encore acceptable ; à un an de fonctionnement (fichier de plusieurs Mo relu 24 fois/min par le dashboard), ça devient mesurable.
- Timeouts corrects sur HTTP (15 s) et subprocess (180 s). Le retry exponentiel de loop._retry dort même après la dernière tentative (sommeil inutile) — et est de toute façon quasi inatteignable (§3).
- Pas de reprise propre après crash : pas de supervision, pas de verrou d'instance unique (deux workers lancés par erreur = double trading sur les mêmes fichiers), mort définitive après 5 échecs.
- Pas de fuite mémoire identifiée (pas d'accumulation en mémoire ; tout est relu du disque).

## 11. Observabilité

- Logs : uniquement des print non structurés et non persistés (sauf redirection manuelle). Les échecs d'adaptateurs sont totalement silencieux (fallback sans trace). Le passage online→offline du prix — l'événement le plus dangereux du système — n'émet aucun log.
- Heartbeats : bonne idée (heartbeat.json, hermes_watcher.json, avec sources de données et raison de décision — réellement utile), mais écrasés à chaque écriture : aucun historique, impossible de diagnostiquer a posteriori « que voyait le bot à 14 h 32 ».
- Aucune alerte : un worker mort se manifeste seulement par un heartbeat qui vieillit, que rien ne surveille (le dashboard affiche l'âge du watcher, pas celui du worker).
- Pas de health check HTTP pour le worker ; le dashboard n'expose pas l'état de fraîcheur du heartbeat comme statut explicite.
- Points positifs : entry_reason/exit_reason/decision_reason lisibles, price_source_at_entry/exit dans les trades, hypothèses LLM avec modèle/provider/raison — la traçabilité des décisions est meilleure que la moyenne ; c'est la traçabilité des incidents qui manque.

## 12. Plan d'action priorisé

**À corriger immédiatement**

1. Neutraliser le signal d'entrée en mode offline — adapters/price.py + loop.py : si source == "offline_fallback", interdire l'ouverture (et idéalement geler la gestion de position). Résout : trades fantômes P0. Effort : faible.
2. Archiver la stratégie avant mutation — reflect.py : copier strategy.yaml (ou deep-copy du dict chargé) avant _apply_hypothesis/_fallback, ou archiver depuis le fichier disque dans _save_change. Résout : historique corrompu P1. Effort : faible.
3. Valider les bornes des hypothèses LLM — reflect._apply_hypothesis : clamp aux bornes du prompt, rejeter new_value manquant/non numérique. Résout : stratégie aberrante P1. Effort : faible.
4. Unifier la comptabilité sur le net — score.py, dashboard._portfolio : calculer le retour au niveau du compte (net_pnl_usd / balance) au lieu de composer le pnl_pct prix ; inclure les frais. Résout : bot perdant affiché gagnant P1 ; rend la réflexion LLM capable de voir le vrai problème. Effort : moyen.
5. Purger ou clôturer manuellement la position ouverte du 2026-06-02 avant tout redémarrage (action opérateur, pas code). Résout : trade aberrant de 9 jours. Effort : faible.

**À corriger ensuite**

6. Écritures atomiques (tempfile + os.replace) pour strategy.yaml, open_position.json, heartbeats — loop.py, reflect.py, hermes_watch.py. Résout : lectures partielles, corruption au crash. Effort : faible.
7. Inverser/protéger l'ordre fermeture de trade : marquer la position « closing » ou dédupliquer à l'append (signal_id + candle_ts déjà présents). Résout : duplication au crash. Effort : faible.
8. Verrou de réflexion (lockfile) partagé entre watcher et POST /api/reflect ; rate-limit sur l'endpoint. Résout : course de réflexion + dépense LLM incontrôlée. Effort : faible.
9. Implémenter réellement les garde-fous de goal.yaml dans loop.py (drawdown ≥ max → ne plus ouvrir ; emergency → fermer et exiger un fichier « ack » manuel). Résout : divergence politique/code. Effort : moyen.
10. Appliquer cooldown + évidence au mode hermes (réutiliser la logique de _fallback comme garde autour de _hermes). Effort : moyen.

**À améliorer plus tard**

11. Logging structuré persistant (fichier JSONL d'événements, y c. bascules de source et échecs d'adaptateurs) + historisation du heartbeat. Effort : moyen.
12. Supprimer ou brancher réellement les adaptateurs news/onchain/macro (et retirer l'appel CoinDesk mort). Effort : faible.
13. Tests d'intégration : run_loop avec adaptateurs mockés, crash/reprise, cohérence comptable, hypothèse LLM hors bornes. Effort : moyen.
14. Superviser les 3 processus (compose/supervisord + restart policy ; le Dockerfile n'en lance qu'un). Effort : moyen.
15. Corriger le compteur de réflexion du dashboard et la sémantique de daily_loss_limit. Effort : faible.

## 13. Zones encore incertaines

- Le CLI hermes lui-même : binaire externe non audité ici (seuls ses fichiers de config/sessions dans .sandbox/hermes-local-llm-home/ sont visibles). Le comportement exact de --ignore-rules et la robustesse de sa sortie JSON ne sont pas vérifiables depuis ce code.
- Déploiement Railway : le runbook le décrit, l'EXECUTION_LOG y fait référence, mais je n'ai aucun moyen de confirmer qu'un déploiement existe ni son état.
- Fréquence réelle des bascules offline : aucun trade enregistré n'a price_source = offline_fallback dans trades.jsonl actuel — le scénario P0 est démontré par construction (calcul RSI sur la série synthétique) mais ne s'est pas encore produit avec position dans cette série de données. L'archive invalid-minute-sampler montre en revanche qu'un incident de données a déjà eu lieu.
- Comportement de hermes -z sous prompt long : le passage du prompt en argv peut atteindre des limites de taille avec un gros historique de trades — non testé.
- Je n'ai pas exécuté le worker ni le dashboard (cela aurait modifié state/ — exclu par les règles de l'audit) ; tous les constats runtime s'appuient sur l'état persisté des runs de juin 2026 et sur la lecture du code.

---

## Avis final (très court)

L'app remplit-elle son but ? En tant que démonstrateur d'agent de paper-trading auto-réflexif : oui, partiellement — la boucle tourne vraiment, trade en papier, et le LLM a réellement ajusté la stratégie une fois, avec une bonne traçabilité des décisions. En tant que système capable de mesurer s'il s'améliore — ce qui est sa raison d'être — : non. Sa comptabilité ignore les frais et surévalue le PnL (~4×), son historique de versions est factuellement faux, son fallback offline fabrique des signaux d'achat, et les garde-fous de goal.yaml n'existent que sur le papier. Le bot perd de l'argent net tout en se croyant — et en se montrant — gagnant : c'est exactement le défaut qu'un agent « self-improving » ne peut pas se permettre.


## Correction possible

Oui — et c'est même un bon candidat à la correction, car les défauts sont localisés et peu nombreux, alors que l'architecture de base (boucle simple, état en fichiers, traçabilité des décisions, séparation worker/réflexion/dashboard) est saine pour son usage. Rien n'exige de réécriture. Mais il faut d'abord clarifier ce que « utilisable » veut dire, car il y a deux cibles très différentes :

Cible 1 — Laboratoire de paper-trading honnête : oui, facilement

C'est l'objectif réel du projet (tester si un agent auto-réflexif peut s'améliorer). Pour ça, il faut que le système mesure juste et ne se mente pas. C'est ~1 à 2 jours de travail, environ 200–300 lignes modifiées dans 4 fichiers :

┌─────┬────────────────────────────────────────────────────────────────────────────────┬────────────────────────────────┬────────────────────┐
│  #  │                                   Correction                                   │            Fichiers            │       Effort       │
├─────┼────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────┼────────────────────┤
│ 1   │ Interdire l'ouverture de position quand source == "offline_fallback" (et geler │ loop.py (~10 lignes)           │ Trivial            │
│     │  la gestion de position offline)                                               │                                │                    │
├─────┼────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────┼────────────────────┤
│ 2   │ Archiver strategy.yaml avant mutation (copie du fichier disque dans            │ reflect.py (~5 lignes)         │ Trivial            │
│     │ _save_change)                                                                  │                                │                    │
├─────┼────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────┼────────────────────┤
│ 3   │ Clamp des valeurs LLM aux bornes (10–45 / 0.5–5.0 / 0.5–0.75) + rejet de       │ reflect.py (~15 lignes)        │ Trivial            │
│     │ new_value manquant                                                             │                                │                    │
├─────┼────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────┼────────────────────┤
│     │ Comptabilité au niveau du compte : retour par trade = net_pnl_usd / balance,   │ score.py, dashboard.py,        │ Moyen — c'est le   │
│ 4   │ frais inclus, dans score() et _portfolio() ; persister le solde courant dans   │ loop.py                        │ chantier central   │
│     │ l'état                                                                         │                                │                    │
├─────┼────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────┼────────────────────┤
│ 5   │ Écritures atomiques (tempfile + os.replace) + lockfile de réflexion partagé    │ loop.py, reflect.py,           │ Faible             │
│     │ watcher/endpoint POST                                                          │ hermes_watch.py, dashboard.py  │                    │
├─────┼────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────┼────────────────────┤
│ 6   │ Au démarrage : invalider toute position dont opened_at dépasse un âge max (ta  │ loop.py (~10 lignes)           │ Faible             │
│     │ position du 2026-06-02 serait écartée au lieu de produire un trade de 9 jours) │                                │                    │
├─────┼────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────┼────────────────────┤
│ 7   │ Garde-fous réels : drawdown ≥ max_drawdown → plus d'ouverture ; ≥              │ loop.py                        │ Moyen              │
│     │ emergency_stop → fermeture + fichier d'acquittement manuel                     │                                │                    │
├─────┼────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────┼────────────────────┤
│ 8   │ Cooldown + évidence appliqués aussi au mode hermes (la logique existe déjà     │ reflect.py                     │ Faible             │
│     │ dans _fallback, il suffit de la mettre en garde-fou autour de _hermes)         │                                │                    │
└─────┴────────────────────────────────────────────────────────────────────────────────┴────────────────────────────────┴────────────────────┘

Plus quelques tests de non-régression (cohérence comptable, hypothèse hors bornes, offline sans entrée) — c'est précisément les tests dont l'absence a laissé passer les bugs.

Cible 2 — Système qui gagne de l'argent (même en papier) : c'est de la recherche, pas du bugfix

Le point dur n'est pas technique : une fois la comptabilité corrigée (fix #4), le système verra ce que les données montrent déjà — la stratégie actuelle perd structurellement : sorties RSI-reversion à +0.03–0.12 % brut contre 0.08 % de frais aller-retour. Aucun des trois leviers que la réflexion peut toucher (entry.threshold, stop_loss_pct, position_size_r) ne peut réparer ça, car le problème est dans la sortie (gains trop petits par rapport aux frais), pas dans l'entrée.

Pour que la boucle d'auto-amélioration ait une chance, il faudrait au minimum :
- exposer exit_rsi_threshold, take_profit_pct et max_hold_candles comme variables modifiables (et leurs bornes) ;
- intégrer les frais dans le score (couvert par fix #4) ;
- probablement passer à un timeframe plus long (5 m ou 15 m) où des mouvements supérieurs aux frais sont atteignables en un hold raisonnable.

Et même alors, rien ne garantit une stratégie rentable — c'est l'objet de l'expérience, pas un défaut corrigeable.

Ce que je recommande

Faire la cible 1 en deux lots : lot A = fixes 1–4 (intégrité des données et des mesures — sans eux, toute donnée accumulée est inexploitable), lot B = fixes 5–8 (robustesse opérationnelle). Avant tout redémarrage, archiver l'état actuel dans state/archive/ comme ça a été fait pour les runs précédents, car l'historique existant mélange deux comptabilités et un historique de versions faux — il ne doit pas servir de base à la réflexion future.

Une réserve : le périmètre sandbox-plan/ traite ce code comme un artefact de replay à auditer avant modification (POST_RUN_AUDIT.md, ADR-001 « sandbox replay first »). L'audit est maintenant fait ; modifier le code est l'étape suivante logique, mais c'est toi qui décides si on sort du mode « replay intouché ».

Si tu veux, je peux implémenter le lot A directement — dis-moi si je dois d'abord initialiser un dépôt git dans le projet pour garder un historique propre des changements (il n'y en a pas actuellement, et je le recommande fortement avant de toucher quoi que ce soit).
