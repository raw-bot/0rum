# ADR-021 — Consolidation sans changement de stratégie

Statut : accepté, demande du 9 septembre 2026 (« go sans rien casser »).

## Décision

Retirer les options sans effet et les anciens points d'entrée mono-actifs,
sans changer risque, signaux, sorties ou comptes. Le README décrit désormais
le portefeuille unifié ; l'ancienne version est conservée dans `docs/archive/`.

`load_config_snapshot` retourne ensemble la source et le dictionnaire lus.
`load_config` continue à retourner le même dictionnaire. `--dry` construit son
moteur depuis cette lecture unique et affiche le hash. Le secours reste complet
et les différences opérateur/secours sont conservées.

La préparation des plans est extraite en méthode du même `PaperEngine`. Elle
reçoit le même compte et la même fermeture `fetch` : curseurs, sorties NVDA
différées, ordre des fetches et plans de repli restent aux mêmes endroits.
Cette méthode n'est pas pure. Aucun point d'injection de panne, interface de
replay ou import existant n'est supprimé. Réintégrer son AST restitue exactement
le module précédent ; tests et comparaison déterministe complètent la preuve.

`run_all.sh`, `run_engine.sh`, `run_local.sh`, `run_local_4h.sh` et
`run_native_lab.sh` deviennent des stubs sans effets, sortie 64. Les commandes
CLI `start/stop/restart` échouent avant l'accès au projet/dashboard. Les services
expérimentaux et contrôles dashboard existants restent séparés.

## Compatibilités conservées

Le cycle legacy et `orum.loop` servent encore tests, replays et imports actifs.
`portfolio_shadow.py` est toujours une expérience distincte ; son retrait et
le traitement de ses positions demandent une décision propre. Les anciens
blocs `forecast_gate` des manifestes historiques restent ignorés comme avant.
Voir [l'inventaire](../OPERATING-MODES.md).

## Vérification et retour arrière

Baseline isolée issue du code courant et de ses modifications locales : 161
tests ciblés et 15 sous-tests passent. Les nouveaux tests reproduisent le
mauvais chemin affiché et la double lecture `--dry`, puis vérifient la correction.
La revue indépendante examine le diff et la portée des variables déplacées.

Publication sous garde des hashes des seuls fichiers concernés, avec sauvegarde.
Le cycle de preuve commence après publication complète. Les empreintes des
comptes de recherche continuent à signaler le changement de source, même si la
parité logicielle est prouvée ; aucune continuité méthodologique n'est fabriquée.
Le rollback restaure uniquement les fichiers publiés sous contrôle de hash,
jamais les positions, soldes, fills ou checkpoints produits depuis.
