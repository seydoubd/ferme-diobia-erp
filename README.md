# La Ferme Diobia — ERP avicole

Application de gestion complète d'une entreprise d'achat, de stockage, de transformation et de vente de poulets : stocks par lots (FIFO), découpe avec répartition des coûts, ventes en gros et au détail, crédits clients, trésorerie (caisse, banque, Wave, Orange Money), dépenses, paie et comptabilité SYSCOHADA révisé générée automatiquement.

Design et technologies repris de **Teranga Sip Manager** (Python / Flask, application web installable sur téléphone), aux couleurs vert et orange.

---

## Démarrer sur le Mac

1. Double-cliquez sur **`Demarrer_FermeDiobia.command`**. La première fois, macOS peut demander une confirmation : clic droit → *Ouvrir*.
2. Le navigateur s'ouvre sur `http://127.0.0.1:5060`.
3. Connectez-vous :

| Rôle | E-mail | Mot de passe |
|---|---|---|
| Administrateur / propriétaire | `admin@fermediobia.sn` | `Diobia2026!` |
| Responsable de stock (démo) | `stock@fermediobia.sn` | `Demo2026!` |
| Commercial / caissier (démo) | `caisse@fermediobia.sn` | `Demo2026!` |
| Comptable (démo) | `compta@fermediobia.sn` | `Demo2026!` |
| Responsable d'entrepôt (démo) | `entrepot@fermediobia.sn` | `Demo2026!` |

**Changez le mot de passe administrateur** dès la première connexion (*Paramètres → Changer mon mot de passe*).

Pour arrêter : double-cliquez sur `Arreter_FermeDiobia.command`. Une sauvegarde de la base est faite automatiquement à chaque démarrage dans `sauvegardes/` (30 dernières conservées).

Prérequis : Python 3.10 ou plus récent (déjà présent sur la plupart des Mac ; sinon python.org).

### Données de démonstration
Au premier lancement, un jeu de données fictif et réaliste est chargé : 3 fournisseurs, 20 clients, 3 entrepôts, 5 lots de poulets vivants, des transformations, des ventes en gros et au détail, des paiements partiels, des factures échues, des dépenses et des salaires. Ces données sont marquées « démo » et se suppriment en un clic : **Paramètres → Supprimer les données de démo**. Le catalogue de produits, le plan comptable et les paramètres sont conservés.

---

## Modules

