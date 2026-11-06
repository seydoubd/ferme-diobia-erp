"""Rôles et permissions granulaires."""
from functools import wraps
from flask import abort
from flask_login import current_user

PERMISSIONS = {
    "dashboard.voir": "Voir le tableau de bord opérationnel",
    "finances.voir": "Voir les bénéfices, marges et coûts",
    "couts.modifier": "Modifier les prix de revient et coûts d'achat",
    "produits.gerer": "Créer / modifier les produits et catégories",
    "stock.voir": "Consulter les stocks",
    "stock.mouvement": "Entrées, sorties, pertes, mortalités, transferts",
    "stock.inventaire": "Réaliser les inventaires",
    "transformation.gerer": "Réaliser les transformations",
    "achats.voir": "Consulter les achats",
    "achats.gerer": "Créer bons de commande et réceptions",
    "fournisseurs.gerer": "Gérer les fournisseurs",
    "ventes.voir": "Consulter les ventes",
    "ventes.creer": "Créer des ventes",
    "ventes.annuler": "Annuler une vente / créer un avoir",
    "ventes.prix_libre": "Modifier le prix proposé / remise > 10 %",
    "ventes.sans_stock": "Vendre au-delà du stock disponible",
    "clients.gerer": "Gérer les clients",
    "encaissements.gerer": "Encaisser les règlements clients",
    "decaissements.gerer": "Payer les fournisseurs",
    "commandes.gerer": "Gérer les commandes clients",
    "livraisons.gerer": "Préparer et livrer les commandes",
    "depenses.gerer": "Saisir les dépenses",
    "tresorerie.voir": "Voir la trésorerie",
    "tresorerie.gerer": "Transferts, rapprochements",
    "compta.voir": "Consulter la comptabilité",
    "compta.gerer": "Valider écritures, OD, clôturer les périodes",
    "rh.gerer": "Gérer les employés et la paie",
    "rapports.voir": "Générer les rapports",
    "parametres.gerer": "Paramètres de l'application",
    "users.gerer": "Gérer les utilisateurs",
    "audit.voir": "Consulter le journal d'audit",
}

ROLES = {
    "admin": {"libelle": "Administrateur / Propriétaire", "perms": ["*"]},
    "stock": {"libelle": "Responsable de stock", "perms": [
        "dashboard.voir", "produits.gerer", "stock.voir", "stock.mouvement", "stock.inventaire",
        "transformation.gerer", "achats.voir", "achats.gerer", "livraisons.gerer", "commandes.gerer", "rapports.voir"]},
    "commercial": {"libelle": "Commercial / Caissier", "perms": [
        "dashboard.voir", "stock.voir", "ventes.voir", "ventes.creer", "clients.gerer",
        "encaissements.gerer", "commandes.gerer"]},
    "comptable": {"libelle": "Comptable", "perms": [
        "dashboard.voir", "finances.voir", "stock.voir", "achats.voir", "ventes.voir", "depenses.gerer",
        "decaissements.gerer", "encaissements.gerer", "tresorerie.voir", "tresorerie.gerer",
        "compta.voir", "compta.gerer", "rapports.voir", "rh.gerer", "audit.voir"]},
    "entrepot": {"libelle": "Responsable d'entrepôt", "perms": [
        "dashboard.voir", "stock.voir", "achats.voir", "achats.gerer", "livraisons.gerer", "commandes.gerer"]},
}


def require(perm):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.can(perm):
                abort(403)
            return f(*a, **kw)
        return wrapper
    return deco
