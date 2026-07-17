# Dashboard — pins d'événements et raccord scénario

## Design validé

- Les libellés IN, OUT, TP et SL passent à 11–12 px et conservent leur couleur sémantique.
- Chaque événement possède un halo discret, un pin central de 4,2 px et une tige verticale de 1 px jusqu'à la timeline.
- Un point de 3 px est dessiné sur la timeline à l'aplomb de chaque événement.
- Les trades appariés utilisent une liaison plus visible et des points aux deux extrémités.
- La zone future est séparée de l'historique par une ligne verticale discrète.
- Le scénario jaune central est raccordé au dernier close observé par un segment explicite et un nœud de jonction.
- L'éventail reste un scénario d'affichage, jamais une probabilité ni une donnée envoyée au moteur.

## Périmètre

Les changements concernent uniquement `orum/static/dashboard.js`, `orum/static/dashboard.css` et le contrat statique `tests/test_dashboard_terminal.py`.

