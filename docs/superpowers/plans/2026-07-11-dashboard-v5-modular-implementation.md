# Dashboard V5 Modulaire Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer les graphiques hérités par trois cartes V5 modulaires, proportionnelles et zoomables, avec les événements de trading explicitement reliés.

**Architecture:** Un seul renderer `renderMarketTerminal(asset, state, card)` dessine n'importe quel marché dans sa carte GridStack. Un état de vue par actif conserve le nombre de bougies et la visibilité ; GridStack gère dimensions et position, puis déclenche un nouveau calcul géométrique au resize.

**Tech Stack:** HTML, CSS, JavaScript natif, SVG, GridStack 10, pytest pour les contrats statiques, Node pour la syntaxe.

---

### Task 1: Contrat DOM modulaire

**Files:**
- Modify: `tests/test_dashboard_terminal.py`
- Modify: `orum/static/dashboard.html`

- [ ] Ajouter un test qui exige `market-btc-card`, `market-eth-card`, `market-paxg-card`, les boutons de visibilité et interdit `price-card`.
- [ ] Exécuter `pytest tests/test_dashboard_terminal.py -q -p no:cacheprovider` et constater l'échec sur l'ancien DOM.
- [ ] Remplacer la carte terminal hors grille et les anciens graphiques par trois cartes GridStack V5.
- [ ] Relancer le test et vérifier la partie DOM.

### Task 2: Rendu proportionnel et zoom interne

**Files:**
- Modify: `tests/test_dashboard_terminal.py`
- Modify: `orum/static/dashboard.js`
- Modify: `orum/static/dashboard.css`

- [ ] Ajouter des assertions exigeant `renderMarketTerminal`, `terminalZoom`, `ResizeObserver`, `vector-effect: non-scaling-stroke` et interdisant `preserveAspectRatio="none"` dans le renderer V5.
- [ ] Exécuter le test et constater l'échec attendu.
- [ ] Extraire le renderer paramétré par actif et calculer sa géométrie depuis `clientWidth/clientHeight`.
- [ ] Ajouter les boutons −/+/1:1, la molette et une fenêtre de 36 à 220 bougies.
- [ ] Relancer les tests et `node --check orum/static/dashboard.js`.

### Task 3: Événements et modularité GridStack

**Files:**
- Modify: `tests/test_dashboard_terminal.py`
- Modify: `orum/static/dashboard.js`
- Modify: `orum/static/dashboard.css`

- [ ] Exiger dans le test les couches `event-stem`, `trade-link`, `terminal-close` et le stockage des cartes visibles.
- [ ] Faire échouer le test avant implémentation.
- [ ] Rendre les tiges IN/TP/SL et les liaisons entrée→sortie dans chaque carte.
- [ ] Connecter fermeture, réouverture, persistence et `resizestop` à un nouveau rendu.
- [ ] Relancer le test ciblé.

### Task 4: Vérification et activation

**Files:**
- Verify: `orum/static/dashboard.html`
- Verify: `orum/static/dashboard.css`
- Verify: `orum/static/dashboard.js`

- [ ] Exécuter `git diff --check`.
- [ ] Exécuter `node --check orum/static/dashboard.js`.
- [ ] Exécuter `pytest -q -p no:cacheprovider` et attendre zéro échec.
- [ ] Lancer GitNexus `detect_changes` et contrôler que les flux dashboard attendus sont seuls concernés par cette correction.
- [ ] Redémarrer uniquement `com.0rum.dashboard`.
- [ ] Vérifier `/api/state`, les trois cartes, le zoom, le resize, fermeture/réouverture et l'absence d'erreurs console sur `127.0.0.1:8787`.

