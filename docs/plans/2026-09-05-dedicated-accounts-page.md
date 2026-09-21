# Page dédiée aux comptes

Demande : déplacer les huit comptes hors du dashboard principal.

1. Déplacer le rendu existant dans une page /strategy-accounts et son script propre ; conserver la référence partagée dans cette page. Retirer la carte, son rendu et ses entrées des dispositions du dashboard ; garder le lien de navigation.
2. Exposer une lecture dédiée /api/strategy-accounts à partir de la synthèse existante, sans déclencher de collecte marché ni modifier les comptes. Vérifier routes, rendu, dispositions et erreurs réseau.
3. Sauvegarder les fichiers concernés, appliquer avec contrôle des empreintes, recharger le dashboard uniquement, contrôler les URLs et ouvrir la nouvelle page.

Rollback : restaurer les fichiers web sauvegardés puis recharger le dashboard. Aucun état financier ou service de recherche à restaurer. L'empreinte globale actuelle inclut dashboard.py : cette évolution d'interface sera tracée comme changement de code, sans modifier les règles de trading.
