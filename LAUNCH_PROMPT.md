# 0rum — LAUNCH_PROMPT.md v2

> **Prompts de lancement pour Claude Code.**
> Deux modes : autonome (full build) et phase-by-phase (incrémental).
> Chaque prompt est auto-suffisant — copier-coller directement dans Claude Code.

> **Statut 2026-05-20** : ce fichier est historique. Pour l'état courant, lire d'abord
> `CLAUDE.md`. La surface active est le dashboard web local, sans canal externe.
> IG est legacy/inactif. Dukascopy public `.bi5` est la source research/backtest
> XAUUSD validée. Binance/PAXG reste un proxy de plomberie runtime, pas une source
> de validation stratégique.

---

## Mode 1 : Full Build Autonome

> **Utilisation** : lancer une seule fois pour construire le projet de A à Z.
> Claude Code exécutera les 8 phases séquentiellement.

```
Tu es un développeur senior Python spécialisé en trading algorithmique.

Lis le fichier CLAUDE.md dans ce répertoire — c'est la spec technique complète du bot 0rum.

Construis le projet en suivant EXACTEMENT le build order en 8 phases décrit dans la section 18.

Règles impératives :
1. Respecte TOUTES les conventions de code (section 16) — async, structlog, Pydantic v2, SQLAlchemy 2.0
2. Respecte TOUS les anti-patterns à éviter (section 17) — en particulier max 3 paramètres optimisables par stratégie
3. Ne simplifie/raccourcis AUCUNE stratégie — implémente la logique complète de chaque stratégie
4. Utilise les schémas SQL EXACTS de la section 6
5. N'ajoute AUCUNE dépendance non listée dans la section 2 sans me demander
6. Chaque phase doit être fonctionnelle indépendamment — teste avant de passer à la suivante
7. Docker Compose doit fonctionner dès la Phase 1
8. Le mode d'exécution dual (signal/auto) est critique — section 13

Pour chaque phase terminée, confirme le livrable attendu (section 18) et montre-moi un test de validation.

Commence par la Phase 1 : Foundation.
```

---

## Mode 2 : Phase par Phase

> **Utilisation** : lancer chaque phase individuellement pour plus de contrôle.
> Copier-coller le prompt de la phase courante.

---

### Phase 1 : Foundation

```
Tu es un développeur senior Python. Lis CLAUDE.md dans ce répertoire.

Exécute la Phase 1 — Foundation :

1. Crée le docker-compose.yml (section 20) avec postgres, redis, app
2. Crée le Dockerfile (Python 3.12, pip install)
3. Crée pyproject.toml avec toutes les dépendances (section 2)
4. Crée src/config.py avec Pydantic Settings (section 5)
5. Crée src/database.py avec async engine + sessionmaker (SQLAlchemy 2.0 async)
6. Crée TOUS les modèles ORM (section 6) dans src/models/
7. Configure Alembic pour les migrations
8. Crée src/main.py avec FastAPI app + /health endpoint (section 14.2)
9. Crée .env.example

Livrable attendu : `docker compose up` démarre sans erreur, 
GET /health retourne le JSON de la section 14.2.

Conventions : async everywhere, structlog, Pydantic v2, SQLAlchemy 2.0 mapped_column.
Ne crée PAS les dossiers vides — seulement les fichiers nécessaires pour cette phase.
```

---

### Phase 2 : Data Ingestion

```
Tu es un développeur senior Python. Lis CLAUDE.md dans ce répertoire.

Exécute la Phase 2 — Data Ingestion :

1. Crée src/ingestion/market_client.py (section 8.1)
   - Client async pour le provider de marché retenu
   - Garder Binance/PAXG comme proxy de plomberie runtime si nécessaire
   - Ne pas réactiver IG sans décision explicite
   - Pour la recherche/backtest XAUUSD, utiliser Dukascopy public `.bi5`
   - Garder le mapping interne des timeframes M15 / H1 / H4 / D1
   - Rate limiting respecté côté provider
2. Crée src/ingestion/candle_fetcher.py (section 8.2)
   - Fetch les 4 timeframes : M15, H1, H4, D1
   - Upsert (ON CONFLICT DO NOTHING)
   - Backfill automatique au startup (6 mois)
3. Crée src/ingestion/gap_detector.py (section 8.3)
   - Scanner les trous temporels après chaque fetch
   - Backfill automatique
   - Prune les candles complete=False > 24h
4. Crée src/scheduler/jobs.py
   - APScheduler avec les jobs de fetch (section 15) :
     M15=15min, H1=1h, H4=4h, D1=daily 00:05 UTC
   - Job gap detection = 1h
   - Job prune = daily
5. Intègre le scheduler dans main.py

Livrable attendu : les candles s'accumulent en DB sur les 4 timeframes, 
pas de gap, structlog montre chaque fetch.

Teste avec `docker compose up` et vérifie les données en DB.
```

