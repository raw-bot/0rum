# Bilan de nuit — 11 juin 13:17 → 12 juin 05:26 UTC (~16 h)

**Résultat : 14 trades, solde 9 988,79 $ (−11,21 $ net), win rate net 6/14.**
Stratégie : v02 (exit RSI 55) → v03 (exit RSI 60) à 01:44 UTC.

## ✅ Ce qui fonctionne

1. **Première boucle d'auto-amélioration complète.** À 01:44, après exactement
   10 trades, la réflexion (gemini-3.1-flash-lite) a diagnostiqué :
   *« les trades ferment fréquemment en perte nette car les frais dépassent
   les petits gains »* → `exit_rsi_threshold` 55 → 60. Diagnostic identique à
   celui de l'audit, trouvé par le bot lui-même grâce à la comptabilité nette
   et au levier de sortie exposé la veille. Bornes respectées, v0002 archivée
   correctement (pré-changement), cooldown actif.
2. **Stabilité parfaite.** Aucun crash, aucune bascule offline du prix,
   aucune quarantaine, guardrail `normal`, drawdown max 0,2 %.
   Événements : 14 ouvertures / 14 fermetures, 3 boots (mise en route).
3. **Comptabilité honnête.** Solde = somme exacte des `net_pnl_usd`.
   Win rate brut 12/14 ; net 6/14 — l'ancien système aurait affiché une nuit
   « gagnante ».
4. **Rythme conforme** : ~0,9 trade/h, une réflexion par ~demi-journée.

## ⚠️ Ce qui fonctionne moins bien

1. **Économie des trades limite.** Avant changement (11 trades, exit 55) :
   +25,11 $ brut − 22,00 $ frais = **+3,11 $ net** (quasi breakeven).
2. **« Couteau qui tombe »** : trade de 05:13 entré sur RSI bas, marché en
   chute 30 min, sorti par `max_hold` avec RSI **12,7** (vendu au plus
   profond) : **−12,61 $ net**, plus que tous les gains de la nuit réunis.
   Non imputable à exit 60 (le RSI n'a jamais approché 55 non plus).
   Arbitrage stop/durée → dans les leviers du LLM (`max_hold_candles`,
   `stop_loss_pct`).
3. **Trop tôt pour juger exit 60** : 3 trades post-changement (0/3, dominés
   par le max_hold ci-dessus). Prochaine réflexion dans ~8 trades.
4. Mineur : adaptateur macro (Stooq) en `offline_fallback` — sans conséquence
   (données inutilisées par les décisions).

## Lecture d'ensemble

La nuit valide l'**infrastructure** (mesure juste, surveillance, première
amélioration motivée). Elle ne valide pas encore la **stratégie** (−11 $ /
16 h, mode de perte identifié : entrée en chute prolongée → sortie max_hold
au pire moment). Ce mode de perte est désormais visible dans les données que
la réflexion lit. À réévaluer après les deux prochaines réflexions.

## Détail des trades

| TS (UTC) | Sortie | Brut $ | Net $ | RSI sortie | Durée |
|---|---|---|---|---|---|
| 06-11 13:17 | rsi_reversion | +2.39 | +0.39 | 55.2 | 5 m |
| 06-11 13:44 | rsi_reversion | +7.65 | +5.65 | 57.8 | 13 m |
| 06-11 16:36 | rsi_reversion | +4.82 | +2.82 | 65.0 | 8 m |
| 06-11 17:17 | rsi_reversion | −4.63 | −6.63 | 60.3 | 27 m |
| 06-11 18:36 | rsi_reversion | +5.79 | +3.79 | 61.4 | 7 m |
| 06-11 20:52 | rsi_reversion | +1.54 | −0.46 | 57.4 | 24 m |
| 06-11 21:39 | rsi_reversion | +2.45 | +0.45 | 57.2 | 12 m |
| 06-11 22:55 | rsi_reversion | +1.57 | −0.43 | 56.2 | 20 m |
| 06-11 23:22 | rsi_reversion | +0.76 | −1.24 | 55.8 | 14 m |
| 06-12 01:17 | rsi_reversion | +0.55 | −1.45 | 55.6 | 16 m |
| 06-12 01:43 | rsi_reversion | +2.21 | +0.21 | 57.9 | 13 m |
| — 01:44 : réflexion v03, exit RSI 55 → 60 — | | | | | |
| 06-12 03:37 | rsi_reversion | +0.75 | −1.25 | 64.6 | 17 m |
| 06-12 05:13 | max_hold | −10.61 | −12.61 | 12.7 | 30 m |
| 06-12 05:26 | rsi_reversion | +1.55 | −0.45 | 60.0 | 12 m |
