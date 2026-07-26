# C1 — Résultats du test pré-enregistré (2026-07-26)

Spec : `PREREG_C1.md` (commit `849b06a`, gelé avant données). Run unique, seed 42, sortie brute `results_c1.json`.

## Verdict formel : **AUCUN CRITÈRE D'ABANDON DÉCLENCHÉ** (gates a–i passés)

| Mesure (confirmation 2022-07 → 2026-07) | Valeur |
|---|---|
| Stratégie signal (net, annualisé) | **+29,4 %/an** (exposition 50 %, **2 bascules en 4 ans**) |
| Null momentum investissable (net) | +16,1 %/an (exposition 30 %, 14 bascules) |
| Avantage vs null | **+13,4 %/an** (critère b : ≥ 1 % ✓) |
| Sous-périodes (avantage vs null) | +7,4 % / +18,2 % (c ✓) |
| Leave-out 3 épisodes d'émission | +14,2 % (d ✓) |
| Ex-USDT / ex-USDC | +10,0 % / +16,0 % (e ✓) |
| Contrôle ETH (signe) | +2,5 % (✓) |
| Audit des bascules | 2 bascules, 0 anomalie de périmètre (h ✓) |
| Coefficient incrémental en développement | +0,0054/sem, positif (a ✓) |

## Lecture honnête : le GO formel est FAIBLE — trois diagnostics le disent

1. **Placebo au 36e percentile.** 64 % des stratégies aléatoires à exposition et structure identiques font MIEUX que le signal. Le timing stablecoin n'a démontré aucune valeur d'emplacement — l'avantage vs null vient de la fragilité du null (14 bascules de momentum whipsawé), pas d'une compétence de timing du signal. Le critère (i) exigeait placebo < 60e ET avantage < 1 % pour rejeter — il n'est formellement pas déclenché, mais c'est le diagnostic le plus profond du tableau.
2. **2 bascules en 4 ans = ~1,5 pari indépendant.** L'avantage de +13,4 %/an repose essentiellement sur un unique bloc long bien placé (2023→2025). Les sous-périodes « positives » ne sont pas des réplications : c'est le même bloc.
3. **t = 0,35 en développement.** Le coefficient incrémental était positif mais statistiquement nul ; en dev, le null momentum battait largement la stratégie signal (85,6 % vs 40,8 %/an). Le signal ne « gagne » qu'en confirmation — compatible avec de la chance de régime, exactement l'avertissement de GPT (« avec 2,5 cycles, une marge attribuable au hasard de régime »).

## Décision de recherche

Le pré-enregistrement gouverne le **rejet** — il n'a pas rejeté. Il ne force pas le déploiement. Sur l'échelle de décision de la mission :

**À SURVEILLER — validation prospective uniquement.** Concrètement : (1) collecte prospective du signal dès maintenant (décision hebdo loggée, sans capital) pour accumuler des paris indépendants réels ; (2) pas de déploiement de capital sur 1,5 pari indépendant ; (3) l'itération 3/3 de contradiction GPT-5.6 reste disponible pour un examen hostile de ce dossier de résultats si souhaité.

Ce résultat est cohérent avec la conclusion des deux campagnes : les gates quantitatifs peuvent passer sur un échantillon dont la structure (2 bascules) ne contient tout simplement pas assez d'information pour trancher.
