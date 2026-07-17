# Dashboard Event Pins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Renforcer la lecture des événements et raccorder sans ambiguïté l'historique réel au scénario central futur.

**Architecture:** `renderTimelineEvents` produit les pins, tiges, points de timeline et extrémités de trades. `renderMarketTerminal` produit la séparation historique/futur et le segment de raccord au premier point du scénario central. Le CSS fixe les épaisseurs et contrastes.

**Tech Stack:** JavaScript natif, SVG, CSS, pytest, Node syntax check.

---

### Task 1: Contrat visuel en échec

**Files:**
- Modify: `tests/test_dashboard_terminal.py`

- [ ] Exiger les classes `event-timeline-dot`, `event-halo`, `trade-endpoint`, `history-future-divider` et `scenario-join`.
- [ ] Exécuter `pytest tests/test_dashboard_terminal.py -q -p no:cacheprovider`.
- [ ] Vérifier que le test échoue parce que ces couches n'existent pas.

### Task 2: Pins et raccord SVG

**Files:**
- Modify: `orum/static/dashboard.js`
- Modify: `orum/static/dashboard.css`

- [ ] Ajouter le halo, le pin, la tige et le point de timeline à chaque événement.
- [ ] Ajouter les points d'extrémité à chaque liaison de trade.
- [ ] Ajouter une séparation à `fanX(0)` et un segment entre la dernière bougie et `central[0]`.
- [ ] Augmenter les contrastes sans modifier les données.

### Task 3: Vérification et activation

**Files:**
- Verify: `orum/static/dashboard.js`
- Verify: `orum/static/dashboard.css`

- [ ] Exécuter le test ciblé puis la suite complète.
- [ ] Exécuter `node --check orum/static/dashboard.js` et `git diff --check`.
- [ ] Contrôler zoom, ouverture de carte et rendu réel dans le navigateur.
- [ ] Redémarrer uniquement `com.0rum.dashboard` et vérifier `127.0.0.1:8787`.

