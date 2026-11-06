from flask import Blueprint, render_template, request, url_for, abort
from flask_login import login_required, current_user
from ..extensions import db
from ..models import (Produit, Entrepot, Lot, MouvementStock, Inventaire, OperationStock, VenteLigneLot, Transformation,
                      TransformationSource)
from ..permissions import require
from ..services import stock, transformation as trf
from ..utils import BusinessError, to_float, to_datetime, to_date, audit
from . import run_action

bp = Blueprint("stocks", __name__, url_prefix="/stocks")


@bp.before_request
@login_required
def _auth():
    pass


def _refs():
    return {"produits": Produit.query.filter_by(actif=True).order_by(Produit.type_produit, Produit.nom).all(),
            "entrepots": Entrepot.query.filter_by(actif=True).order_by(Entrepot.nom).all()}


@bp.route("/")
@require("stock.voir")
def index():
    eid = request.args.get("entrepot", type=int)
    etat = stock.etat_stock(eid, request.args.get("type") or None)
    pid = request.args.get("produit", type=int)
    if pid:
        etat = [r for r in etat if r["produit"].id == pid]
    en_cours = Transformation.query.filter_by(statut="en_cours").all()
    return render_template("stocks/index.html", etat=etat, eid=eid, en_cours=en_cours,
                           peremption=stock.lots_proches_peremption(3), alertes=stock.produits_en_alerte(),
                           rappro=stock.rapprochement_stock() if current_user.can("finances.voir") else None,
                           total=sum(r["valeur"] for r in etat), **_refs())


@bp.route("/lots")
@require("stock.voir")
def lots():
    q = Lot.query
    if not request.args.get("tous"):
        q = q.filter(Lot.qte_restante > 0.0001)
    if request.args.get("produit"):
        q = q.filter_by(produit_id=request.args.get("produit", type=int))
    if request.args.get("entrepot"):
        q = q.filter_by(entrepot_id=request.args.get("entrepot", type=int))
    return render_template("stocks/lots.html", lots=q.order_by(Lot.date_entree.desc()).limit(500).all(), **_refs())


@bp.route("/lots/<int:lid>")
@require("stock.voir")
def lot(lid):
    l = db.get_or_404(Lot, lid)
    mvts = MouvementStock.query.filter_by(lot_id=l.id).order_by(MouvementStock.date).all()
    enfants = Lot.query.filter_by(lot_parent_id=l.id).all()
    # Traçabilité aval : transformations ayant utilisé ce lot vivant, ventes servies
    transfos = TransformationSource.query.filter_by(lot_id=l.id).all()
    ventes = VenteLigneLot.query.filter_by(lot_id=l.id).all()
    return render_template("stocks/lot.html", l=l, mvts=mvts, enfants=enfants, transfos=transfos, ventes=ventes,
                           origine=trf.tracabilite_lot(l))


@bp.route("/mouvements")
@require("stock.voir")
def mouvements():
    q = MouvementStock.query
    for k in ("produit", "entrepot"):
        if request.args.get(k):
            q = q.filter(getattr(MouvementStock, k + "_id") == request.args.get(k, type=int))
    if request.args.get("type"):
        q = q.filter(MouvementStock.type_mvt == request.args["type"])
    if request.args.get("debut"):
        q = q.filter(MouvementStock.date >= to_datetime(request.args["debut"]).replace(hour=0, minute=0))
    if request.args.get("fin"):
        q = q.filter(MouvementStock.date <= to_datetime(request.args["fin"]).replace(hour=23, minute=59))
    page = request.args.get("page", 1, type=int)
    pag = q.order_by(MouvementStock.date.desc(), MouvementStock.id.desc()).paginate(page=page, per_page=60)
    return render_template("stocks/mouvements.html", pag=pag, TYPES=stock.TYPES_MVT, **_refs())


