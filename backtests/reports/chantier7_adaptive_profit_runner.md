# Chantier 7 — Égide offensive et runner adaptatif

## Objectif

Supprimer le plafond économique des stratégies à bracket sans remplacer 2R
par un autre plafond statique. Le TP initial devient une cible active. À
l'approche de cette cible, Égide mesure quatre signaux causaux de continuation
sur bougies closes et étend la cible de +1R lorsque trois signaux au moins sont
positifs.

UT Bot, Donchian et Gold COT restent sans cible synthétique: leurs sorties
natives étaient déjà non plafonnées. AK et HA reçoivent le runner adaptatif.

## Contrat de replay

- Fenêtre: 2024-01-01 → 2026-07-15.
- Run: `adaptive_profit_runner_20260723`.
- Données: `d5efff8d2d9461ad1f1ab189b01b0bdfa65cf3d3f1e72a36e3ebdcc3a4893571`.
- Configuration fichier: `9f8885d69e9756d991607b77af7a3e4e770966e05cea684fa80b2a3309084804`.
- Configuration normalisée: `6954d3f718ef23e990a9a354ea2c103e7e17e3da0855c6ac29b61f45387a53f8`.
- COT: `b50a265d49d4a8db17e12740178bb12488bdca2d194d8ba3ba653a48920989e3`.

Les paramètres ont été définis puis mesurés sur cette même fenêtre. Les
résultats sont donc in-sample; le paper `execute` est le test forward.

## Résultats

| Périmètre | Statique | Égide défensive | Runner adaptatif | Gain runner vs défensif |
|---|---:|---:|---:|---:|
| AK seul | $13,801.38 | $15,110.33 | **$15,636.42** | **+$526.09** |
| AK + UT opérationnel | $35,200.33 | $42,128.67 | **$43,067.01** | **+$938.34** |
| HA seul | $11,970.20 | $12,241.86 | **$12,266.43** | **+$24.58** |

Face au portefeuille AK+UT statique, le runner sélectionné ajoute
**$7,866.67** et +78.67 points de rendement. Le max drawdown reste à 40.48%,
inchangé à la précision du replay.

AK a produit 23 extensions de cible; la séquence la plus forte a atteint une
cible active de 6.27R officiel. HA a produit 31 extensions et atteint 4.14R.
Sur AK, 22 des 23 anciens TP fixes ont cessé de plafonner les trades; les
sorties ont ensuite été gouvernées par le stop dynamique.

## Interaction portefeuille

Le portefeuille théorique cinq sleeves avec AK+HA runners termine à
$46,372.68 contre $46,937.14 en natif, soit −$564.46 à cause des interactions
de caps. HA reste donc `entry_enabled: false`. Cette interaction ne touche pas
le portefeuille opérationnel AK+UT.

## Décision

Activer le runner adaptatif en paper `execute` sur AK et le préparer sur HA.
Conserver les sorties natives non plafonnées d'UT, Donchian et COT. Ne pas
présenter cette étude comme une optimisation complète des paramètres UT.