---

### Phase 3 : Strategy Engine

```
Tu es un développeur senior Python. Lis CLAUDE.md dans ce répertoire.

Exécute la Phase 3 — Strategy Engine :

1. Crée src/strategies/base.py — AbstractStrategy (section 9.1)
   - PARAM_RANGES déclaré par chaque stratégie
   - Helpers : calculate_atr(), detect_swing_levels()
   - detect_swing_levels utilise scipy.signal.argrelextrema avec order=10 FIXÉ

2. Crée src/strategies/liquidity_sweep.py — (section 9.2)
   - 3 params : sweep_atr_mult, sl_atr_mult, tp_risk_mult
   - FIXÉS : order=10, TP2=2×TP1, pas de filtre volume
   - Logique complète des 7 étapes

3. Crée src/strategies/trend_continuation.py — (section 9.3)
   - 3 params : pullback_ema, sl_atr_mult, tp_risk_mult
   - FIXÉS : EMA(50)>EMA(200) trend filter, price action trigger
   - Logique complète des 7 étapes

4. Crée src/strategies/breakout_expansion.py — (section 9.4)
   - 3 params : squeeze_lookback, volume_mult, sl_atr_mult
   - FIXÉS : TP=1×range width, pas de RSI
   - Logique complète des 6 étapes

5. Crée src/strategies/ema_momentum.py — (section 9.5)
   - 3 params : fast_ema, slow_ema, sl_atr_mult
   - FIXÉS : TP=1.5×risk, filtre EMA(50), pas de MACD
   - Logique complète des 6 étapes

6. Crée les tests unitaires dans tests/test_strategies/

IMPORTANT : chaque stratégie = EXACTEMENT 3 paramètres optimisables, pas plus.
Les paramètres structurels (EMA 50, EMA 200, order=10) sont HARDCODÉS, 
pas dans PARAM_RANGES.

Livrable attendu : chaque stratégie génère des CandidateSignals 
à partir de candles réelles en DB.
```

---

### Phase 4 : Signal Pipeline

```
Tu es un développeur senior Python. Lis CLAUDE.md dans ce répertoire.

Exécute la Phase 4 — Signal Pipeline :

1. Crée src/pipeline/dedup.py (section 10.1)
   - Fenêtre 60min, même stratégie + direction + entry ±0.1%
   - Le plus récent gagne

2. Crée src/pipeline/conflict_filter.py (section 10.2)
   - Long vs Short simultanés → garder la meilleure confidence

3. Crée src/pipeline/ranker.py (section 10.3)
   - Score composite : confidence×0.40 + RR×0.30 + WFE×0.20 + regime×0.10

4. Crée src/pipeline/quota.py (section 10.4)
   - Max 5 approved/jour (UTC)

5. Intègre le pipeline dans le scheduler (run après les stratégies)

6. Crée les tests dans tests/test_pipeline/

Livrable attendu : CandidateSignals → ApprovedSignals avec tous les filtres,
vérifié par les tests unitaires.
```

---

### Phase 5 : Backtesting & Validation

