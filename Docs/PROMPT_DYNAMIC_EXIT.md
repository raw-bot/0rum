# Prompt pour Claude Code : Implémentation d'un Moteur de Gestion de Position Dynamique (Dynamic Exit)

## Contexte et Problème
Actuellement, 0rum souffre du syndrome "Set and Forget". Lorsqu'un trade est ouvert, l'architecture actuelle (via `paper_engine.py` et `external/orchestrator.py`) fixe un `take_profit_price` et un `stop_loss_price` statiques, sous forme d'ordres bracket. 

Le bot devient ensuite totalement "aveugle" aux nouvelles données du marché pour ce trade précis : il se contente d'attendre que le prix touche l'une de ces deux lignes (TP ou SL).

**Conséquence :** Si un trade accumule de gros gains latents (ex: x2 par rapport au risque) mais que le marché montre des signes techniques évidents de retournement avant d'atteindre le TP rigide (x3), le bot ne sécurise rien et laisse le prix s'effondrer jusqu'au Stop Loss. C'est une perte critique d'Edge mathématique.

## Objectif
Nous voulons ajouter une "intelligence de sortie" (Position Manager / Dynamic Exit Engine). Le bot doit pouvoir analyser en permanence les bougies entrantes **même lorsqu'un trade est déjà ouvert**, et être capable de déclencher une clôture anticipée (Market Close) si les indicateurs montrent que la thèse initiale est invalidée ou que le momentum s'est retourné, sécurisant ainsi les gains avant le crash.

## Architecture Actuelle (À analyser)
- **`orum/loop.py`** : La boucle principale tourne chaque minute pour évaluer les stratégies et chercher de nouvelles entrées, mais elle n'évalue pas dynamiquement la santé des positions ouvertes par rapport aux indicateurs.
- **`orum/portfolio/paper_engine.py`** et **`dsl/backtest.py`** : La sortie de position n'est gérée que par le croisement du prix (High/Low) avec les lignes dures `take_profit_price` ou `stop_loss_price`.

## Mission pour Claude Code

Ton objectif est de concevoir et planifier l'architecture d'un **Moteur de Gestion de Position Dynamique**.

### Étape 1 : Phase d'Analyse
- Analyse `loop.py`, `executor.py` et `paper_engine.py` (ou `orchestrator.py`).
- Identifie comment et où insérer une boucle de "monitoring des positions ouvertes" à chaque nouveau tick de données.

### Étape 2 : Planification du MVP (Minimum Viable Product)
Propose un plan d'implémentation (sans coder dans un premier temps) pour :
1. **Créer une interface d'évaluation des sorties** : Les stratégies (ex: `ak_macd.py` ou les stratégies DSL) doivent pouvoir définir une logique `evaluate_exit(position, candles)` qui retourne un booléen ou un signal `CLOSE`.
2. **Intégrer le moniteur dans la boucle** : Modifier la boucle principale ou le paper_engine pour qu'elle passe les bougies récentes à la fonction d'évaluation de sortie de la stratégie correspondante à la position active.
3. **Mécanisme d'exécution de la sortie** : Si `evaluate_exit` retourne `True`, envoyer un signal à l'exécuteur pour fermer la position au prix du marché actuel, avec une raison explicite (ex: `exit_reason: "dynamic_momentum_loss"`), contournant ainsi les ordres bracket d'origine.

### Contraintes absolues
- Cette modification ne doit pas casser les backtests existants qui reposent sur des TP/SL statiques. Le "Dynamic Exit" doit être une capacité optionnelle par stratégie.
- Le code doit rester résilient (gestion des crashs, redémarrage du bot avec une position déjà ouverte).
- Produis un **Plan d'Implémentation (Design Document)** détaillant les fichiers touchés et la logique avant de modifier la moindre ligne de code.
