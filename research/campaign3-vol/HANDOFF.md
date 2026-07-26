# HANDOFF — Campagne 3, candidate A (drift post-earnings × erreur d'ampleur options)

Document de passation pour tout modèle/session reprenant ce travail. Lu conjointement avec `PREREG_A.md` (le contrat), `PHASE3_CONTRADICTION.md` (pourquoi chaque choix), `AUDIT_LSE.md` et `gate_edgar_results.json`.

## Où on en est (2026-07-26)

- Boucle de contradiction GPT-5.6 close (3/3) : verdict TESTABLE EN L'ÉTAT. Pré-enregistrement committé AVANT tout calcul (`00e6b17`).
- Gate EDGAR : **PASSÉ** (13 549 événements 8-K 2.02, couverture 94 %, ~540 éligibles/an).
- **Moisson dev EN COURS** : `dev_harvest.py` (v2, découpage adaptatif) tourne en détaché ; logs dans `dev_harvest.log`, fichiers dans `data/options_dev/`, manifeste `HARVEST_MANIFEST.csv`. Durée attendue : 2-3 semaines (quotas LSE).
- Pipeline d'analyse : À CONSTRUIRE (voir §Tâches).

## RÈGLES DURES — aucune exception

1. **`PREREG_A.md` est immuable.** Aucune modification, aucune « précision », aucun ajustement de seuil, fenêtre, horizon ou critère. Si quelque chose semble ambigu ou impossible : STOP, escalade (voir §Escalade). Les seuls paramètres réglables en dev sont explicitement listés dans le prereg.
2. **Ne jamais toucher aux données de confirmation** (période 2020-07-01+ pour les options ; ne pas les moissonner, ne pas les lire) avant que le développement ait PASSÉ ses critères d'abandon et qu'un verdict de dev soit committé.
3. **La clé LSE est dans `~/.config/0rum/lse.key`** — ne jamais la committer, ne jamais l'écrire dans un fichier du repo ou un log.
4. Un run d'analyse = un run. Pas de relance « pour vérifier » après avoir vu les résultats, pas de deuxième spec. Les bugs de code se corrigent et se relancent ; les choix de spec, jamais.
5. Chaque étape significative : commit sur la branche locale + cherry-pick vers `research/edge-campaigns` + push origin (pattern établi, voir git log).
6. Rapporter les échecs tels quels. Une conclusion négative est un résultat valide et attendu (3 campagnes : 28 hypothèses, 0 edge à ce jour).

## Tâches déléguables (Sonnet OK)

- **Suivi de moisson** : `tail dev_harvest.log`, compter `ls data/options_dev/*.parquet`, vérifier `pgrep -f dev_harvest.py`. Si arrêté (reboot…) : relancer `nohup python3 dev_harvest.py > dev_harvest.log 2>&1 &` — il est resumable (saute les chunks existants).
- **Construction du pipeline de dev** (`pipeline_dev.py`) contre le prereg, SANS l'exécuter sur les outcomes : ingestion parquets → straddles ATM T−1 (3 estimateurs du prereg) → m_e à deux échéances → u → éligibilité EDGAR (`edgar_events.json`, marge 20 min, jamais `filing date`) → gaps anormaux (candles LSE synchrones) → appariement caliper → portefeuille → critères d'abandon. Tests unitaires sur données synthétiques bienvenus.
- **Modèle de coûts et δ** : formule f(prix, ADV, vol, |gap|) calibrée sur références EXTERNES aux outcomes (spreads typiques par bucket ADV), δ = 5× l'aller-retour à 10 k€ — calculé et committé AVANT le premier run de dev.
- Debugging, refactors du code d'infra, mises à jour de ce fichier.

## Escalade OBLIGATOIRE (Fable + utilisateur) — ne pas franchir seul

1. **Gel du pipeline avant le premier run de dev** : revue ligne à ligne contre `PREREG_A.md` (classe d'erreurs déjà vue : critère placebo en ET au lieu de OU en campagne 2 ; « 25Δ » violant la contrainte model-free en campagne 3 — c'est du jugement, pas du code).
2. **Verdict de développement** (lecture des critères d'abandon) et **verdict de confirmation**.
3. Toute ambiguïté du prereg découverte en implémentation.
4. Tout résultat borderline ou surprenant (trop beau inclus — surtout trop beau).
5. Toute nouvelle boucle de contradiction GPT-5.6 (l'utilisateur fait l'intermédiaire manuellement).

## Pièges connus (payés, ne pas repayer)

- **Exports LSE tronqués à 2 500 000 lignes** exactement, silencieusement — toujours vérifier `rows < 2.5M`, sinon découper la plage (v2 le fait).
- `est_bytes` à la soumission d'un export = estimation pré-filtrage (11 Go annoncés → 2 Mo réels) ; juger sur `bytes` du job ready.
- Format d'export : parquet/arrow uniquement (pas de CSV malgré le marketing).
- `options_flow` LSE = semaine glissante seulement ; l'historique profond passe par `/export` dataset=options.
- `option_candles` synchrone = contrats récents seulement.
- Quotas LSE : 5 exports/h, 16 Go/sem, 50 Go/mois — `GET /vault/usage` avec la clé ; le harvester gère.
- Timestamps Yahoo/CM/LSE : mélanges tz-naive/tz-aware = crash pandas (déjà vu deux fois).
- EDGAR : `acceptanceDateTime` (heure ET), JAMAIS le champ `filing date` (décalé au lendemain après 17h30 ET) ; les 13 non-mappés de l'univers sont des ETF (pas de 8-K, hors périmètre) ; ~47 % des événements sont déposés en séance → inéligibles par construction (perte de couverture documentée, pas un bug).
- FRED : instable en HTTP/2 → `curl --http1.1`.
- SEC : User-Agent déclaratif obligatoire, ~10 req/s max.

## Contexte des campagnes (pour calibrer le scepticisme)

C1 2026-07 : turn-of-month mort post-publication ; réversion post-deleveraging négative après coûts. C2 : stablecoins — gates passés mais placebo au 36e percentile = faux positif procédural documenté, d'où la règle « placebo apparié autonome ». Le pattern qui tue les candidates : breadth insuffisante, coûts, variables fragiles, et l'écart entre « beaucoup d'événements » et « beaucoup d'information indépendante ». La candidate A est la première à survivre au circuit complet — la probabilité d'un edge net reste inconnue et le protocole existe précisément pour que le verdict, quel qu'il soit, soit fiable.
