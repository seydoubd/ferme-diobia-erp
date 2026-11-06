"""Modèle de données — La Ferme Diobia ERP.

Principes :
- Le stock est porté par des LOTS (quantité restante + coût unitaire). La valorisation est FIFO.
- Tout changement de stock passe par un MouvementStock (journal immuable).
- Toute opération financière passe par un MouvementTresorerie et une Ecriture comptable.
- Aucune opération validée n'est modifiée : on annule / régularise par une opération inverse tracée.
- La colonne `demo` identifie les données de démonstration (supprimables en un clic).
"""
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from sqlalchemy import UniqueConstraint, Index
from .extensions import db


def now():
    return datetime.now()


class DemoMixin:
    demo = db.Column(db.Boolean, default=False, nullable=False, index=True)


# ───────────────────────────── Paramètres & sécurité ─────────────────────────────
class Parametre(db.Model):
    cle = db.Column(db.String(60), primary_key=True)
    valeur = db.Column(db.String(500))


class User(UserMixin, DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="commercial")
    permissions_extra = db.Column(db.String(500), default="")  # permissions ajoutées, séparées par des virgules
    actif = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now)

    def set_password(self, pwd):
        self.password_hash = generate_password_hash(pwd)

    def check_password(self, pwd):
        return check_password_hash(self.password_hash, pwd)

    @property
    def is_active(self):
        return self.actif

    def perms(self):
        from .permissions import ROLES
        base = set(ROLES.get(self.role, {}).get("perms", []))
        extra = {p.strip() for p in (self.permissions_extra or "").split(",") if p.strip()}
        return base | extra

    def can(self, perm):
        p = self.perms()
        return "*" in p or perm in p


class AuditLog(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=now, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(60))
    entite = db.Column(db.String(60))
    entite_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    user = db.relationship("User")


class SubmissionToken(db.Model):
    """Protection contre les doubles soumissions : un jeton de formulaire n'est consommé qu'une fois."""
    token = db.Column(db.String(64), primary_key=True)
    used_at = db.Column(db.DateTime, default=now)


# ───────────────────────────── Référentiels ─────────────────────────────
class Categorie(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(200))


class Produit(DemoMixin, db.Model):
    """type_produit : vivant | abattu | decoupe | abats | emballage | consommable"""
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True)
    nom = db.Column(db.String(120), nullable=False)
    categorie_id = db.Column(db.Integer, db.ForeignKey("categorie.id"))
    type_produit = db.Column(db.String(20), nullable=False, default="decoupe")
    unite = db.Column(db.String(15), nullable=False, default="kg")
    prix_revient = db.Column(db.Float, default=0)  # prix de revient standard (le coût réel vient des lots)
    prix_gros = db.Column(db.Float, default=0)
    prix_revendeur = db.Column(db.Float, default=0)
    prix_restaurant = db.Column(db.Float, default=0)
    prix_detail = db.Column(db.Float, default=0)
    seuil_alerte = db.Column(db.Float, default=0)
    duree_conservation_jours = db.Column(db.Integer, default=0)  # 0 = non périssable
    coef_valeur = db.Column(db.Float, default=1.0)  # coefficient de valeur pour la répartition des coûts
    soumis_tva = db.Column(db.Boolean, default=False)
    vendable = db.Column(db.Boolean, default=True)
    actif = db.Column(db.Boolean, default=True)
    categorie = db.relationship("Categorie")

    @property
    def circuit(self):
        return "sur_pied" if self.type_produit == "vivant" else "transforme"

    @property
    def marge_detail(self):
        return (self.prix_detail or 0) - (self.prix_revient or 0)

    @property
    def marge_gros(self):
        return (self.prix_gros or 0) - (self.prix_revient or 0)

    def prix_pour(self, tarif):
        return {
            "grossiste": self.prix_gros, "revendeur": self.prix_revendeur or self.prix_gros,
            "restaurant": self.prix_restaurant or self.prix_detail, "particulier": self.prix_detail,
        }.get(tarif, self.prix_detail) or 0


class Entrepot(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False, unique=True)
    type_entrepot = db.Column(db.String(30), default="magasin")  # ferme | chambre_froide | magasin
    localisation = db.Column(db.String(150))
    actif = db.Column(db.Boolean, default=True)


