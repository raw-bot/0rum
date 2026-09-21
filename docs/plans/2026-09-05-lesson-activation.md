# Réparer l'apprentissage évolutif

Autorisation utilisateur : « fait la correction », 5 septembre 2026.

1. Introduire un petit catalogue de corrections falsifiables et une identité v2 qui ne dépend pas de la prose. Conserver l'identité v1 pour les observations non classées, l'historique, l'expiration et les contre-exemples. Deux décisions distinctes sont requises pour une leçon active, ce qui signifie utilisable comme hypothèse paper, pas rentable ou validée.
2. Classer explicitement un sous-ensemble des 68 candidates après lecture ; conserver les sources et ajouter un événement atomique d'agrégation/supersession. Contrôler versions/empreintes, rejet/expiration, décisions distinctes et contextes. Reprise idempotente. Ne pas forcer toutes les candidates en actives.
3. Post-mortems futurs : code contrôlé ou null, texte original conservé. Recherche de leçons applicables au symbole/régime, inconnus non valorisés comme similarités. Exposition tirée du compte évolutif seul. Tracer ce qui est fourni, ses versions et les citations du modèle ; aucune affirmation causale sur les performances.
4. Tests, relecture adversariale bornée (choix actuel de modèle conservé), migration sur copie, puis déploiement sauvegardé/hash-guard. Appliquer la migration sous verrou LLM, recharger LLM et dashboard ; vérifier compteurs, contexte réellement fourni et cycle achevé.

Risque : regrouper des observations incompatibles ou ressusciter un rejet. Mesures : clés/contextes explicites, règles canoniques sans seuils inventés, références aux sources, tests négatifs. Référence trader et ses prompts inchangés ; aucun ordre broker. Hypothèses v2 évaluées prospectivement ; résultats anciens non réécrits.

Rollback : arrêter seulement le service LLM si nécessaire et désactiver les règles v2 par événement, conserver historique et comptes/fills. Restaurer du code compatible avec les événements v2, jamais un ancien état comptable.

## Relecture et arbitrages

Cycle 1, huit findings acceptés : catalogue compatible et oppositions explicites ; normalisation symétrique depuis le brief d'entrée ; preuve distincte sans rajeunissement ; producteur de contre-exemples ; isolement de l'erreur de récupération ; observations null idempotentes ; lecture historique compatible ; hypothèses et citations sans promesse causale.

Cycle 2, cinq findings acceptés et corrigés : null non injectable, régime inconnu exclu, contradiction/support incohérents refusés avant mutation et observation terminale pour un rejet, disponibilité actuelle distinguée de l'état persisté, contexte canonique sans prix/récit historique.

Cycle 3 : garde de reprise renforcé pour empêcher qu'un rejet v2 retombe sur une source v1 et produise un auto-alias. Le lecteur dashboard utilise now_utc même lorsque l'API ne fournit pas de date. Tests de régression ajoutés. Choix utilisateur de conserver le modèle actuel respecté.
