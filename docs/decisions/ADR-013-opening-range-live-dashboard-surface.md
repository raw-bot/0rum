# ADR-013 — Surface Opening Range sur le dashboard live

## Statut

Accepté — 2026-08-03. La décision ultérieure d’alimenter et d’activer une sleeve
paper est décrite dans ADR-014.

## Contexte

Une première page de recherche Opening Range Reversal avait été ajoutée au moteur de recherche/backtest sur le port 8008. L’opérateur utilise toutefois le dashboard du bot récent sur le port 8787, dans ce dépôt. La page devait être indépendante des vues Marchés et Bot, conserver le même langage visuel, identifier explicitement l’actif étudié et ne pas créer d’ambiguïté avec une stratégie déjà tradée.

Le runtime paper unifié récent est autoritatif. L’ancien moteur continu reste une surface séparée et non autoritative ; son état doit être visible sans permettre de le relancer depuis cette page.

## Décision

- Servir une page dédiée à GET /opening-range.
- Ajouter un lien OPENING RANGE · NVDA dans la barre supérieure du dashboard principal.
- Utiliser NVDA (Nasdaq, USD) comme actif de référence explicite pour la phase de validation.
- Réutiliser les tokens et composants visuels du dashboard existant.
- Lire uniquement GET /api/state afin d’afficher l’état du runtime paper unifié et la déconnexion de l’ancien moteur.
- Garder la page strictement en lecture seule : aucune route POST, aucun contrôle de worker et aucun branchement dans le portefeuille.
- Condamner l’ancien point d’entrée POST /api/worker/start avec une réponse HTTP 410 ; conserver uniquement l’arrêt comme mesure de sécurité.
- Ne pas afficher de prix ou de signal tant qu’un flux NVDA M5/D1 validé n’est pas connecté.

## Alternatives considérées

### Conserver uniquement la page du port 8008

Rejeté : cette surface n’est pas le dashboard opérationnel consulté sur le port 8787 et le bouton demandé resterait absent.

### Enregistrer immédiatement la stratégie dans le portefeuille paper

Rejeté : les données NVDA, le calendrier Nasdaq, les coûts et le backtest sans fuite temporelle ne sont pas encore validés. Une simple page de suivi ne justifie pas une modification du moteur de trading.

## Conséquences

- La stratégie devient accessible depuis le dashboard récent avec un statut honnête de recherche.
- L’ancien moteur ne peut plus être relancé depuis l’API du dashboard, même manuellement.
- Aucun comportement de signal, de risque, d’exécution ou de portefeuille n’est modifié.
- L’activation future nécessitera une décision séparée après connexion des données et validation paper.

## Retour arrière

Supprimer les trois routes statiques, le lien de navigation, les fichiers opening_range.* et leur test de routage. Aucun état de trading ni schéma de données ne doit être restauré.