# ───────────────────────────── Stocks ─────────────────────────────
class Lot(DemoMixin, db.Model):
    """Unité de traçabilité et de valorisation.
    etat : frais | congele | en_cours | endommage
    origine : achat | transformation | ajustement | retour_client | transfert
    """
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    produit_id = db.Column(db.Integer, db.ForeignKey("produit.id"), nullable=False, index=True)
    entrepot_id = db.Column(db.Integer, db.ForeignKey("entrepot.id"), nullable=False, index=True)
    origine = db.Column(db.String(20), nullable=False)
    achat_ligne_id = db.Column(db.Integer, db.ForeignKey("achat_ligne.id"))
    transformation_id = db.Column(db.Integer, db.ForeignKey("transformation.id"))
    lot_parent_id = db.Column(db.Integer, db.ForeignKey("lot.id"))
    date_entree = db.Column(db.DateTime, default=now, nullable=False, index=True)
    date_peremption = db.Column(db.Date)
    qte_initiale = db.Column(db.Float, nullable=False)
    qte_restante = db.Column(db.Float, nullable=False)
    cout_unitaire = db.Column(db.Float, nullable=False, default=0)
    poids_moyen = db.Column(db.Float)  # pour les poulets vivants (kg / tête)
    etat = db.Column(db.String(15), default="frais")
    compte_stock = db.Column(db.String(10), nullable=False, default="312")  # compte SYSCOHADA du lot (311/312/335/322/361)
    produit = db.relationship("Produit")
    entrepot = db.relationship("Entrepot")
    parent = db.relationship("Lot", remote_side=[id])

    @property
    def valeur(self):
        return round(self.qte_restante * self.cout_unitaire, 2)

    @property
    def jours_avant_peremption(self):
        if not self.date_peremption:
            return None
        return (self.date_peremption - date.today()).days


class MouvementStock(DemoMixin, db.Model):
    """Journal immuable des mouvements. quantite > 0 = entrée, < 0 = sortie."""
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=now, index=True)
    type_mvt = db.Column(db.String(30), nullable=False, index=True)
    produit_id = db.Column(db.Integer, db.ForeignKey("produit.id"), nullable=False, index=True)
    lot_id = db.Column(db.Integer, db.ForeignKey("lot.id"), index=True)
    entrepot_id = db.Column(db.Integer, db.ForeignKey("entrepot.id"), nullable=False)
    quantite = db.Column(db.Float, nullable=False)
    cout_unitaire = db.Column(db.Float, default=0)
    valeur = db.Column(db.Float, default=0)
    ref_type = db.Column(db.String(30))
    ref_id = db.Column(db.Integer)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    note = db.Column(db.String(255))
    produit = db.relationship("Produit")
    lot = db.relationship("Lot")
    entrepot = db.relationship("Entrepot")
    user = db.relationship("User")


class OperationStock(DemoMixin, db.Model):
    """Opération manuelle de stock (perte, mortalité, transfert, ajustement, entrée, déclassement…)."""
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(30), unique=True)
    date = db.Column(db.DateTime, default=now, index=True)
    type_op = db.Column(db.String(30), nullable=False)
    entrepot_id = db.Column(db.Integer, db.ForeignKey("entrepot.id"), nullable=False)
    entrepot_dest_id = db.Column(db.Integer, db.ForeignKey("entrepot.id"))
    produit_id = db.Column(db.Integer, db.ForeignKey("produit.id"), nullable=False)
    quantite = db.Column(db.Float, nullable=False)
    valeur = db.Column(db.Float, default=0)
    motif = db.Column(db.String(255))
    statut = db.Column(db.String(10), default="valide")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    entrepot = db.relationship("Entrepot", foreign_keys=[entrepot_id])
    entrepot_dest = db.relationship("Entrepot", foreign_keys=[entrepot_dest_id])
    produit = db.relationship("Produit")
    user = db.relationship("User")


