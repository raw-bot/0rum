# ADR-018 — Exécution paper observable et reprise comptable v2

Statut : accepté par l’utilisateur le 5 septembre 2026. Activation à tracer dans le dossier d’audit.

## Contexte

L’audit 8787 a trouvé des prix d’entrée rétroactifs, des premières barres non protégées, des erreurs secondaires qui interrompaient les sorties et une fenêtre de crash entre journal LLM et compte. Les métriques historiques utilisant ces conventions ne valident pas la méthode corrigée.

## Décision

Le portefeuille principal utilise `observed_mark_v2` : signal sur bougie close, entrée au dernier close de monitor effectivement observé, timestamp du cycle. `signal_candle_ts`, `signal_available_ts`, `decision_observed_at` et `price_asof_ts` décrivent les temps distincts. Le close utilisé reste un prix paper retardé, pas une cotation négociable garantie. La barre déjà fermée lors de l’entrée ne peut pas déclencher rétroactivement sa protection. Les barres suivantes sont surveillées dans l’ordre.

Les stops franchis à l’ouverture prennent le prix d’ouverture; un target franchi à l’ouverture précède un stop touché plus tard. Sans ordre intrabar connu, une collision stop/target est stop-first. Les sorties récupérées après plusieurs barres utilisent le dernier prix disponible selon la convention existante, avec `recovered_after_gap` visible. `next_open_v2` reste un modèle de replay à données complètes et frontières alignées; il refuse les décisions antérieures à `Account.last_event_ts`. Un gap déjà connu est liquidé avant d’allouer le nouveau risque. Le repricer protège aussi la première barre et inclut les deux frais.

Les protections précèdent les observations de risque et l’apprentissage LLM. Après un échec de monitor non confirmé, les bougies suivantes de la même lane attendent la reprise; les autres lanes continuent. Un curseur dynamique ne dépasse pas sa première évaluation échouée. Les données d’un indicateur ou observateur ne conditionnent pas la disponibilité de la protection.

Le compte LLM v2 porte `pending_fills`. Le remplacement atomique du compte engage la transaction; publication JSONL idempotente puis vidage de l’outbox. Toutes les outboxes engagées sont récupérées avant de valider un ledger legacy partagé. Une queue partielle n’est réparée que si elle correspond au préfixe d’un fill engagé, et ses octets sont archivés. La migration v1 refuse les divergences de solde, quantités, décisions ou curseurs, ainsi qu’un compte absent avec des fills existants. Les nouvelles décisions périmées restent rejetées. Un apprentissage sans couverture chronologique complète reste en attente, sans outcome présenté comme complet.

NVDA utilise le calendrier Nasdaq 2026, les demi-séances et America/New_York. La sortie devient disponible quinze minutes avant fermeture; la collecte paper passe à 300 secondes. Un premier reversal M5 déjà manqué est signalé et consommé, sans entrée tardive. Une sortie manquée persiste jusqu’à un mark de la séance courante. L’absence de calendrier vérifié pour une autre année dégrade NVDA seul. Réviser les calendriers avant 2027.

COT reste hors de la boucle paper : un service séparé actualise l’année courante, cache six heures; l’historique consolidé est réutilisé. L’âge économique maximal est dix jours. Publication officielle à 15:30 New York, bornée par l’as-of du signal. Les jours différés sont explicites. Une erreur conserve le gate antérieur et publie un statut dégradé.

`max_leverage` conserve sa sémantique par tranche. L’exposition agrégée est affichée séparément. Le risque demandé, autorisé et effectivement exécuté sont distingués. BTC reste limité à une position et un notionnel 1× equity. `DynamicRiskShadow` reste observationnel, artefact non éligible à promotion. Aucune hausse de risque ou exécution broker n’est autorisée par cette décision.

La santé vient du dernier cycle et de ses erreurs; une equity fraîche ne prouve pas un cycle réussi. Les statistiques de trades soustraient les frais d’entrée et de sortie. Le dashboard sépare les sources retirées, signale les données partielles et les différentes méthodes. Les épisodes ouverts en v1 et fermés en v2 sont identifiés comme transition.

## Validation et limites

Les tests utilisent des données synthétiques et un runner qui interdit réseau, processus enfants et accès à l’état opérationnel. Une copie récente des comptes est réconciliée sans modifier les originaux. Le replay de contrôle produit 9 798,10 USD après le stop de première barre, contre 10 197,90 avec l’ancien biais. Ce résultat valide une convention comptable, pas une rentabilité.

Une étude prospective doit commencer à l’activation, avec source/config/artefact/coûts gelés et observations futures séparées de celles ayant servi aux corrections. Conserver les coûts paper actuels (0,1 % aller-retour), examiner séparément slippage 0/2/5/10 bps et gaps. Ne pas optimiser sur la fenêtre prospective; toute modification ouvre une nouvelle version. Une promotion de sizing ou de levier requiert une validation distincte incluant marge/liquidation et coûts. Les données futures ne sont pas encore acquises.

## Activation et retour arrière

Avant application : vérifier les empreintes du code de départ, conserver code/config/plists et état/journaux horodatés; attendre les cycles en cours. Recharger uniquement paper, dashboard, LLM paper et watchdog, et le service COT séparé. Vérifier les services chargés, source/config du cycle, heartbeat, exit status, erreurs, comptes et outboxes. Le worker legacy reste retiré.

Ne jamais restaurer un ancien compte par-dessus des fills nouveaux. Le rollback conserve états/journaux; réconcilier et vider les outboxes avec du code v2 compatible avant tout retour à une version antérieure. En cas de divergence ou de corruption, conserver toutes les preuves et bloquer l’écriture concernée.

## Sources

- [Calendrier officiel Nasdaq](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule), vérifié le 5 septembre 2026.
- [Calendrier officiel CFTC](https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm), vérifié le 5 septembre 2026.
- ADR-014 (NVDA), ADR-016 (préservation du capital), ADR-017 (risque shadow) restent applicables hors conventions explicitement changées ci-dessus.
