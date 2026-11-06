from flask import Blueprint, render_template, request, url_for, abort
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Produit, Categorie, Lot, VenteLigne
from ..permissions import require
from ..services import stock
from ..utils import BusinessError, to_float, audit
from . import run_action

bp = Blueprint("produits", __name__, url_prefix="/produits")
TYPES = {"vivant": "Poulet vivant", "abattu": "Poulet abattu / entier", "decoupe": "Découpe", "abats": "Abats",
         "emballage": "Emballage", "consommable": "Consommable"}
UNITES = ["kg", "pièce", "tête", "carton", "sac", "litre"]


@bp.before_request
@login_required
def _auth():
    pass


@bp.route("/")
@require("produits.gerer")
def index():
    produits = Produit.query.order_by(Produit.type_produit, Produit.nom).all()
    stocks = {p.id: stock.stock_physique(p.id) for p in produits}
    couts = {p.id: stock.cmup(p.id) for p in produits} if current_user.can("finances.voir") else {}
    return render_template("produits/index.html", produits=produits, stocks=stocks, couts=couts, TYPES=TYPES,
                           categories=Categorie.query.order_by(Categorie.nom).all())


def _save(p):
    f = request.form
    nom = (f.get("nom") or "").strip()
    if not nom:
        raise BusinessError("Le nom du produit est obligatoire.")
    if f.get("type_produit") not in TYPES:
        raise BusinessError("Type de produit invalide.")
    p.nom, p.code = nom, (f.get("code") or "").strip().upper() or None
    p.categorie_id = f.get("categorie_id", type=int)
    p.type_produit, p.unite = f["type_produit"], f.get("unite") or "kg"
    for champ in ("prix_gros", "prix_revendeur", "prix_restaurant", "prix_detail", "seuil_alerte", "coef_valeur"):
        setattr(p, champ, to_float(f.get(champ)))
    p.duree_conservation_jours = int(to_float(f.get("duree_conservation_jours")))
    if current_user.can("couts.modifier"):
        p.prix_revient = to_float(f.get("prix_revient"))
    p.soumis_tva = bool(f.get("soumis_tva"))
    p.vendable = bool(f.get("vendable"))
    p.actif = bool(f.get("actif"))
    if any(x < 0 for x in (p.prix_gros, p.prix_detail, p.prix_revendeur, p.prix_restaurant, p.seuil_alerte)):
        raise BusinessError("Les prix et seuils doivent être positifs.")
    q = Produit.query.filter(Produit.code == p.code, Produit.id != (p.id or 0)) if p.code else None
    if q is not None and q.first():
        raise BusinessError("Ce code produit existe déjà.")
    return p


@bp.route("/nouveau", methods=["GET", "POST"])
@bp.route("/<int:pid>", methods=["GET", "POST"])
@require("produits.gerer")
def edit(pid=None):
    p = db.session.get(Produit, pid) if pid else Produit(actif=True, vendable=True, coef_valeur=1, unite="kg")
    if pid and not p:
        abort(404)
    if request.method == "POST":
        def go():
            _save(p)
            if not p.id:
                db.session.add(p)
            db.session.flush()
            audit("produit_modifie" if pid else "produit_cree", "produit", p.id, {"nom": p.nom})
            return p
        return run_action(go, "Produit enregistré.", url_for("produits.index"))
    return render_template("produits/edit.html", p=p, TYPES=TYPES, UNITES=UNITES,
                           categories=Categorie.query.order_by(Categorie.nom).all())


@bp.route("/<int:pid>/supprimer", methods=["POST"])
@require("produits.gerer")
def supprimer(pid):
    p = db.get_or_404(Produit, pid)

    def go():
        if Lot.query.filter_by(produit_id=p.id).count() or VenteLigne.query.filter_by(produit_id=p.id).count():
            p.actif = False
            audit("produit_desactive", "produit", p.id)
            return "desactive"
        audit("produit_supprime", "produit", p.id, {"nom": p.nom})
        db.session.delete(p)
        return "supprime"
    return run_action(go, lambda r: "Produit désactivé (il a un historique : il ne peut pas être supprimé)."
                      if r == "desactive" else "Produit supprimé.", url_for("produits.index"))


@bp.route("/categories", methods=["POST"])
@require("produits.gerer")
def categorie_save():
    def go():
        cid = request.form.get("id", type=int)
        nom = (request.form.get("nom") or "").strip()
        if request.form.get("action") == "supprimer":
            c = db.get_or_404(Categorie, cid)
            if Produit.query.filter_by(categorie_id=c.id).count():
                raise BusinessError("Catégorie utilisée par des produits : réaffectez-les avant de la supprimer.")
            db.session.delete(c)
            return c
        if not nom:
            raise BusinessError("Nom de catégorie obligatoire.")
        c = db.session.get(Categorie, cid) if cid else Categorie()
        c.nom = nom
        c.description = request.form.get("description")
        db.session.add(c)
        return c
    return run_action(go, "Catégories mises à jour.", url_for("produits.index") + "#categories")
