# Exécution du plan validé le 5 septembre 2026

1. Isoler les protections et observations; tests de faute puis régression.
2. Compte LLM + outbox atomiques, reprise avant validation, compatibilité v1.
3. Versionner les temps/prix et traiter la première période, gaps, curseurs.
4. NVDA: calendrier de séance et cadence M5, signaux périmés consommés.
5. OHLCV/COT: validité et publication économique, état dégradé explicite.
6. Conserver le plafond par tranche, exposer le cumul et le risque exécuté.
7. Santé active, frais complets et snapshot partiel du dashboard.
8. Geler la nouvelle méthode, rejouer isolément, préparer la collecte prospective.

Copie de départ: code courant incluant les modifications préexistantes, sans état ni secrets. Répertoire isolé /private/tmp/0rum-corrections-20260905. Comparaison par empreinte avant application; aucun écrasement de divergence. Les reproductions d’audit deviennent des garanties métier. Contrôle final des tests existants et du diff, revue contradictoire des frontières sensibles. Le risque dynamique reste shadow-only, BTC1x, aucun ordre broker et aucune reprise LSE. Les observations prospectives futures ne seront pas annoncées comme déjà acquises.

Retour arrière: conserver code et configuration avant application, conserver états/fills présents; ne jamais rejouer un ancien état par-dessus de nouveaux fills. Reprendre/vider l’outbox avec une version compatible. L’activation du composant sera vérifiée selon AGENTS.md; toute autorisation explicite de redémarrage encore requise sera demandée une fois le résultat testable prêt.