class Reservation(DemoMixin, db.Model):
    """Réservation temporaire de stock pour une commande confirmée."""
    id = db.Column(db.Integer, primary_key=True)
    commande_ligne_id = db.Column(db.Integer, db.ForeignKey("commande_ligne.id"), nullable=False)
    produit_id = db.Column(db.Integer, db.ForeignKey("produit.id"), nullable=False, index=True)
    entrepot_id = db.Column(db.Integer, db.ForeignKey("entrepot.id"), nullable=False)
    quantite = db.Column(db.Float, nullable=False)
    active = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime, default=now)


class Inventaire(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(30), unique=True)
    date = db.Column(db.DateTime, default=now)
    entrepot_id = db.Column(db.Integer, db.ForeignKey("entrepot.id"), nullable=False)
    statut = db.Column(db.String(15), default="brouillon")  # brouillon | valide
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    note = db.Column(db.String(255))
    entrepot = db.relationship("Entrepot")
    lignes = db.relationship("InventaireLigne", backref="inventaire", cascade="all, delete-orphan")


class InventaireLigne(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    inventaire_id = db.Column(db.Integer, db.ForeignKey("inventaire.id"), nullable=False)
    produit_id = db.Column(db.Integer, db.ForeignKey("produit.id"), nullable=False)
    qte_theorique = db.Column(db.Float, default=0)
    qte_physique = db.Column(db.Float, default=0)
    ecart = db.Column(db.Float, default=0)
    valeur_ecart = db.Column(db.Float, default=0)
    produit = db.relationship("Produit")


# ───────────────────────────── Tiers ─────────────────────────────
class Fournisseur(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(150), nullable=False)
    telephone = db.Column(db.String(40))
    adresse = db.Column(db.String(200))
    email = db.Column(db.String(120))
    ninea = db.Column(db.String(40))
    delai_paiement_jours = db.Column(db.Integer, default=0)
    actif = db.Column(db.Boolean, default=True)


CLIENT_TYPES = ["grossiste", "revendeur", "restaurant", "hotel", "supermarche", "boucherie", "particulier"]
TARIF_PAR_TYPE = {"grossiste": "grossiste", "revendeur": "revendeur", "restaurant": "restaurant",
                  "hotel": "restaurant", "supermarche": "revendeur", "boucherie": "revendeur",
                  "particulier": "particulier"}


class Client(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(150), nullable=False)
    telephone = db.Column(db.String(40))
    adresse = db.Column(db.String(200))
    email = db.Column(db.String(120))
    type_client = db.Column(db.String(20), nullable=False, default="particulier")
    tarif = db.Column(db.String(20), default="particulier")  # grille tarifaire appliquée
    remise_defaut_pct = db.Column(db.Float, default=0)
    plafond_credit = db.Column(db.Float, default=0)
    delai_paiement_jours = db.Column(db.Integer, default=0)
    actif = db.Column(db.Boolean, default=True)

    @property
    def canal(self):
        return "detail" if self.type_client == "particulier" else "gros"


# ───────────────────────────── Achats ─────────────────────────────
class Achat(DemoMixin, db.Model):
    """statut : commande (bon de commande) | recu (réception validée) | annule"""
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(30), unique=True)
    fournisseur_id = db.Column(db.Integer, db.ForeignKey("fournisseur.id"), nullable=False)
    entrepot_id = db.Column(db.Integer, db.ForeignKey("entrepot.id"), nullable=False)
    date = db.Column(db.DateTime, default=now)
    date_reception = db.Column(db.DateTime)
    statut = db.Column(db.String(15), default="commande", index=True)
    reference_facture = db.Column(db.String(60))
    frais_transport = db.Column(db.Float, default=0)
    autres_frais = db.Column(db.Float, default=0)
    total_marchandises = db.Column(db.Float, default=0)
    tva = db.Column(db.Float, default=0)
    total_ttc = db.Column(db.Float, default=0)  # montant dû au fournisseur (marchandises + frais facturés + TVA)
    compte_frais_id = db.Column(db.Integer, db.ForeignKey("compte_tresorerie.id"))  # frais réglés à part (transporteur) depuis ce compte
    montant_paye = db.Column(db.Float, default=0)
    montant_avoirs = db.Column(db.Float, default=0)  # retours fournisseurs
    echeance = db.Column(db.Date)
    mortalite_transport = db.Column(db.Integer, default=0)
    note = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    fournisseur = db.relationship("Fournisseur")
    entrepot = db.relationship("Entrepot")
    lignes = db.relationship("AchatLigne", backref="achat", cascade="all, delete-orphan")

    @property
    def solde(self):
        if self.statut != "recu":
            return 0
        return round((self.total_ttc or 0) - (self.montant_paye or 0) - (self.montant_avoirs or 0), 2)

    @property
    def en_retard(self):
        return self.statut == "recu" and self.solde > 0 and self.echeance and self.echeance < date.today()


