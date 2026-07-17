# Runbook — test 1 semaine sans intervention (démarré 2026-07-06)

Objectif : laisser tourner le paper 4h long-only 7 jours SANS y toucher, avec
surveillance automatique. Tout est sous launchd (survit crash/logout/reboot).

## Ce qui tourne (launchd, `launchctl list | grep 0rum`)

| Label | Rôle | Cadence |
|---|---|---|
| com.0rum.engine | worker + watcher + **producteur local --live** (via scripts/run_engine.sh, AK_MACD_LIVE=1) | continu, KeepAlive |
| com.0rum.dashboard | console ops http://127.0.0.1:8787 | continu, KeepAlive |
| com.0rum.portfolio-shadow | paper 3 moteurs recherche (donchian BTC/ETH + gold COT) | horaire |
| com.0rum.watchdog | scripts/watchdog_heartbeat.sh — heartbeat<15min, producteur<5h, processus présents, shadow exit 0, disque>2G | 5 min |
| com.0rum.parity | scripts/parity_check_auto.py — producteur live vs code labo (BUY manqués/fantômes, trous de barres) | quotidien 08:30 |
| com.0rum.caffeinate | anti-sleep (idle+system, sur secteur) | continu |

TradingView Desktop : **PAS nécessaire**. Le bot calcule ses signaux lui-même
(Binance). TV ne sert qu'à REGARDER le miroir (0rum Mirror — AK MACD 4h,
parité 35/35 vérifiée le 2026-07-06). Tu peux fermer TV toute la semaine.

## Alertes (notifications macOS, son Basso)

- « 0rum WATCHDOG » : un signe vital est tombé (détail dans la notif ;
  historique : state/watchdog_alerts.jsonl). Anti-spam 30 min.
- « 0rum PARITY » : le producteur live a divergé du code validé
  (state/parity_check.json + parity_alerts.jsonl).
- Pas de notification = tout va bien. Vérif manuelle en 10 s :
  `cat state/watchdog_status.json state/parity_check.json`

## Kill-criteria (déjà armés, goal.yaml + portfolio_shadow)

Inchangés : série de pertes > 9, trade < −2.7R, DD portefeuille > 60% → coupe.
Le worker est superviseur de risque ; position actuelle : aucune à l'ouverture
du test.

## En cas de pépin (SEULES interventions autorisées pendant le test)

- Redémarrer un composant : `launchctl kickstart -k gui/$(id -u)/com.0rum.<label>`
- Tout arrêter VRAIMENT : `launchctl unload ~/Library/LaunchAgents/com.0rum.engine.plist`
  (le bouton Stop du dashboard ne suffit plus : KeepAlive relancerait l'engine).
- Ne PAS lancer scripts/run_local_4h.sh ni run_engine.sh à la main : ça
  créerait un 2e superviseur (cause de l'incident doublons du 2026-07-06 —
  l'ancien run_local_4h.sh détaché de jeudi ressuscitait les processus tués).

## Leçons câblées aujourd'hui

- run_engine.sh pointait encore sur le bridge TV/CDP → remplacé par le
  producteur local (sinon un reboot aurait ressuscité la dépendance TV).
- launchd n'a pas le PATH utilisateur → tous les plists portent
  PATH=~/.local/bin:… (cause de l'échec exit 127 du portfolio-shadow depuis le 5/07).
- Un seul superviseur à la fois : launchd est le patron, plus de nohup manuel.

## À la fin de la semaine

1. `uv run python scripts/parity_check_auto.py` (dernier contrôle)
2. Bilan : state/trades.jsonl + ak_macd_local_shadow.jsonl (verdicts par barre)
   + portfolio_shadow.jsonl (3 moteurs) vs sim (portfolio_sim_v2_result.txt)
3. Décision VPS/Docker (la vraie solution 24/7, déjà au plan).
