from flask import Blueprint, render_template, request, url_for
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Achat, AchatLigne, Fournisseur, Produit, Entrepot, CompteTresorerie, Lot
from ..permissions import require
from ..services import achats as svc, finance
from ..utils import BusinessError, to_float, to_datetime, to_date
from . import run_action, lignes_form

bp = Blueprint("achats", __name__, url_prefix="/achats")


@bp.before_request
@login_required
def _auth():
    pass


def _refs():
    return {"fournisseurs": Fournisseur.query.filter_by(actif=True).order_by(Fournisseur.nom).all(),
            "produits": Produit.query.filter_by(actif=True).order_by(Produit.type_produit, Produit.nom).all(),
            "entrepots": Entrepot.query.filter_by(actif=True).order_by(Entrepot.nom).all(),
            "comptes": CompteTresorerie.query.filter_by(actif=True).all(), "MODES": finance.MODES}


@bp.route("/")
@require("achats.voir")
def index():
    q = Achat.query
    if request.args.get("statut"):
        q = q.filter_by(statut=request.args["statut"])
    if request.args.get("fournisseur"):
        q = q.filter_by(fournisseur_id=request.args.get("fournisseur", type=int))
    achats = q.order_by(Achat.date.desc()).limit(300).all()
    recus = [a for a in achats if a.statut == "recu"]
    return render_template("achats/index.html", achats=achats, total=sum(a.total_ttc for a in recus),
                           du=sum(a.solde for a in recus), **_refs())


def _paiement():
    f = request.form
    m = to_float(f.get("paiement_montant"))
    if m > 0:
        return {"montant": m, "compte_id": f.get("paiement_compte_id", type=int), "mode": f.get("paiement_mode", "especes")}
    return None


@bp.route("/nouveau", methods=["GET", "POST"])
@require("achats.gerer")
def nouveau():
    if request.method == "POST":
        f = request.form

        def go():
            lignes = lignes_form("produit_id", "quantite", "prix_unitaire", "poids_total")
            for l in lignes:
                l["quantite"], l["prix_unitaire"] = to_float(l["quantite"]), to_float(l["prix_unitaire"])
                l["poids_total"] = to_float(l["poids_total"]) or None
            a = svc.creer_commande(f.get("fournisseur_id", type=int), f.get("entrepot_id", type=int), lignes,
                                   to_datetime(f.get("date")), to_float(f.get("frais_transport")),
                                   to_float(f.get("autres_frais")), f.get("note", ""), f.get("reference_facture"),
                                   f.get("compte_frais_id", type=int) if f.get("frais_a_part") else None)
            if f.get("receptionner"):
                svc.receptionner(a, to_datetime(f.get("date")), int(to_float(f.get("mortalite"))), _paiement(),
                                 to_date(f.get("echeance")))
            return a
        return run_action(go, lambda a: f"Achat {a.numero} enregistré" + (" et réceptionné : stock mis à jour."
                                                                          if a.statut == "recu" else " (bon de commande)."),
                          lambda a: url_for("achats.detail", aid=a.id))
    return render_template("achats/nouveau.html", **_refs())


@bp.route("/<int:aid>")
@require("achats.voir")
def detail(aid):
    a = db.get_or_404(Achat, aid)
    lots = {l.id: Lot.query.filter_by(achat_ligne_id=l.id).first() for l in a.lignes}
    return render_template("achats/detail.html", a=a, lots=lots, **_refs())


@bp.route("/<int:aid>/receptionner", methods=["POST"])
@require("achats.gerer")
def receptionner(aid):
    a = db.get_or_404(Achat, aid)
    f = request.form
    qtes = {l.id: to_float(f.get(f"q_{l.id}")) for l in a.lignes if f.get(f"q_{l.id}")}
    return run_action(lambda: svc.receptionner(a, to_datetime(f.get("date")), int(to_float(f.get("mortalite"))),
                                               _paiement(), to_date(f.get("echeance")), qtes),
                      "Réception validée : stock et dette fournisseur mis à jour.", url_for("achats.detail", aid=a.id))


@bp.route("/<int:aid>/annuler", methods=["POST"])
@require("achats.gerer")
def annuler(aid):
    a = db.get_or_404(Achat, aid)
    return run_action(lambda: svc.annuler(a, request.form.get("motif", "")), "Achat annulé (écritures contre-passées).",
                      url_for("achats.detail", aid=a.id))


@bp.route("/<int:aid>/retour", methods=["POST"])
@require("achats.gerer")
def retour(aid):
    a = db.get_or_404(Achat, aid)
    l = db.get_or_404(AchatLigne, request.form.get("ligne_id", type=int))
    return run_action(lambda: svc.retour_fournisseur(a, l, to_float(request.form.get("quantite")), request.form.get("motif", "")),
                      "Retour fournisseur enregistré : stock et dette réduits.", url_for("achats.detail", aid=a.id))


@bp.route("/<int:aid>/imprimer")
@require("achats.voir")
def imprimer(aid):
    return render_template("achats/imprimer.html", a=db.get_or_404(Achat, aid))
