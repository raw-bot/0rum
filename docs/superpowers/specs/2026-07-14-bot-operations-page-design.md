# Page dédiée « Bot » et langue française des sorties LLM

Date : 2026-07-14

Statut : proposée — validée oralement, en attente de relecture écrite

## Contexte

Le dashboard principal mélange les marchés, le portefeuille paper unifié et le
laboratoire LLM. Cette densité rend le suivi opérationnel du bot difficile.
L'opérateur veut une page distincte, accessible directement depuis la barre du
dashboard à côté des icônes de disposition, qui rassemble le bot unifié et les
deux lanes LLM paper.

Les réponses de DeepSeek doivent être lisibles en français. Une instruction de
prompt seule ne suffit pas : des briefs ont été produits en chinois. Les
réponses non françaises ne doivent pas devenir des décisions exécutables ni
être présentées comme un mémo français.

Les journaux de la nuit montrent des erreurs OpenRouter HTTP 429, et non 409.
Un 429 signifie une limite temporaire du fournisseur (quota ou cadence), pas un
conflit de portefeuille. Les stops du simulateur paper restent locaux et
continuent de fonctionner lorsqu'OpenRouter est indisponible.

## Décision

Créer une page en lecture seule `GET /bot`, servie par le même processus
dashboard et alimentée exclusivement par `GET /api/state`.

Le dashboard principal reste la console marché. Sa carte LLM est retirée et un
lien compact « BOT » est ajouté dans la barre supérieure, immédiatement après
les contrôles de disposition. La page `/bot` reprend le même style visuel et
offre un lien de retour « MARCHÉS » vers `/`.

## Contenu de la page `/bot`

1. **Santé du bot unifié** : worker paper, fraîcheur du heartbeat, positions
   ouvertes, solde, équité, P&L latent et signal du moteur historique arrêté.
2. **Portefeuille unifié** : positions, derniers fills et derniers trades,
   clairement séparés de l'historique legacy non autoritatif.
3. **Runtime LLM paper** : modèle, cadence, dernière exécution, résultat et
   erreur fournisseur formulée en français. Un HTTP 429 est affiché comme
   « limite temporaire OpenRouter ».
4. **Lanes référence et évolutive** : soldes, équité, positions, levier,
   stop, objectifs, P&L, décisions et fills.
5. **Analyse et apprentissage LLM** : dernier avis de marché, outcomes,
   post-mortems et leçons. Les entrées d'audit restent consultables, mais une
   réponse non française est signalée comme refusée au lieu d'être affichée
   comme une analyse valide.

La page ne contient aucune commande de trading, aucun contrôle de démarrage ou
d'arrêt et aucun secret. Elle n'écrit pas les journaux.

## Contrat de langue

Avant la validation des objets `MarketBrief` et `ProposedDecision`, le runtime
vérifie que les champs narratifs demandés en français ne contiennent pas de
caractères CJK. Les symboles, nombres, identifiants et termes techniques restent
acceptés.

En cas de réponse non française :

- l'objet est journalisé comme `model_error` avec la cause
  `response_not_french` ;
- aucune décision paper n'est exécutée ;
- le dashboard et `/bot` affichent une explication française concise ;
- le texte brut reste seulement dans le journal d'audit, et n'est pas rendu
  dans l'interface opérateur.

Ce mécanisme refuse un contenu ambigu ; il ne le traduit pas et ne modifie donc
pas une trace d'audit générée par le modèle.

## Erreurs OpenRouter

Le runtime conserve ses erreurs brutes dans le journal, mais l'API dashboard
expose une cause opérateur sûre : `429` devient « limite temporaire OpenRouter
(quota ou cadence) ». Les autres erreurs restent bornées et sans secret.

Un 429 n'empêche pas le moniteur paper de vérifier les stops, les take-profits,
les liquidations et les sorties temporelles des positions déjà ouvertes.

## Architecture

```text
GET /             -> dashboard marché + lien BOT
GET /bot          -> page opérations bot (lecture seule)
GET /api/state    -> même snapshot borné pour les deux pages

OpenRouter -> validation de langue -> journaux LLM -> /api/state -> /bot
                         | refus
                         +-> aucune exécution paper
```

Nouveaux assets prévus : `orum/static/bot.html` et `orum/static/bot.js`.
Le CSS commun reste dans `dashboard.css`, avec seulement les styles spécifiques
à la page Bot ajoutés au même fichier pour éviter un second thème.

## Vérification

- tests de route : `/bot` sert la page et ses assets ;
- tests front-end : le lien BOT est placé dans la barre supérieure ;
- tests de snapshot : le contenu LLM et unifié déjà borné est réutilisé ;
- tests de langue : une réponse CJK est journalisée comme refusée et ne peut
  pas aboutir à une exécution ;
- tests d'erreur : un 429 est rendu en français et sans clé ;
- vérification manuelle : navigation `/` → `/bot` → `/`, et contrôle des deux
  lanes après un cycle paper.

## Hors périmètre

- traduction automatique des réponses modèles ;
- modification de la fréquence LLM, du modèle ou des limites OpenRouter ;
- ajout de commandes d'exécution sur `/bot` ;
- changement du moteur unifié ou du simulateur paper.
