# R1 Turn-of-month — Résultats (run du 2026-07-26)

**VERDICT : REJET**, par déclenchement du critère pré-enregistré (iii). Spec : `PREREG.md` (commit `1b7cb46`, gelé avant toute donnée). Code : `analysis.py`, sortie brute : `results.json`, seed 42, 10 000 bootstraps.

## Résultat central : l'effet a disparu après publication

| Période (^SP500TR) | TOM (bp/jour) | non-TOM (bp/jour) | Différence |
|---|---|---|---|
| 1988–2008 (pré-publication, descriptif) | — | — | **+8,01 bp** |
| **2009–2026 (test principal, 210 mois)** | 6,97 | 6,02 | **+0,95 bp** — IC95 [−6,89 ; +8,68] |

- La différence post-publication est statistiquement indiscernable de zéro (41 % de la distribution bootstrap ≤ 0) et **8× plus faible** qu'avant publication.
- La revendication de concentration est réfutée : la fenêtre TOM (19,1 % des jours) capture 21,5 % du rendement total — proportionnel, pas concentré. Les jours non-TOM ont rapporté 6 bp/jour : le premium 2009–2026 était réparti uniformément.
- Benchmark de blocs aléatoires de 4 jours : la fenêtre TOM se classe au **61e percentile** — indiscernable d'un bloc quelconque.

## Critères d'invalidation

| Critère | Résultat | Déclenché |
|---|---|---|
| (i) diff ≤ 0 sur 2009–2026 | +0,95 bp | non |
| (ii) signe non conservé dans les 2 sous-périodes | +0,9 / +1,0 bp | non |
| (iii) avantage net vs B&H exposé égal < 1 %/an à 20 k€ | **−1,42 %/an** (avantage brut +0,14 %, coûts 1,56 %) | **OUI → REJET** |
| (iv) signe perdu après retrait des 4 mois extrêmes | +3,12 bp | non |

Le point (iii) n'est pas un accident de coûts : même **avant coûts**, la stratégie TOM ne bat le buy-and-hold à exposition égale que de 0,14 %/an, avec un Sharpe deux fois pire (0,39 vs 0,82) et un drawdown double. Il n'y a rien à récolter.

## Résultats annexes

- EXW1.DE (Euro Stoxx tradable) : avantage brut **négatif** (−1,14 %/an) — l'effet est absent aussi en Europe sur la période.
- Contrôles de signe : ISF.L +2,5 bp (positif faible) ; 1306.T +94,6 bp — **artefact de données** probable (ajustements de dividendes de l'adjclose Yahoo), non exploitable, signalé et non utilisé.
- Variante descriptive T−4→T+3 : +4,49 bp — supérieure à la fenêtre primaire, mais descriptive par engagement et de toute façon insuffisante face à 1,56 %/an de coûts.

## Limites de données (sans effet sur le verdict)

Accrual cash DTB3 en base 360 approximée ; Euribor 3M FRED arrêté à 2026-01, forward-fill 6 mois ; adjclose Yahoo pour les ETF (qualité moyenne, suffisante pour des critères en %/an).

## Conclusion

L'effet turn-of-month documenté par Lakonishok & Smidt (1988) et McConnell & Xu (2008) **n'a pas survécu à sa publication** sur le S&P 500 : c'est un cas classique de décroissance post-publication (McLean & Pontiff). Conformément au pré-enregistrement : pas de deuxième fenêtre, pas de re-spécification. R1 est clos. Le pipeline de la mission edge-research passe à R2 (gate : audit d'intégrité OI Kraken/OKX).