class AchatLigne(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    achat_id = db.Column(db.Integer, db.ForeignKey("achat.id"), nullable=False)
    produit_id = db.Column(db.Integer, db.ForeignKey("produit.id"), nullable=False)
    quantite = db.Column(db.Float, nullable=False)
    poids_total = db.Column(db.Float)
    prix_unitaire = db.Column(db.Float, nullable=False)
    frais_alloues = db.Column(db.Float, default=0)
    cout_unitaire_final = db.Column(db.Float, default=0)
    date_peremption = db.Column(db.Date)
    produit = db.relationship("Produit")

    @property
    def montant(self):
        return round(self.quantite * self.prix_unitaire, 2)


# ───────────────────────────── Transformation ─────────────────────────────
class Transformation(DemoMixin, db.Model):
    """statut : en_cours (poulets sortis du stock vivant) | terminee | annulee
    methode_repartition : poids | coefficient | manuelle"""
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(30), unique=True)
    date = db.Column(db.DateTime, default=now)
    date_fin = db.Column(db.DateTime)
    entrepot_source_id = db.Column(db.Integer, db.ForeignKey("entrepot.id"), nullable=False)
    entrepot_dest_id = db.Column(db.Integer, db.ForeignKey("entrepot.id"), nullable=False)
    produit_source_id = db.Column(db.Integer, db.ForeignKey("produit.id"), nullable=False)
    lot_source_id = db.Column(db.Integer, db.ForeignKey("lot.id", use_alter=True, name="fk_trf_lot_source"))  # lot imposé (sinon FIFO)
    responsable = db.Column(db.String(120))
    nb_poulets = db.Column(db.Integer, nullable=False)
    poids_vif_total = db.Column(db.Float, default=0)
    frais_abattage = db.Column(db.Float, default=0)
    frais_main_oeuvre = db.Column(db.Float, default=0)
    autres_frais = db.Column(db.Float, default=0)
    compte_frais_id = db.Column(db.Integer, db.ForeignKey("compte_tresorerie.id"))  # compte de paiement des frais
    methode_repartition = db.Column(db.String(15), default="poids")
    cout_matiere = db.Column(db.Float, default=0)
    cout_total = db.Column(db.Float, default=0)
    poids_obtenu = db.Column(db.Float, default=0)
    rendement_pct = db.Column(db.Float, default=0)
    pertes_kg = db.Column(db.Float, default=0)
    valeur_pertes = db.Column(db.Float, default=0)
    statut = db.Column(db.String(15), default="en_cours", index=True)
    note = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    entrepot_source = db.relationship("Entrepot", foreign_keys=[entrepot_source_id])
    entrepot_dest = db.relationship("Entrepot", foreign_keys=[entrepot_dest_id])
    produit_source = db.relationship("Produit")
    sources = db.relationship("TransformationSource", backref="transformation", cascade="all, delete-orphan")
    produits = db.relationship("TransformationProduit", backref="transformation", cascade="all, delete-orphan")

    @property
    def frais_total(self):
        return (self.frais_abattage or 0) + (self.frais_main_oeuvre or 0) + (self.autres_frais or 0)