| Module | Contenu |
|---|---|
| Tableau de bord | Les 12 questions du dirigeant : poulets disponibles, valeur des stocks, vendus sur pied, transformés, kilos de cuisses / blancs / pilons, produit le plus rentable, CA, bénéfice réel, trésorerie, créances, dettes, rentabilité de chaque activité. Graphiques et filtres par période. |
| Ventes | Caisse rapide, tarif automatique selon le profil du client (grossiste, revendeur, restaurant, particulier), remises contrôlées, paiement espèces / Wave / Orange Money / virement / chèque / crédit, paiement partiel, facture A4, reçu ticket, PDF, partage WhatsApp, avoirs (retours clients), annulation tracée. |
| Commandes & livraisons | En attente → confirmée (stock **réservé**) → en préparation → livrée (facture générée). Livreurs, frais de livraison, bon de livraison. |
| Transformation | Démarrage (les poulets sortent du stock vivant, passent « en cours »), saisie des produits obtenus, rendement, pertes, répartition des coûts **au poids**, **par coefficients de valeur** ou **manuelle**, aperçu en direct, traçabilité lot vivant → produits → ventes. |
| Stocks | Stock par entrepôt et par état (frais, congelé, endommagé, en cours), lots et péremptions, historique des mouvements, pertes / mortalités / casse / péremption, transferts, congélation, déclassement, entrées valorisées, inventaires physiques avec régularisation, rapprochement stock physique / comptable. |
| Achats | Bons de commande, réceptions (frais d'approche intégrés au coût de revient), mortalité à la réception, achats à crédit, paiements, retours fournisseurs. |
| Clients / fournisseurs / crédits | Fiches, conditions tarifaires, plafond de crédit, historique, soldes, échéances, relances WhatsApp, factures échues. |
| Trésorerie | Soldes caisse, banque, Wave, Orange Money ; mouvements ; transferts ; rapprochements ; prévision à 60 jours. |
| Dépenses | Catégories (transport, électricité, eau, salaires, loyer, carburant, abattage, emballages, maintenance, chambres froides…), justificatif photo / PDF, distinction charges / investissements. |
| Comptabilité | Plan SYSCOHADA configurable, journaux (ventes, achats, caisse, banque, Wave, OM, OD), grand livre, balance, TVA, compte de résultat, bilan, validation des écritures, contre-passation, clôture mensuelle. |
| RH | Employés, présences, congés, avances, primes, retenues, paie (intégrée aux dépenses, à la trésorerie et à la comptabilité). |
| Rapports | 11 rapports filtrables par période, en PDF et en Excel. |
| Administration | Paramètres de l'entreprise, TVA, utilisateurs, rôles et permissions granulaires, journal d'audit, sauvegarde. |

---

## Règles métier garanties (et testées)

1. Un poulet vendu sur pied sort du stock vivant et génère une vente.
2. Un poulet transformé sort du stock vivant et génère des produits transformés ; **une transformation n'est jamais une vente**.
3. Les produits transformés entrent en stock à leur coût de revient réel (coût des poulets + frais), réparti sans perte d'un seul franc.
4. Les pertes (mortalité, casse, péremption, écarts d'inventaire, perte de poids à la découpe) sont identifiées et valorisées.
5. Les marges sont calculées sur les **coûts réels des lots (FIFO)**, frais de transport et de transformation inclus.
6. Vente à crédit → créance, pas de trésorerie. Paiement partiel → la créance baisse, la trésorerie du compte augmente (ex. 500 000 facturés, 200 000 payés → 300 000 dus).
7. Impossible de vendre une quantité indisponible (stock, lots périmés et réservations exclus), sauf droit « vendre au-delà du stock ».
8. Les dépenses sont séparées des achats de marchandises et des investissements ; les frais de transformation, déjà inclus dans le coût des produits, ne sont pas comptés une seconde fois en charges.
9. Les stocks physiques et comptables se rapprochent (écran *Stocks* et *Comptabilité → États*).
10. Aucune opération validée n'est modifiée ni supprimée : annulations et corrections passent par des opérations inverses tracées (contre-passation). Une même opération ne peut pas générer deux fois la même écriture.
11. Toutes les opérations de stock et financières s'exécutent en une seule transaction : tout est enregistré, ou rien. Les doubles clics et doubles envois de formulaire sont bloqués.
12. Les périodes clôturées sont verrouillées.

Les quatre notions **chiffre d'affaires**, **encaissements**, **bénéfice** et **trésorerie disponible** sont calculées et affichées séparément.

### Schéma comptable (SYSCOHADA révisé, inventaire permanent)
- Achat : D 601 (+ 6015 frais, + 4452 TVA) / C 401 · entrée en stock au coût réel : D 311/312 / C 6031
- Vente : D 411 / C 701 (sur pied) ou 702 (transformés) (+ 706 livraison, + 4431 TVA) · coût des ventes : D 6031 / C 31x ou D 736 / C 361
- Transformation : D 341 / C 311 au démarrage ; D 361 / C 341 + C 736 (frais incorporés) à la fin ; frais : D 637 / C 57x
- Pertes : D 6031 / C 31x ou D 736 / C 361 · règlements : D 5x / C 411, D 401 / C 5x · paie : D 661 / C 4251 / C 5x

À faire valider par votre expert-comptable, notamment le régime de TVA (désactivée par défaut : les produits avicoles non transformés sont généralement exonérés).

---

## Technique

- **Python 3 / Flask**, SQLAlchemy, Flask-Login, protection CSRF, mots de passe chiffrés, limitation des tentatives de connexion.
- Base **SQLite** en local (`instance/diobia.db`), **PostgreSQL** en ligne via la variable `DATABASE_URL`.
- Application web installable (PWA) : sur téléphone, *Ajouter à l'écran d'accueil*. Mode sombre.
- Formats sénégalais : `1 250 000 FCFA`, `12,5 kg`, `06/11/2026`.

```
app/
  models.py            Modèle de données (lots, mouvements, ventes, écritures…)
  permissions.py       Rôles et permissions granulaires
  services/            Règles métier : stock (FIFO), achats, transformation, ventes,
                       finance (trésorerie, règlements, dépenses), compta, rh, analytics, rapports
  routes/              Écrans (un fichier par module)
  templates/, static/  Interface (design Teranga Sip, vert & orange)
  seed.py              Référentiels de base et données de démonstration
tests/                 Scénarios réels + affichage de toutes les pages par rôle
```

### Tests
```bash
python3 -m pip install -r requirements.txt pytest
python3 -m pytest -q
```
Scénarios couverts : achat de 500 poulets, vente directe de 200, transformation de 100, vente de 30 kg de cuisses, paiement partiel, mortalité, inventaire avec écart, annulation de vente, clôture mensuelle, double comptabilisation, réservation de commande, vente sans stock, droits du commercial, données de démo supprimables. Après chaque étape, le test vérifie : équilibre comptable, stock physique = comptabilité, trésorerie = comptes 5x, créances = compte 411, dettes = compte 401.

### Mise en ligne (plus tard, optionnel)
Le projet contient `Procfile` et `railway.json` (comme Teranga Sip) : sur Railway, ajoutez une base PostgreSQL et les variables `SECRET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`.

### Évolution multi-entreprises
Les services centralisent toutes les règles : pour un SaaS multi-entreprises, ajouter une colonne `entreprise_id` aux tables et un filtre par entreprise dans les services suffit, sans toucher aux écrans.
