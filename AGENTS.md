> ✅ **C'EST ICI LE BOT LIVE** (2026-07-06) : dashboard 127.0.0.1:8787, LaunchAgent com.0rum.dashboard.
> Toute modification du bot de trading se fait DANS CE REPO (branche strategy/backtest-parity).
> NE JAMAIS redémarrer les process live (worker/watcher/dashboard/producer) sans confirmation explicite de l'utilisateur.
> Après toute modification opérationnelle autorisée, recharger immédiatement le composant paper concerné dans le même passage et vérifier son état réellement chargé (configuration, heartbeat, code de sortie et erreurs). Ne jamais remettre l'activation à plus tard sans présenter explicitement le blocage à l'utilisateur. Cette règle n'autorise aucun ordre broker/live.
> Ne pas confondre avec ~/Documents/00-code/0rum (moteur de recherche, Docker :8008).

## Analyse d'impact et vérification

- Utiliser en priorité le graphe local `codebase-memory-mcp` pour découvrir les symboles, lire leur code et tracer leurs appelants; utiliser `rg` pour les chaînes, configurations et fichiers non indexés.
- Avant de modifier un symbole de trading, risque, exécution, persistance ou scheduler, analyser ses appelants directs et les flux concernés. Prévenir l'utilisateur avant de poursuivre si l'impact est élevé ou critique.
- Avant de terminer, examiner le diff complet, vérifier qu'il ne touche que la portée demandée et exécuter les tests ciblés couvrant les appelants affectés.
- Ne jamais ignorer un appelant direct cassé, un test en échec, une dérive d'état persisté ou un risque de double exécution.

## Contrat paper après audit du 5 septembre 2026

- Portefeuille principal : `observed_mark` (ADR-018), cycle 300 s; COT dans un service séparé. `next_open` est réservé à un replay aligné sans retour avant les événements du compte.
- Compte LLM v2 : récupérer les outboxes engagées avant validation; ne jamais restaurer un ancien compte par-dessus des fills. Toute divergence legacy échoue explicitement.
- `max_leverage` reste par tranche; exposition agrégée affichée séparément. BTC 1× et DynamicRiskShadow observationnel restent inchangés.
- Calendriers Nasdaq/CFTC vérifiés pour 2026 seulement; les réviser avant 2027. Les résultats v1, transition et v2 ne constituent pas une série de validation homogène.

## Comptes de recherche par stratégie (ADR-019)

- `scripts/run_strategy_accounts.py` : huit comptes virtuels natifs et un témoin partagé, 10 000 USD chacun, état exclusif sous `state/strategy_accounts/v1`, service 300 s.
- Les neuf comptes ont un risque nominal sans DDscale/kill, mais gardent les autres plafonds/sorties/frais. Le principal et les LLM conservent leurs politiques distinctes. Ne pas sommer les capitaux fictifs pour afficher un rendement comparable à un seul compte.
- Configuration figée à l'initialisation ; bougies clôturées et COT archivés par cycle avant exécution. Reprise sur les mêmes entrées, outbox distincte par compte. Aucun reset implicite des comptes.
- Une dérive de code reste signalée comme rupture méthodologique sans bloquer les sorties. Ne pas présenter une série mélangeant plusieurs méthodes comme une validation homogène.

## Evolving lesson hypotheses v2 (2026-09-05)

`llm_evolving` uses the controlled qualitative catalog in `orum/llm/lesson_rules.py` (ADR-020). Active means repeated paper hypothesis, not profitability validation. Unclassified observations are never injected. Preserve original observations, distinct support IDs, per-support expiry, rejection and migration aliases. Never apply lessons to `llm_reference`. The `/bot` surface must distinguish journal state, current eligibility, provided versions and model citations. Do not collapse supplied/cited into causal effectiveness.

## Consolidation du 9 septembre 2026 (ADR-021)

- `state/portfolio.yaml` est la configuration opérateur complète ; `config/portfolio.yaml` est le secours complet si elle manque, sans fusion par clé. Préserver les différences volontaires. `--dry` affiche source et empreinte d'une lecture unique ; `--once` relit à chaque invocation, `--loop` conserve sa configuration.
- Les anciens lanceurs mono-actifs et `scripts/0rum start/stop/restart` sont retirés et refusent toute action. Les modules restent disponibles aux imports/replays ; ne pas les réactiver par nettoyage ou erreur de routage.
- Chaque expérience reste séparée ; tout retrait traite explicitement positions et preuves. Toute nouvelle expérience définit question, témoin, coût, budget, critère d'utilité et règle d'arrêt (voir `docs/OPERATING-MODES.md`).
- Après publication, vérifier un nouveau processus/cycle démarré après les remplacements. Un hash du disque ne prouve pas le code précédemment importé. Préserver les indicateurs de changement de méthode des comptes de recherche.
