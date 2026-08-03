# Chantier 6 — Étude Dynamic Égide sur toutes les stratégies

## Protocole

- Fenêtre: 2024-01-01 → 2026-07-15.
- Données: snapshots immuables BTC 15m/1h/4h, ETH 1d et PAXG 1d.
- Empreinte: `d5efff8d2d9461ad1f1ab189b01b0bdfa65cf3d3f1e72a36e3ebdcc3a4893571`.
- Moteur: le vrai `PaperEngine`, horloge simulée 15 minutes, bougies closes
  seulement, coûts et caps identiques entre chaque paire.
- COT: rapports historiques publiés uniquement après `usable_from`; aucun
  rapport futur n'est visible au moteur.
- Comparaisons: chaque sleeve seule en native puis dynamique, portefeuille
  cinq sleeves, et portefeuille opérationnel AK+UT.

Artefact machine compact et versionné:
`backtests/reports/chantier6_universal_dynamic_exit.json`. Le run détaillé local
reste sous `backtests/runs/universal_dynamic_exit_final_20260722/`.

Contrat reproductible: configuration `state/portfolio.yaml`, hash fichier
`da440f4221b81d3bb16ff1fae1d3a77ff93093ee590eeafe36790919b22c063d`,
hash YAML normalisé
`780b0ac15c08567a139a45fd0563e951fefaebeed5df73bf1457d4952401d9a9`,
hash COT
`b50a265d49d4a8db17e12740178bb12488bdca2d194d8ba3ba653a48920989e3`.
Chaque sous-run stocke et vérifie son propre `study_contract.json`; `--resume`
refuse un contrat absent ou différent.

## Limite de validation

Les seuils adaptés ont été choisis après lecture de cette même fenêtre
2024–2026. Les deltas ci-dessous sont donc **in-sample** et ne constituent pas
une validation OOS. Conformément au choix opérateur, `execute` en paper est le
test forward à partir du 2026-07-22; aucune conclusion live n'en est tirée.

## Résultats par stratégie

| Stratégie | Native | Ratchet adapté | Delta | DD native → dyn. | Verdict |
|---|---:|---:|---:|---:|---|
| AK MACD (TP 2R) | $13,801.38 | $15,110.33 | **+$1,308.94** | 22.22% → 18.31% | essai forward paper |
| UT Bot | $37,733.31 | $14,535.10 | −$23,198.20 | 33.28% → 37.65% | conserver SELL natif |
| HA Trend | $11,970.20 | $12,241.86 | **+$271.65** | 24.87% → 13.18% | prêt si sleeve réactivée |
| ETH Donchian | $11,688.88 | $11,255.10 | −$433.79 | 20.11% → 18.78% | conserver canal natif |
| Gold COT | $15,818.27 | $15,664.13 | −$154.14 | 6.71% → 7.12% | conserver COT natif |

Les paramètres adaptés testés étaient AK `1.5/1.0/0.25`, UT `5/3/1`, HA
`1/0.5/0.1`, Donchian `3/2/0.5` et Gold `5/3/1`
(`activation/giveback/floor`, en R officiel). Même rendus très lâches, les
ratchets UT, Donchian et COT restent inférieurs à leurs sorties natives.

## Résultat opérationnel

Le portefeuille réellement autorisé aujourd'hui est AK + UT. La politique
retenue active le ratchet calibré sur AK et conserve le SELL UT natif:

| Variante | Equity finale | Retour | Max DD | Sorties dynamiques ajoutées |
|---|---:|---:|---:|---:|
| AK+UT natif | $35,200.33 | +252.00% | 40.48% | 0 |
| AK ratchet + UT natif | **$42,128.67** | **+321.29%** | 40.48% | 26 |

Delta historique: **+$6,928.33**, soit +69.28 points de rendement, sans hausse
mesurable du drawdown. C'est la politique sélectionnée pour l'essai forward
paper, pas une promesse de performance future.

## Interaction cinq sleeves

Forcer un ratchet sur les cinq sleeves est formellement rejeté. Avec les
paramètres AK copiés partout, l'equity tombe de $43,846.28 à $17,919.86. Même
avec les seuils adaptés, l'all-on finit à $24,037.30 contre $46,937.14.

AK+HA dynamiques seuls finissent légèrement sous le témoin cinq sleeves
($46,433.89, −$503.25), car leurs sorties plus précoces changent l'allocation
des caps. HA reste donc configuré mais `entry_enabled: false`; il n'affecte
pas le portefeuille opérationnel.

## Conclusion

Toutes les stratégies sont sous Dynamic Égide, mais l'Égide route vers la
meilleure intelligence de sortie prouvée pour chacune. “Dynamique” ne veut
pas dire “même trailing stop”: UT, Donchian et COT étaient déjà dynamiques et
leur ajouter le ratchet détruit de l'edge. Le changement rentable est AK
calibré, avec HA prêt pour une éventuelle réactivation séparée.
