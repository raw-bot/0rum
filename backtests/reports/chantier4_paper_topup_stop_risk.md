# Chantier 4 — topup paper et risque réel au stop

> **STATUT : SCÉNARIO ABANDONNÉ.** Le 2026-07-17, l'opérateur a restauré le
> dimensionnement historique `2×ATR` et conservé le plafond notionnel à 3x.
> Les résultats +107,25 % / +118,23 % ci-dessous documentent uniquement
> l'expérience corrigée au stop ; ils ne décrivent plus le bot paper actif.

Date : 2026-07-17. Périmètre : duo BTC AK MACD 4h + UT Bot M15/H1,
du 2024-01-01 au 2026-07-15, 88 897 cycles, même snapshot fermé pour les
deux variantes.

## Changement mesuré

Les nouvelles entrées sont dimensionnées et plafonnées avec la perte réelle
par unité jusqu'au stop :

`risk_distance = entry_price - stop_loss_price`

L'ATR 2x reste persisté comme diagnostic et comme fallback uniquement quand la
stratégie ne fournit aucun stop. Les anciens résultats du chantier 1
(+341 % hold, +391 % topup) utilisaient l'ATR comme base de taille malgré des
stops souvent plus distants : ils ne sont donc pas comparables et ne doivent
plus servir à estimer le rendement du bot.

## Configuration ex ante

```yaml
max_total_stop_risk_pct: 0.05
max_symbol_stop_risk_pct: 0.03
reentry_policy: topup
merit_order:
  - btc_utbot_m15_h1
  - btc_ak_macd_4h
min_topup_fraction: 0.0
```

Risques des sleeves inchangés : AK 2 %, UT Bot 0,75 % dans la configuration
paper opérateur. Le replay comparable utilise le fichier figé
`backtests/configs/duo_ak_utbot.yaml` pour les deux variantes.

## Résultat au close historique

| Variante | Équité finale | Rendement net | DD max | Entrées | Écart vs hold |
|---|---:|---:|---:|---:|---:|
| hold | 20 725,39 $ | +107,25 % | 18,65 % | 202 | — |
| topup | 21 822,81 $ | +118,23 % | 19,42 % | 247 | +1 097,42 $ / +10,98 pts |

Le topup ajoute 45 entrées de tranche : 19 AK et 26 UT Bot. Le ledger
d'enchère compte 36 allocations partielles `granted_topup`; certaines ré-entrées
peuvent recevoir une allocation complète selon le budget disponible. Une
demande UT Bot est refusée parce que la thèse est déjà entièrement financée.

Avec le plancher demandé à zéro, les plus petites tranches sont réellement
minuscules (risque stop minimum 0,09 $ pour AK et 0,66 $ pour UT Bot). C'est un
effet assumé de `min_topup_fraction: 0.0`, pas un défaut masqué. Aucune protection
ou réduction de risque n'a été ajoutée.

## Stress d'exécution M15

Entrée au premier open M15 disponible, slippage défavorable à l'entrée et aux
stops, niveaux SL/TP figés. Le flux de décisions reste gelé, comme dans le
repricer existant.

| Slippage | Hold rendement / DD | Topup rendement / DD | Avantage topup | Trades sautés |
|---:|---:|---:|---:|---:|
| 0 bps | +105,21 % / 18,8 % | +116,83 % / 19,6 % | +11,62 pts | 0 / 0 |
| 2 bps | +96,92 % / 19,7 % | +107,20 % / 20,6 % | +10,28 pts | 0 / 0 |
| 5 bps | +85,11 % / 21,1 % | +93,55 % / 22,0 % | +8,44 pts | 0 / 0 |
| 10 bps | +66,96 % / 23,3 % | +72,75 % / 24,3 % | +5,79 pts | 0 / 0 |

Le bénéfice du topup survit aux quatre niveaux. Son coût observé est environ
+0,8 à +1,0 point de drawdown. Le repricer signale sept changements de motif de
sortie dans chaque variante et aucun `gap_through_entry`,
`position_still_open` ou `no_bar`.

## Attribution

Au close historique, le topup porte le PnL net AK de 4 818,08 $ à 5 196,99 $
et le PnL net UT Bot de 5 907,31 $ à 6 625,82 $. À 10 bps réalistes, les deux
restent positifs : AK 4 112,04 $, UT Bot 3 162,52 $.

## Vérification

- 92 tests ciblés verts ;
- 583 tests `unittest discover -s tests` verts ;
- aucun redémarrage du worker, watcher, producer ou dashboard ;
- aucun ordre broker : paper uniquement ;
- même empreinte de données :
  `d5efff8d2d9461ad1f1ab189b01b0bdfa65cf3d3f1e72a36e3ebdcc3a4893571`.

Empreintes des ledgers :

| Fichier | SHA-256 |
|---|---|
| hold `fills.jsonl` | `549dfbac7180f68b319350b0005f656702ada070e590f5818c7398662473589f` |
| hold `equity.jsonl` | `2f21f17b0360a48c90690bf1a1744fb91bf04073ed4cfa0674b61a70ec6c835a` |
| topup `fills.jsonl` | `a6fe392e215d28da87ac5b2d37f4af011d62feafbc124b8b944e2cf87b7aedfa` |
| topup `equity.jsonl` | `46a3828103ab025f10ca57b1d96a02302a94abc16f205f64509d04f6a14d1cad` |

## Verdict

Promotion justifiée en paper : avec le risque corrigé au vrai stop, le topup
reste supérieur au hold sur le close historique et sous chaque stress de
slippage testé. Il améliore le rendement au prix d'un drawdown légèrement plus
élevé et d'un nombre de fills supérieur. Ce résultat qualifie le comportement
paper ; il ne constitue pas une preuve live.
