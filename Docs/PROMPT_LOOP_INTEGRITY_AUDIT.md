# Prompt pour Claude Code (Fable) : Audit d'Intégrité des Boucles de 0rum (Decay + Blindness Upward)

## Contexte

Une revue externe (carrousel @vyzual.ai sur les systèmes d'auto-amélioration) a servi de grille
de lecture pour ré-auditer l'architecture de gouvernance de 0rum, déjà actée dans
`Docs/decisions/ADR-002-lab-to-bot-promotion-contract.md`. Le pattern décrit — Lab (challenger)
vs bot live (incumbent), gate à 10 critères figés, paper-forward = held-out eval, sign-off humain
obligatoire — **correspond déjà** à ce que fait 0rum. Ce n'est donc pas une reconception : c'est un
audit ciblé sur les 2 angles morts que la grille met en évidence et que l'ADR-002 ne couvre pas :

1. **Decay (dégradation silencieuse du monitoring)** : un item déjà connu et non résolu — l'alerte
   `model_error` dans `orum/dashboard.py` (~ligne 446-447) ne vérifie que le **dernier cycle**.
   Un cycle isolé en erreur suivi d'un cycle "sain" fait disparaître l'alerte, alors qu'une
   instabilité API récurrente (mais pas systématique) resterait invisible sur le dashboard —
   exactement le pattern "sensors drift, definitions shift, the dashboard stays green".

2. **Blindness upward (le baseline n'est jamais remis en cause)** : l'ADR-002 gate rigoureusement
   tout **challenger** contre le champion actuel (AK MACD 4h long-only, PF 1.53 full / 2.02 OOS),
   mais rien ne re-valide périodiquement le **champion lui-même** sur des données fraîches. Un
   champion qui a cessé de fonctionner (régime de marché changé) continuerait de tourner en live
   tant qu'aucun challenger ne le bat explicitement — il n'y a pas de boucle qui demande "est-ce
   que je devrais encore être le champion ?" indépendamment de la compétition.

Les deux autres failure modes de la grille (**conflict** entre boucles, **shape vs anchors**) sont
déjà couverts par l'ADR-002 (§4 : toute boucle "lente" — LLM, contexte — qui voudrait influencer
entrées/sorties doit repasser par le gate complet ; §Observability : anti-pattern du gauge sur
candidat non figé déjà documenté). **Ne pas les retraiter.**

## Portée et routage

- Le bot live tourne dans `.sandbox/0rum-one-shot-home/0rum-trading/` (LaunchAgent
  `com.0rum.dashboard`, 127.0.0.1:8787). C'est là que vivent `orum/dashboard.py`, `orum/loop.py`,
  `orum/llm/paper_agent.py`.
- **Aucun process live (worker, watcher, dashboard, producer) ne doit être redémarré sans
  confirmation explicite de l'opérateur**, même si le code est prêt.
- Ce chantier est strictement **observabilité / gouvernance** — zéro changement à la logique
  d'entrée/sortie du champion, zéro promotion automatique. Conforme à l'esprit ADR-002 : le Lab
  et l'audit proposent, l'humain dispose.

## Mission

### Étape 1 — Analyse (lecture seule)
- Lire `orum/dashboard.py` autour de l'alerte `model_error` (et `_guardrail_status` pour le
  pattern d'alerte existant à réutiliser).
- Lire `orum/loop.py` et `ak_macd_producer.py` pour comprendre le logging `shadow_disagreement`
  déjà en place (mécanisme de comparaison live/shadow existant, à ne pas dupliquer).
- Lire `ADR-002` en entier pour le vocabulaire des gates (G1-G10) et le format du registre
  champion/challenger, afin que tout ajout reste cohérent avec ce contrat.

### Étape 2 — Plan (Design Document, ne pas coder encore)

Proposer, pour chacun des deux angles morts, un plan d'implémentation :

**A. Durcir l'alerte de decay**
- Remplacer la vérification "dernier cycle uniquement" par une fenêtre glissante (ex : N derniers
  cycles) avec un seuil de fréquence d'erreur (pas juste présence/absence).
- Distinguer explicitement "erreur ponctuelle" (transitoire, tolérée) de "dégradation récurrente"
  (alerte persistante tant que non acquittée).
- Garder le format d'alerte existant (`{"kind": ..., "level": ..., "message": ...}`) pour ne pas
  casser le rendu dashboard actuel.

**B. Boucle de ré-audit périodique du champion**
- Définir une cadence (ex : mensuelle, ou tous les N nouveaux trades clos) à laquelle le champion
  actuel repasse **lui-même** un sous-ensemble des gates ADR-002 pertinents sur la fenêtre de
  données la plus récente (G1 raisonné en auto-comparaison vs sa propre baseline historique, G8
  per-year consistency, G2 walk-forward).
- Sortie : un statut lisible ("champion toujours conforme à son propre dossier de preuve" /
  "dérive détectée — dossier à ré-ouvrir"), jamais une action automatique. Aucune promotion,
  aucun arrêt automatique du bot — seulement un signal humain-actionnable, dans le même esprit que
  la registry champion/challenger déjà prévue en §3.1 de l'ADR-002.
- Préciser où stocker ce statut (probable : à côté de `state/candidate_status.json`, format
  analogue) et comment le dashboard le surface (nouvelle puce à côté de `_guardrail_status`, pas
  un nouveau système parallèle).

### Contraintes absolues
- Lecture/alerting seulement : aucune modification de la logique de trading, d'exécution, ou de
  promotion automatique.
- Ne pas toucher aux processus live en cours sans confirmation explicite.
- Rester un **addendum** à l'ADR-002 (même vocabulaire, mêmes formats de fichiers d'état), pas une
  nouvelle architecture parallèle.
- Produire le Design Document (fichiers touchés, formats de données, emplacement des nouveaux
  signaux) avant d'écrire la moindre ligne de code, et attendre validation humaine avant
  d'implémenter.