class TransformationSource(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transformation_id = db.Column(db.Integer, db.ForeignKey("transformation.id"), nullable=False)
    lot_id = db.Column(db.Integer, db.ForeignKey("lot.id"), nullable=False)
    quantite = db.Column(db.Float, nullable=False)
    cout_unitaire = db.Column(db.Float, nullable=False)
    lot = db.relationship("Lot")


class TransformationProduit(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transformation_id = db.Column(db.Integer, db.ForeignKey("transformation.id"), nullable=False)
    produit_id = db.Column(db.Integer, db.ForeignKey("produit.id"), nullable=False)
    quantite = db.Column(db.Float, nullable=False)
    poids_kg = db.Column(db.Float, default=0)  # = quantité si unité kg ; poids réel si pièces
    coef = db.Column(db.Float, default=1)
    part_manuelle_pct = db.Column(db.Float, default=0)
    cout_alloue = db.Column(db.Float, default=0)
    cout_unitaire = db.Column(db.Float, default=0)
    etat = db.Column(db.String(15), default="frais")
    lot_id = db.Column(db.Integer, db.ForeignKey("lot.id"))
    produit = db.relationship("Produit")
    lot = db.relationship("Lot", foreign_keys=[lot_id])


# ───────────────────────────── Ventes ─────────────────────────────
class Vente(DemoMixin, db.Model):
    """statut : valide | annulee"""
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(30), unique=True)
    date = db.Column(db.DateTime, default=now, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    entrepot_id = db.Column(db.Integer, db.ForeignKey("entrepot.id"), nullable=False)
    commande_id = db.Column(db.Integer, db.ForeignKey("commande.id"))
    canal = db.Column(db.String(10), default="detail")  # gros | detail
    tarif = db.Column(db.String(20))
    remise_globale = db.Column(db.Float, default=0)
    frais_livraison = db.Column(db.Float, default=0)
    total_ht = db.Column(db.Float, default=0)
    tva = db.Column(db.Float, default=0)
    total_ttc = db.Column(db.Float, default=0)
    cout_total = db.Column(db.Float, default=0)
    montant_paye = db.Column(db.Float, default=0)
    montant_avoirs = db.Column(db.Float, default=0)
    echeance = db.Column(db.Date)
    statut = db.Column(db.String(15), default="valide", index=True)
    stock_force = db.Column(db.Boolean, default=False)
    motif_annulation = db.Column(db.String(255))
    note = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    client = db.relationship("Client")
    entrepot = db.relationship("Entrepot")
    user = db.relationship("User")
    lignes = db.relationship("VenteLigne", backref="vente", cascade="all, delete-orphan")

    @property
    def solde(self):
        if self.statut == "annulee":
            return 0
        return round((self.total_ttc or 0) - (self.montant_paye or 0) - (self.montant_avoirs or 0), 2)

    @property
    def statut_paiement(self):
        if self.statut == "annulee":
            return "annulee"
        if self.solde <= 0.5:
            return "payee"
        if (self.montant_paye or 0) > 0 or (self.montant_avoirs or 0) > 0:
            return "partielle"
        return "impayee"

    @property
    def en_retard(self):
        return self.solde > 0.5 and self.echeance and self.echeance < date.today()

    @property
    def marge(self):
        return round((self.total_ht or 0) - (self.cout_total or 0), 2)


class VenteLigne(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vente_id = db.Column(db.Integer, db.ForeignKey("vente.id"), nullable=False)
    produit_id = db.Column(db.Integer, db.ForeignKey("produit.id"), nullable=False, index=True)
    quantite = db.Column(db.Float, nullable=False)
    prix_suggere = db.Column(db.Float, default=0)
    prix_unitaire = db.Column(db.Float, nullable=False)
    remise_pct = db.Column(db.Float, default=0)
    montant_ht = db.Column(db.Float, default=0)
    tva = db.Column(db.Float, default=0)
    cout_total = db.Column(db.Float, default=0)
    qte_retournee = db.Column(db.Float, default=0)
    produit = db.relationship("Produit")
    lots = db.relationship("VenteLigneLot", backref="ligne", cascade="all, delete-orphan")

    @property
    def cout_unitaire(self):
        return self.cout_total / self.quantite if self.quantite else 0


class VenteLigneLot(DemoMixin, db.Model):
    """Traçabilité FIFO : quels lots ont servi la ligne de vente."""
    id = db.Column(db.Integer, primary_key=True)
    ligne_id = db.Column(db.Integer, db.ForeignKey("vente_ligne.id"), nullable=False)
    lot_id = db.Column(db.Integer, db.ForeignKey("lot.id"), nullable=False)
    quantite = db.Column(db.Float, nullable=False)
    cout_unitaire = db.Column(db.Float, nullable=False)
    lot = db.relationship("Lot")


class Avoir(DemoMixin, db.Model):
    """Retour client : remet (ou non) en stock et réduit la créance."""
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(30), unique=True)
    date = db.Column(db.DateTime, default=now)
    vente_id = db.Column(db.Integer, db.ForeignKey("vente.id"), nullable=False)
    vente_ligne_id = db.Column(db.Integer, db.ForeignKey("vente_ligne.id"), nullable=False)
    quantite = db.Column(db.Float, nullable=False)
    montant_ht = db.Column(db.Float, default=0)
    tva = db.Column(db.Float, default=0)
    montant_ttc = db.Column(db.Float, default=0)
    cout = db.Column(db.Float, default=0)
    remise_en_stock = db.Column(db.Boolean, default=True)
    motif = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    vente = db.relationship("Vente", backref="avoirs")
    ligne = db.relationship("VenteLigne")


# ───────────────────────────── Trésorerie & paiements ─────────────────────────────
class CompteTresorerie(DemoMixin, db.Model):
    """type_compte : caisse | banque | wave | orange_money"""
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False, unique=True)
    type_compte = db.Column(db.String(20), nullable=False)
    compte_comptable = db.Column(db.String(10), nullable=False)
    journal_code = db.Column(db.String(5), nullable=False)
    numero = db.Column(db.String(60))
    solde_initial = db.Column(db.Float, default=0)
    actif = db.Column(db.Boolean, default=True)


class MouvementTresorerie(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=now, index=True)
    compte_id = db.Column(db.Integer, db.ForeignKey("compte_tresorerie.id"), nullable=False, index=True)
    montant = db.Column(db.Float, nullable=False)  # + entrée / - sortie
    type_mvt = db.Column(db.String(30), nullable=False)  # encaissement_client, paiement_fournisseur, depense, salaire, transfert, apport, ...
    libelle = db.Column(db.String(255))
    ref_type = db.Column(db.String(30))
    ref_id = db.Column(db.Integer)
    rapproche = db.Column(db.Boolean, default=False)
    rapprochement_id = db.Column(db.Integer, db.ForeignKey("rapprochement.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    compte = db.relationship("CompteTresorerie")


class Rapprochement(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    compte_id = db.Column(db.Integer, db.ForeignKey("compte_tresorerie.id"), nullable=False)
    date_releve = db.Column(db.Date, nullable=False)
    solde_releve = db.Column(db.Float, nullable=False)
    solde_comptable = db.Column(db.Float, nullable=False)
    ecart = db.Column(db.Float, default=0)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=now)
    compte = db.relationship("CompteTresorerie")


class Paiement(DemoMixin, db.Model):
    """sens : encaissement (client) | decaissement (fournisseur). statut : valide | annule"""
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(30), unique=True)
    date = db.Column(db.DateTime, default=now, index=True)
    sens = db.Column(db.String(15), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))
    fournisseur_id = db.Column(db.Integer, db.ForeignKey("fournisseur.id"))
    compte_id = db.Column(db.Integer, db.ForeignKey("compte_tresorerie.id"), nullable=False)
    mode = db.Column(db.String(20), nullable=False)  # especes | wave | orange_money | virement | cheque
    montant = db.Column(db.Float, nullable=False)
    reference = db.Column(db.String(80))
    statut = db.Column(db.String(10), default="valide")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    client = db.relationship("Client")
    fournisseur = db.relationship("Fournisseur")
    compte = db.relationship("CompteTresorerie")
    allocations = db.relationship("PaiementAllocation", backref="paiement", cascade="all, delete-orphan")


class PaiementAllocation(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    paiement_id = db.Column(db.Integer, db.ForeignKey("paiement.id"), nullable=False)
    vente_id = db.Column(db.Integer, db.ForeignKey("vente.id"))
    achat_id = db.Column(db.Integer, db.ForeignKey("achat.id"))
    montant = db.Column(db.Float, nullable=False)
    vente = db.relationship("Vente", backref="allocations")
    achat = db.relationship("Achat", backref="allocations")


# ───────────────────────────── Dépenses ─────────────────────────────
class CategorieDepense(DemoMixin, db.Model):
    """nature : charge | investissement"""
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(80), unique=True, nullable=False)
    compte_comptable = db.Column(db.String(10), nullable=False)
    nature = db.Column(db.String(15), default="charge")


class Depense(DemoMixin, db.Model):
    """source : manuelle | transformation | paie.
    incorporee_stock=True : la charge est intégrée au coût de revient d'un stock (frais de transformation),
    elle est donc exclue des charges opérationnelles du compte de résultat de gestion (sinon double comptage)."""
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(30), unique=True)
    date = db.Column(db.DateTime, default=now, index=True)
    categorie_id = db.Column(db.Integer, db.ForeignKey("categorie_depense.id"), nullable=False)
    montant = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(255))
    beneficiaire = db.Column(db.String(150))
    compte_id = db.Column(db.Integer, db.ForeignKey("compte_tresorerie.id"))
    mode = db.Column(db.String(20))
    justificatif = db.Column(db.String(255))
    source = db.Column(db.String(20), default="manuelle")
    incorporee_stock = db.Column(db.Boolean, default=False)
    transformation_id = db.Column(db.Integer, db.ForeignKey("transformation.id"))
    paie_id = db.Column(db.Integer, db.ForeignKey("paie.id"))
    statut = db.Column(db.String(10), default="valide")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    categorie = db.relationship("CategorieDepense")
    compte = db.relationship("CompteTresorerie")


