# ADR-014 — Données NVDA paper et sleeve Opening Range

## Statut

Accepté — 2026-08-03

## Contexte

La surface Opening Range de ADR-013 identifiait NVDA mais restait sans données.
Une page sans bougies ne permet ni détection ni paper trade. Le portefeuille
unifié ne connaissait que des symboles Binance et son fournisseur ne pouvait
donc pas alimenter une action américaine.

Le dépôt possède déjà yfinance. Sa documentation officielle expose les
intervalles 5 minutes et 1 jour, mais limite l’intraday aux 60 derniers jours et
précise que l’outil vise les usages personnels, éducatifs et de recherche :
https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html

Une donnée exhaustive SIP serait préférable avant une exécution réelle. La
documentation Alpaca indique que son offre gratuite IEX ne représente qu’une
seule place et environ 2,5 % du volume, tandis que le flux SIP couvre toutes les
places américaines :
https://docs.alpaca.markets/us/docs/historical-stock-data-1

## Décision

- Utiliser Yahoo Finance via yfinance comme source immédiate
  PAPER_RESEARCH pour NVDA M5 et D1.
- Ne jamais qualifier ce flux de broker-grade ou de SIP.
- Accepter uniquement les bougies clôturées, régulières 09:30–16:00 ET,
  ordonnées et conformes aux bornes OHLCV.
- Router uniquement NVDA vers ce fournisseur ; conserver Binance sans
  modification pour BTC, ETH et PAXG.
- Activer la stratégie pure opening_range dans le portefeuille paper unifié
  avec un risque de 0,25 %.
- Autoriser les entrées uniquement de 09:45 à 11:00 ET après une fausse cassure
  clôturée de retour dans la plage 09:30–09:45.
- Exiger Range / ATR(14) D1 supérieur ou égal à 25 %, viser le bord opposé et
  placer le stop à l’extrême de la bougie signal.
- Ne conserver qu’un premier retournement par séance et demander une clôture
  paper sur la dernière bougie régulière.
- Exposer GET /api/opening-range en lecture seule avec provenance, fraîcheur,
  range, signal et dernières bougies.
- Ne connecter aucun broker et ne créer aucun ordre réel.

## Alternatives considérées

### Alpaca Basic / IEX

Rejeté pour le détecteur de référence : le flux temps réel gratuit ne couvre
qu’une fraction du marché américain. Il reste utile pour des tests techniques,
mais ses extrêmes et volumes ne représentent pas la totalité de NVDA.

### Alpaca SIP

Préféré pour une future étape broker-grade, mais différé : il exige un compte,
des identifiants et un abonnement. Cette autorité et cette dépense ne peuvent
pas être supposées.

### Laisser la page sans données

Rejeté : cela empêche toute observation et tout paper trade.

## Conséquences

- La page montre de vraies bougies NVDA et l’état de leur qualité.
- La sleeve peut ouvrir et fermer des positions uniquement dans le ledger paper.
- Une panne ou une bougie invalide isole NVDA et n’arrête pas les stratégies
  Binance.
- L’historique yfinance M5 ne suffit pas à un backtest long terme ; une source
  historique sous licence reste nécessaire avant promotion.
- Les jours fériés Nasdaq ne sont pas encore fournis par un calendrier officiel.
  L’absence de bougies empêche néanmoins une entrée plutôt que d’en inventer une.
- Les coûts réels, la qualité SIP et l’exécution broker restent des verrous
  explicites avant tout passage hors paper.

## Amendement — D1 Yahoo partiel (2026-08-04)

Yahoo peut publier une ligne D1 de séance terminée dont Open, High, Low et
Volume sont finis mais dont Close reste temporairement absent. Rejeter cette
ligne est correct pour le moteur paper, mais ne doit pas effacer les M5 valides
de la surface opérateur.

Le dashboard peut donc écarter uniquement une ligne D1 répondant à toutes les
conditions suivantes : elle est l’unique ligne non finie, son timestamp est le
maximum unique du lot, seul Close est non fini, et toutes les lignes antérieures
restent conformes. L’API expose alors `status=DEGRADED_PAPER` et un `warning`
daté, conserve les M5 en lecture seule, et suspend tout nouveau signal. Une
ligne non finie plus ancienne, plusieurs lignes non finies, un timestamp
dupliqué ou une violation des bornes OHLCV rend toujours le flux indisponible.

Cette tolérance est explicitement demandée par le snapshot du dashboard. Le
fournisseur utilisé par le portefeuille paper conserve son comportement strict
par défaut ; aucune entrée n’est réactivée à partir d’un D1 dégradé et aucun
prix n’est synthétisé depuis les M5.

## Retour arrière

Retirer la sleeve nvda_opening_range des deux configurations, supprimer son
registre et le dispatch NVDA, puis retirer GET /api/opening-range. Les positions
paper NVDA éventuellement ouvertes doivent d’abord être clôturées dans le ledger
paper ; aucun ordre broker n’est concerné.
