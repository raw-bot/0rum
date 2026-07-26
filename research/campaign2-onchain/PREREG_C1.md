# Pré-enregistrement C1 — Croissance de l'offre stablecoin fiat-backed comme signal de régime de liquidité

**Gelé le 2026-07-26, committé AVANT tout téléchargement de données de test et tout calcul. Aucune modification après le premier run.** Boucle de contradiction close à l'itération 2/3 (`PHASE3_CONTRADICTION.md`) ; les 9 critères d'abandon proposés par GPT-5.6 sont intégrés ci-dessous.

## Hypothèse (revendication dégradée)

La croissance de l'offre agrégée des stablecoins USD fiat-backed contient une information prédictive sur les rendements BTC à 1-4 semaines, incrémentale à un null composite momentum+volatilité. Aucune identification du canal causal n'est revendiquée.

## Variable et signal (figés)

- FB_t = Σ offre circulante `peggedUSD` des stablecoins classés `fiat-backed` + `peggedUSD` par DefiLlama (snapshot SHA-256 au téléchargement).
- g_t = ln(FB_{t−1}) − ln(FB_{t−31}) (lag de publication 1 j ; **aucun prix dans la construction**).
- S_t = z-score de g_t sur 730 j roulants (min 365 j).
- Décision hebdomadaire (lundi, close UTC) : long BTC spot si S ≥ z_in ; cash rémunéré (DTB3) si S ≤ z_out ; sinon état conservé (hystérésis).
- Grille de développement UNIQUEMENT : z_in ∈ {0.25, 0.5, 0.75, 1.0} × z_out ∈ {z_in−0.5, z_in−1.0} ; sélection = rendement net annualisé maximal en développement. La même sélection est accordée au null (équité).

## Périodes

- Développement : 2017-11 → 2022-06 (choix des seuils, régression incrémentale).
- **Confirmation : 2022-07 → 2026-07, intacte.** Sous-périodes : 2022-07→2024-06 et 2024-07→2026-07.

## Benchmarks (figés)

1. **Null momentum investissable** : même structure (hebdo, hystérésis, mêmes grilles, même sélection en dev) sur M_t = z-score 730 j du momentum prix 30 j.
2. **Placebo** : 10 000 stratégies aléatoires préservant le nombre de blocs de détention et le temps total en marché de la stratégie en confirmation ; rang percentile rapporté (> 90e = soutien, non éliminatoire).
3. B&H réduit à exposition moyenne égale (rapporté).
4. Régression de développement : rendement forward 1 sem sur [mom30z, mom90z, vol30z, S_t] — coefficient de S positif exigé pour passer en confirmation.

## Coûts (figés)

Kraken spot : commission maker 16 bp + demi-spread 1 bp par exécution (aller-retour ≈ 34 bp) ; capitaux 5 000 € et 20 000 € (minimums sans objet en spot %). Le null investissable supporte les mêmes coûts.

## Critères d'abandon (un seul suffit → REJET de C1) — intègrent les 9 de GPT-5.6

- (a) Coefficient incrémental de S non positif dans la régression de développement → C1 ne passe jamais en confirmation.
- (b) Avantage net en confirmation vs null momentum < **1 %/an** (à 20 k€ ; mesuré stratégie−null, PAS stratégie−cash).
- (c) Une sous-période de confirmation avec contribution nette vs null < 0 (inversion, pas bruit : seuil −0,5 %/an ; ET les deux sous-périodes doivent être ≥ 0).
- (d) Leave-out prédéfini : retrait des 3 plus grands épisodes d'émission de la confirmation — épisode = fenêtre de 30 j centrée sur les pics de |S| non chevauchants les plus élevés, mesurés sur la variable primaire ; les semaines dont la décision utilise ces fenêtres sont retirées → l'avantage net vs null doit rester > 0.
- (e) Concentration par actif : le signe de l'avantage doit persister avec le signal recalculé ex-USDT et ex-USDC (chacun séparément).
- (f) Actif : BTC = actif primaire (seul décisionnel) ; ETH = contrôle de signe rapporté. Aucun choix ex post.
- (g) Aucun déplacement de seuils/fenêtres après développement ; si la confirmation exige un réglage pour survivre → rejet.
- (h) Audit des épisodes déclencheurs : vérification manuelle des séries d'offre des 2 principaux contributeurs autour de chaque bascule long/cash de la confirmation (ruptures de périmètre, migrations de contrats) ; anomalie majeure non explicable → rejet du trade concerné, et rejet de C1 si > 25 % des trades sont affectés.
- (i) Le placebo montre que le résultat est reproductible par du simple temps-en-marché (stratégie < 60e percentile) ET l'avantage vs null < 1 %/an → rejet (redondant avec b, conservé par prudence).

## Engagements

Snapshot des données + SHA-256 dans `DATA_MANIFEST_C1.md`. Code committé avec les résultats. Le ratio à la mcap crypto et le canal causal ne figurent dans aucun critère décisionnel (descriptifs seulement). Une conclusion négative est un résultat valide.