# ───────────────────────────── Comptabilité ─────────────────────────────
class CompteComptable(DemoMixin, db.Model):
    numero = db.Column(db.String(10), primary_key=True)
    intitule = db.Column(db.String(150), nullable=False)
    classe = db.Column(db.Integer, nullable=False)


class Journal(DemoMixin, db.Model):
    code = db.Column(db.String(5), primary_key=True)
    libelle = db.Column(db.String(80), nullable=False)


class Periode(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    annee = db.Column(db.Integer, nullable=False)
    mois = db.Column(db.Integer, nullable=False)
    cloturee = db.Column(db.Boolean, default=False)
    cloturee_le = db.Column(db.DateTime)
    cloturee_par = db.Column(db.Integer, db.ForeignKey("user.id"))
    __table_args__ = (UniqueConstraint("annee", "mois"),)


class Ecriture(DemoMixin, db.Model):
    """statut : brouillard (générée, à valider) | valide.
    (source_type, source_id, nature) est unique : une opération ne génère jamais deux fois la même écriture."""
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(30), unique=True)
    journal_code = db.Column(db.String(5), db.ForeignKey("journal.code"), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    libelle = db.Column(db.String(255))
    source_type = db.Column(db.String(30))
    source_id = db.Column(db.Integer)
    nature = db.Column(db.String(30), default="principal")
    statut = db.Column(db.String(12), default="brouillard")
    contre_passation_de = db.Column(db.Integer, db.ForeignKey("ecriture.id"))
    valide_par = db.Column(db.Integer, db.ForeignKey("user.id"))
    valide_le = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=now)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    journal = db.relationship("Journal")
    lignes = db.relationship("LigneEcriture", backref="ecriture", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("source_type", "source_id", "nature", name="uq_ecriture_source"),)

    @property
    def total_debit(self):
        return round(sum(l.debit for l in self.lignes), 2)

    @property
    def total_credit(self):
        return round(sum(l.credit for l in self.lignes), 2)


