# Dashboard V5 — design validé

## Objectif

Transformer la zone marché du dashboard paper en terminal lisible et dense, inspiré du prototype validé et de la référence fournie, sans modifier le moteur de trading.

## Décisions

- Un seul portefeuille paper reste la source de vérité pour BTC/USDT, ETH/USDT et PAXG/USDT.
- Les onglets changent uniquement l'actif observé ; ils ne créent pas de sous-portefeuilles.
- Le graphique d'observation utilise des bougies Binance 1 h pour une lecture comparable entre actifs.
- Le moteur et son timeframe natif restent affichés séparément : AK MACD 4 h, Donchian 1 j, COT 1 j.
- Les entrées/sorties paper réelles et les marqueurs de stratégie restent distincts visuellement.
- Les événements IN/OUT/TP/SL ont un libellé, un trait court vers la bougie, un lien entrée→sortie et une ligne verticale fine vers la timeline.
- L'éventail à droite représente des scénarios de stress déterministes basés sur la volatilité récente. Il est étiqueté comme scénario, n'est pas une prévision probabiliste et n'alimente aucune décision.
- Le fond devient gris anthracite, la typographie est plus fine et compacte, les bougies restent vertes/rouges et les couleurs ont une sémantique stable.

## Échelle et lisibilité

- Fenêtre observée : environ 120 bougies 1 h.
- Échelle verticale : plus bas/plus haut visibles, avec 8 % de marge.
- Zone prix dominante ; volumes compacts en dessous ; indicateur de momentum séparé.
- Corps et mèches fins, espacement faible mais non nul.
- Les scénarios sont sous-pixel ou proches du pixel ; la trajectoire centrale jaune reste légèrement plus visible.

## Sécurité et compatibilité

- Aucun POST, ordre, paramètre de risque ou fichier d'état n'est modifié.
- `/api/state` conserve ses champs existants ; les métadonnées d'affichage sont additives.
- En cas d'échec d'un actif, les autres actifs et le reste du dashboard continuent de fonctionner.
- Sauvegarde de rollback : `backups/dashboard-pre-v5-20260711/`.

## Validation

- Tests unitaires du contrat multi-actifs et du fallback.
- Tests statiques du DOM et des éléments sémantiques du nouveau terminal.
- Vérification de syntaxe Python et JavaScript.
- Vérification visuelle desktop et largeur réduite avec l'API réelle.