```
Tu es un développeur senior Python. Lis CLAUDE.md dans ce répertoire.

Exécute la Phase 5 — Backtesting & Validation :

1. Crée src/backtesting/optimizer.py
   - Latin Hypercube Sampling : 100 combos par stratégie
   - Optimise sur les PARAM_RANGES de chaque stratégie (3 params max)

2. Crée src/backtesting/walk_forward.py (section 11.1)
   - Train = 6 mois, Test = 2 mois
   - Top 5 IS combos → test OOS
   - Multi-window : 3 fenêtres de 20 jours, profitable dans ≥ 2/3
   - WFE = OOS_score / IS_score → minimum 50%
   - Composite score (section 11.2) : PF×0.30 + Sharpe×0.25 + WR×0.25 + (1-DD)×0.20

3. Crée src/backtesting/monte_carlo.py (section 11.3)
   - 1000 simulations, shuffle trades
   - P95 drawdown ≤ 2× historique
   - P5 profit factor > 1.0

4. Crée src/backtesting/regime_detector.py (section 11.4)
   - 4 régimes : TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOL
   - ADX(14) + ATR percentile based
   - Stockage dans market_regimes

5. Intègre dans le scheduler :
   - Optimizer = 24h
   - Backtest validation = 8h
   - Regime detection = 4h

6. Crée les tests dans tests/test_backtesting/

CRITIQUE : les résultats sont stockés dans optimizer_results.
Seuls les params avec WFE > 50% sont activés (is_active = True).

Livrable attendu : les 4 stratégies ont des paramètres optimisés 
testés en OOS avec WFE > 50%.
```

---

### Phase 6 : Risk Management

```
Tu es un développeur senior Python. Lis CLAUDE.md dans ce répertoire.

Exécute la Phase 6 — Risk Management :

1. Crée src/risk/risk_gates.py (section 12.1)
   - Gate 1 : Daily loss limit → P&L jour ≤ -3% → REJECT
   - Gate 2 : Position limits → ≥ 5 open → REJECT
   - Gate 3 : Concentration → 4+ même direction → réduire taille 50%
   - Les 3 gates sont exécutées séquentiellement, toutes doivent passer

2. Crée src/risk/position_sizer.py (section 12.2)
   - ATR-based sizing
   - High vol (≥90th pctile) → -30%
   - Low vol (≤10th pctile) → +30%
   - Hard cap 2%
   - Formule : lots = risk_amount / (sl_distance × 100)

3. Crée src/risk/circuit_breaker.py (section 12.3)
   - Compteur Redis (consecutive stops)
   - 8 stops → 24h shutdown
   - Pas de close forcé des positions ouvertes
   - Reset au premier gain ou fin cooldown
   - État visible via `/health` et `/api/dashboard`

4. Crée les tests dans tests/test_risk/

Livrable attendu : aucun trade ne passe sans les 3 gates, 
le circuit breaker bloque après 8 stops consécutifs.
```

---

### Phase 7 : Execution Engine

```
Tu es un développeur senior Python. Lis CLAUDE.md dans ce répertoire.

Exécute la Phase 7 — Execution Engine :

1. Finalise le mode signal local (section 13.1)
   - Persiste les signaux approuvés
   - Expose les signaux dans `/api/dashboard`
   - Démarre le tracking théorique post-signal

2. Crée src/execution/broker_executor.py (section 13.2)
   - Place les ordres via le broker choisi
   - Partial close 50% à TP1
   - Trailing stop ATR-based après TP1 (section 13.2)
   - Trail = 1.0 × ATR(14) H1, ratchet only

3. Crée src/execution/executor.py (section 13.4)
   - ExecutionRouter : switch signal/auto selon EXECUTION_MODE
   - Mode signal → persistance locale + theoretical tracking
   - Mode auto → broker executor, sans couplage à un canal de notification

4. Intègre le tracking théorique en mode signal :
   - Surveille le prix post-signal
   - Enregistre TP1/TP2/SL théorique
   - Permet la comparaison hypothetical vs actual

5. Crée les tests dans tests/test_execution/

Livrable attendu : le bot est fonctionnel en mode signal avec tracking,
les signaux sont persistés et visibles dans le dashboard local.
```

---

### Phase 8 : Monitoring & Polish