class LigneEcriture(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ecriture_id = db.Column(db.Integer, db.ForeignKey("ecriture.id"), nullable=False, index=True)
    compte_numero = db.Column(db.String(10), db.ForeignKey("compte_comptable.numero"), nullable=False, index=True)
    libelle = db.Column(db.String(255))
    debit = db.Column(db.Float, default=0)
    credit = db.Column(db.Float, default=0)
    tiers = db.Column(db.String(150))
    compte = db.relationship("CompteComptable")


# ───────────────────────────── RH ─────────────────────────────
class Employe(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(150), nullable=False)
    poste = db.Column(db.String(100))
    telephone = db.Column(db.String(40))
    date_embauche = db.Column(db.Date)
    salaire_base = db.Column(db.Float, default=0)
    actif = db.Column(db.Boolean, default=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))


class Presence(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employe_id = db.Column(db.Integer, db.ForeignKey("employe.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    statut = db.Column(db.String(15), default="present")  # present | absent | retard | conge
    employe = db.relationship("Employe")
    __table_args__ = (UniqueConstraint("employe_id", "date"),)


class Conge(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employe_id = db.Column(db.Integer, db.ForeignKey("employe.id"), nullable=False)
    debut = db.Column(db.Date, nullable=False)
    fin = db.Column(db.Date, nullable=False)
    type_conge = db.Column(db.String(30), default="annuel")
    statut = db.Column(db.String(15), default="demande")  # demande | accorde | refuse
    employe = db.relationship("Employe")


class AvanceSalaire(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employe_id = db.Column(db.Integer, db.ForeignKey("employe.id"), nullable=False)
    date = db.Column(db.DateTime, default=now)
    montant = db.Column(db.Float, nullable=False)
    compte_id = db.Column(db.Integer, db.ForeignKey("compte_tresorerie.id"), nullable=False)
    paie_id = db.Column(db.Integer, db.ForeignKey("paie.id"))  # renseigné quand déduite
    employe = db.relationship("Employe")


class Paie(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employe_id = db.Column(db.Integer, db.ForeignKey("employe.id"), nullable=False)
    annee = db.Column(db.Integer, nullable=False)
    mois = db.Column(db.Integer, nullable=False)
    salaire_base = db.Column(db.Float, default=0)
    primes = db.Column(db.Float, default=0)
    retenues = db.Column(db.Float, default=0)
    avances_deduites = db.Column(db.Float, default=0)
    brut = db.Column(db.Float, default=0)
    net_a_payer = db.Column(db.Float, default=0)
    compte_id = db.Column(db.Integer, db.ForeignKey("compte_tresorerie.id"))
    date_paiement = db.Column(db.DateTime)
    statut = db.Column(db.String(10), default="payee")
    employe = db.relationship("Employe")
    __table_args__ = (UniqueConstraint("employe_id", "annee", "mois"),)


# ───────────────────────────── Commandes & livraisons ─────────────────────────────
class Livreur(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(120), nullable=False)
    telephone = db.Column(db.String(40))
    vehicule = db.Column(db.String(80))
    actif = db.Column(db.Boolean, default=True)


class Commande(DemoMixin, db.Model):
    """statut : attente | confirmee | preparation | livree | annulee"""
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(30), unique=True)
    date = db.Column(db.DateTime, default=now)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    entrepot_id = db.Column(db.Integer, db.ForeignKey("entrepot.id"), nullable=False)
    date_livraison_prevue = db.Column(db.Date)
    adresse_livraison = db.Column(db.String(200))
    livreur_id = db.Column(db.Integer, db.ForeignKey("livreur.id"))
    frais_livraison = db.Column(db.Float, default=0)
    statut = db.Column(db.String(15), default="attente", index=True)
    vente_id = db.Column(db.Integer, db.ForeignKey("vente.id", use_alter=True, name="fk_cmd_vente"))
    date_livraison = db.Column(db.DateTime)
    note = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    client = db.relationship("Client")
    entrepot = db.relationship("Entrepot")
    livreur = db.relationship("Livreur")
    vente = db.relationship("Vente", foreign_keys=[vente_id])
    lignes = db.relationship("CommandeLigne", backref="commande", cascade="all, delete-orphan")

    @property
    def total(self):
        return round(sum(l.quantite * l.prix_unitaire for l in self.lignes) + (self.frais_livraison or 0), 2)


class CommandeLigne(DemoMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    commande_id = db.Column(db.Integer, db.ForeignKey("commande.id"), nullable=False)
    produit_id = db.Column(db.Integer, db.ForeignKey("produit.id"), nullable=False)
    quantite = db.Column(db.Float, nullable=False)
    prix_unitaire = db.Column(db.Float, nullable=False)
    produit = db.relationship("Produit")


Index("ix_lot_fifo", Lot.produit_id, Lot.entrepot_id, Lot.date_entree)
