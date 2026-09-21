# ADR-020 — Hypothèses évolutives identifiables et traçables

Date : 2026-09-05. Décision : acceptée pour expérimentation paper, demande utilisateur « fait la correction ».

## Problème et choix

68 leçons candidates et aucune active : l'identité incluait toute la prose de correction et du contexte. Les répétitions sémantiques ne cumulaient pas leurs observations. Introduire six codes qualitatifs compatibles avec des catégories d'erreur explicites. L'identité v2 comprend code, catégorie, symbole, régime normalisé par alias exact et côté. Deux décisions distinctes et non expirées, nettes de contre-exemples, rendent l'hypothèse disponible. Ce seuil exprime une récurrence, pas une efficacité démontrée.

Les observations sans code restent candidates non injectables. Les hypothèses opposées sont exclues de la récupération lorsqu'elles sont simultanément éligibles dans le même contexte. Les régimes inconnus ne sont pas applicables. Le contexte transmis ne conserve pas les prix ni le récit particuliers d'une ancienne observation. Les sources originales restent au journal.

## Reprise et réfutation

Le plan config/lesson_migration_20260905.json classe 12 observations en cinq hypothèses. Les 56 autres restent candidates. Une publication atomique ajoute un événement contenant les versions agrégées et les alias de supersession ; l'ancien préfixe du journal est conservé. Empreintes sources, identifiant et empreinte du plan empêchent une reprise divergente. Dates et expirations originales sont conservées. Aucun rejet ne peut être réactivé par la reprise.

Les post-mortems évolutifs peuvent proposer un code ou null et signaler des contre-exemples parmi les leçons effectivement fournies et citées. Une perte seule n'est pas une réfutation. Soutenir et contredire le même code dans le même post-mortem est rejeté avant mutation. Une observation visant une hypothèse rejetée est conservée sans la réactiver.

## Visibilité et isolation

Chaque proposition conserve les leçons fournies, leurs versions et les citations. Le dashboard /bot distingue états au journal, disponibilité actuelle et leçons fournies/citées lors de la dernière proposition. Fourniture, citation et efficacité ne sont pas équivalentes. Les performances anciennes ne sont pas recalculées.

La référence ne reçoit aucune leçon ; ses prompts et paramètres restent inchangés. Une erreur de récupération est tracée et laisse les deux branches continuer, l'évolutive sans leçons. Les politiques de risque, huit comptes par stratégie, compte principal et cadence de collecte restent identiques. Le fingerprint global du code peut signaler une nouvelle méthode même pour une stratégie dont la logique n'a pas changé ; ne pas réécrire les manifestes gelés.

## Vérification et retour arrière

Tests isolés : regroupement de paraphrases, alias, décisions distinctes, expiration, incompatibilités, null, régime inconnu, rejet sans résurrection, atomicité/empreintes/reprise, référence, trace fournie/citée et disponibilité dashboard. Relecture adversariale bornée à trois cycles ; corrections consignées dans le plan.

Déploiement sauvegardé, vérification des empreintes de chaque fichier live avant copie, migration sous verrou de cycle LLM, puis rechargement LLM et dashboard. Conserver comptes et fills. En cas de retour arrière, désactiver les hypothèses v2 par événements et garder un lecteur compatible avec les événements de migration ; ne jamais restaurer un ancien solde ou journal de fills.