```
Tu es un développeur senior Python. Lis CLAUDE.md dans ce répertoire.

Exécute la Phase 8 — Monitoring & Polish :

1. Finalise le dashboard web local
   - `/dashboard` affiche l'état opérateur
   - `/api/dashboard` expose health, signaux, trades, P&L, circuit breaker
   
2. Implémente le Daily Summary (section 14.3)
   - Envoyé à 00:00 UTC
   - Signaux, trades, P&L daily/MTD, circuit breaker status, mode

3. Enrichis /health (section 14.2)
   - Ajoute toutes les métriques listées dans le JSON de référence
   
4. Finalise le logging structuré
   - structlog partout, JSON format
   - Chaque action importante est loggée
   - Pas de print()

5. Docker production-ready
   - Multi-stage build dans le Dockerfile
   - Health check dans docker-compose
   - Restart policy : unless-stopped

6. Revue complète du code :
   - Vérifie que chaque stratégie a EXACTEMENT 3 paramètres optimisables
   - Vérifie que WFE minimum est 50%
   - Vérifie que le circuit breaker fonctionne
   - Vérifie le mode dual signal/auto
   - Vérifie les conventions de code (section 16)

Livrable attendu : bot complet, prêt pour le mode signal en production.
`docker compose up` et le bot expose `/dashboard`, `/api/dashboard`, et `/health`.
```

---

## Mode 3 : Vérification post-build

> **Utilisation** : après le full build ou après toutes les phases, 
> pour valider l'intégrité du système.

```
Tu es un auditeur technique. Lis CLAUDE.md dans ce répertoire et vérifie le code produit.

Checklist de vérification :

1. PARAMÈTRES
   - [ ] Chaque stratégie a EXACTEMENT 3 paramètres optimisables
   - [ ] Aucun paramètre structurel n'est dans PARAM_RANGES
   - [ ] Les ranges correspondent à ceux du CLAUDE.md
   - [ ] Les paramètres globaux sont tous FIXÉS dans Settings

2. WALK-FORWARD
   - [ ] Train = 6 mois, Test = 2 mois
   - [ ] LHS = 100 combos
   - [ ] WFE minimum = 50%
   - [ ] Multi-window : 3 fenêtres de 20 jours
   - [ ] Composite score correct (PF 0.30, Sharpe 0.25, WR 0.25, DD 0.20)

3. RISK
   - [ ] 3 gates exécutées séquentiellement
   - [ ] Daily loss limit = -3%
   - [ ] Circuit breaker = 8 stops → 24h
   - [ ] Hard cap risk = 2%
   - [ ] ATR sizing avec vol adjustment

4. EXECUTION MODES
   - [ ] Mode signal : dashboard local + theoretical tracking
   - [ ] Mode auto : broker natif + partial close + trailing
   - [ ] Switch via .env uniquement
   - [ ] Transition recommandée : 4 semaines minimum

5. CODE QUALITY
   - [ ] Async everywhere
   - [ ] structlog (pas de print)
   - [ ] Pydantic v2 (model_validate)
   - [ ] SQLAlchemy 2.0 (mapped_column)
   - [ ] Pas d'IA/ML
   - [ ] Pas de multi-asset
   - [ ] Docstrings Google style

6. PROVIDER / BROKER
   - [ ] Provider réel XAUUSD validé
   - [ ] Broker executor aligné avec le broker choisi
   - [ ] Auth / sessions / headers corrects
   - [ ] Rate limiting

Rapporte chaque anomalie trouvée avec le fichier et la ligne concernée.
```

---

## Quick Reference : Commandes utiles

```bash
# Démarrer le stack
docker compose up -d

# Voir les logs
docker compose logs -f app

# Accéder à la DB
docker compose exec postgres psql -U orum -d orum

# Vérifier les candles
SELECT timeframe, COUNT(*), MIN(timestamp), MAX(timestamp) 
FROM candles GROUP BY timeframe;

# Vérifier les signaux
SELECT strategy, status, COUNT(*) 
FROM candidate_signals 
GROUP BY strategy, status;

# Vérifier l'optimizer
SELECT strategy, wfe, is_active, created_at 
FROM optimizer_results 
ORDER BY created_at DESC LIMIT 10;

# Health check
curl http://localhost:8000/health

# Changer de mode
# Éditer .env : EXECUTION_MODE=auto
docker compose restart app
```

---

*Fin du LAUNCH_PROMPT.md v2 — chaque prompt est auto-suffisant et prêt à copier-coller dans Claude Code.*