@bp.route("/operation", methods=["GET", "POST"])
@require("stock.mouvement")
def operation():
    if request.method == "POST":
        f = request.form
        t = f.get("type_op")

        def go():
            pid, eid, q = f.get("produit_id", type=int), f.get("entrepot_id", type=int), to_float(f.get("quantite"))
            d = to_datetime(f.get("date"))
            motif = f.get("motif", "")
            lot_id = f.get("lot_id", type=int) or None
            if not pid or not eid or q <= 0:
                raise BusinessError("Produit, entrepôt et quantité (> 0) sont obligatoires.")
            if t in stock.TYPES_PERTE:
                return stock.declarer_perte(pid, eid, q, t, motif, d, lot_id)
            if t == "transfert":
                return stock.transferer(pid, eid, f.get("entrepot_dest_id", type=int), q, motif, d)
            if t in ("congele", "endommage"):
                return stock.changer_etat(pid, eid, q, t, motif, d, lot_id)
            if t == "entree":
                if not current_user.can("couts.modifier") and not current_user.can("*"):
                    raise BusinessError("Une entrée valorisée nécessite le droit de modifier les coûts.")
                return stock.entree_manuelle(pid, eid, q, to_float(f.get("cout_unitaire")), motif, d,
                                             to_date(f.get("date_peremption")), to_float(f.get("poids_moyen")) or None)
            raise BusinessError("Type d'opération inconnu.")
        return run_action(go, lambda op: f"Opération {op.numero} enregistrée.", url_for("stocks.operations"))
    return render_template("stocks/operation.html", type_op=request.args.get("type", "perte"), **_refs())


@bp.route("/operations")
@require("stock.voir")
def operations():
    ops = OperationStock.query.order_by(OperationStock.date.desc()).limit(300).all()
    return render_template("stocks/operations.html", ops=ops)


# ─────────── Inventaires ───────────
@bp.route("/inventaires")
@require("stock.inventaire")
def inventaires():
    return render_template("stocks/inventaires.html", invs=Inventaire.query.order_by(Inventaire.date.desc()).all(), **_refs())


@bp.route("/inventaires/nouveau", methods=["POST"])
@require("stock.inventaire")
def inventaire_nouveau():
    return run_action(lambda: stock.creer_inventaire(request.form.get("entrepot_id", type=int), note=request.form.get("note", "")),
                      lambda inv: f"Inventaire {inv.numero} ouvert : saisissez les quantités comptées.",
                      lambda inv: url_for("stocks.inventaire", iid=inv.id))


@bp.route("/inventaires/<int:iid>", methods=["GET", "POST"])
@require("stock.inventaire")
def inventaire(iid):
    inv = db.get_or_404(Inventaire, iid)
    if request.method == "POST":
        def go():
            comptages = {}
            for l in inv.lignes:
                v = request.form.get(f"q_{l.id}")
                if v not in (None, ""):
                    comptages[l.id] = to_float(v)
                    if comptages[l.id] < 0:
                        raise BusinessError("Quantité comptée négative.")
            if request.form.get("action") == "enregistrer":
                for l in inv.lignes:
                    if l.id in comptages:
                        l.qte_physique = comptages[l.id]
                return inv
            return stock.valider_inventaire(inv, comptages)
        msg = "Comptage enregistré (brouillon)." if request.form.get("action") == "enregistrer" else \
            "Inventaire validé : les écarts ont été régularisés et comptabilisés."
        return run_action(go, msg, url_for("stocks.inventaire", iid=inv.id))
    return render_template("stocks/inventaire.html", inv=inv)


# ─────────── Entrepôts ───────────
@bp.route("/entrepots", methods=["GET", "POST"])
@require("stock.voir")
def entrepots():
    if request.method == "POST":
        if not current_user.can("parametres.gerer") and not current_user.can("stock.mouvement"):
            abort(403)

        def go():
            eid = request.form.get("id", type=int)
            e = db.session.get(Entrepot, eid) if eid else Entrepot()
            e.nom = (request.form.get("nom") or "").strip()
            if not e.nom:
                raise BusinessError("Nom obligatoire.")
            e.type_entrepot = request.form.get("type_entrepot", "magasin")
            e.localisation = request.form.get("localisation")
            e.actif = request.form.get("actif", "1") == "1"
            db.session.add(e)
            db.session.flush()
            audit("entrepot", "entrepot", e.id, {"nom": e.nom})
            return e
        return run_action(go, "Entrepôt enregistré.", url_for("stocks.entrepots"))
    es = Entrepot.query.order_by(Entrepot.nom).all()
    vals = {e.id: stock.valeur_stock(entrepot_id=e.id) for e in es}
    return render_template("stocks/entrepots.html", es=es, vals=vals)
