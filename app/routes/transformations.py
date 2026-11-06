from flask import Blueprint, render_template, request, url_for, jsonify
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Transformation, Produit, Entrepot, CompteTresorerie, Lot
from ..permissions import require
from ..services import transformation as svc, stock
from ..utils import BusinessError, to_float, to_datetime
from . import run_action, lignes_form

bp = Blueprint("transformations", __name__, url_prefix="/transformations")


@bp.before_request
@login_required
def _auth():
    pass


def _refs():
    vivants = Produit.query.filter_by(type_produit="vivant", actif=True).all()
    return {"vivants": vivants,
            "sorties": Produit.query.filter(Produit.actif.is_(True), Produit.type_produit.in_(["abattu", "decoupe", "abats"]))
            .order_by(Produit.type_produit, Produit.nom).all(),
            "entrepots": Entrepot.query.filter_by(actif=True).order_by(Entrepot.nom).all(),
            "comptes": CompteTresorerie.query.filter_by(actif=True).all(), "METHODES": svc.METHODES,
            "lots_vivants": Lot.query.filter(Lot.produit_id.in_([p.id for p in vivants]), Lot.qte_restante > 0)
            .order_by(Lot.date_entree).all()}


@bp.route("/")
@require("transformation.gerer")
def index():
    ts = Transformation.query.order_by(Transformation.date.desc()).limit(200).all()
    term = [t for t in ts if t.statut == "terminee"]
    stats = {"nb": sum(t.nb_poulets for t in term), "poids_vif": sum(t.poids_vif_total for t in term),
             "poids_obtenu": sum(t.poids_obtenu for t in term), "frais": sum(t.frais_total for t in term),
             "pertes": sum(t.valeur_pertes for t in term)}
    stats["rendement"] = round(stats["poids_obtenu"] / stats["poids_vif"] * 100, 1) if stats["poids_vif"] else 0
    return render_template("transformations/index.html", ts=ts, stats=stats)


def _produits_form():
    rows = lignes_form("produit_id", "quantite", "poids_kg", "coef", "part_pct", "etat")
    return [r for r in rows if to_float(r["quantite"]) > 0]


@bp.route("/nouvelle", methods=["GET", "POST"])
@require("transformation.gerer")
def nouvelle():
    if request.method == "POST":
        f = request.form

        def go():
            d = to_datetime(f.get("date"))
            t = svc.demarrer(f.get("produit_source_id", type=int), f.get("entrepot_source_id", type=int),
                             f.get("entrepot_dest_id", type=int), int(to_float(f.get("nb_poulets"))), d,
                             f.get("responsable", ""), to_float(f.get("poids_vif_total")) or None,
                             f.get("lot_source_id", type=int) or None, f.get("note", ""))
            if f.get("terminer"):
                svc.terminer(t, _produits_form(), f.get("methode", "poids"), to_float(f.get("frais_abattage")),
                             to_float(f.get("frais_main_oeuvre")), to_float(f.get("autres_frais")),
                             f.get("compte_frais_id", type=int), d)
            return t
        return run_action(go, lambda t: f"Transformation {t.numero} enregistrée : {t.nb_poulets} poulets sortis du stock vivant"
                          + (f", {t.poids_obtenu} kg de produits entrés en stock." if t.statut == "terminee" else " (en cours)."),
                          lambda t: url_for("transformations.detail", tid=t.id))
    return render_template("transformations/nouvelle.html", **_refs())


@bp.route("/<int:tid>")
@require("transformation.gerer")
def detail(tid):
    return render_template("transformations/detail.html", t=db.get_or_404(Transformation, tid), **_refs())


@bp.route("/<int:tid>/terminer", methods=["POST"])
@require("transformation.gerer")
def terminer(tid):
    t = db.get_or_404(Transformation, tid)
    f = request.form
    return run_action(lambda: svc.terminer(t, _produits_form(), f.get("methode", "poids"), to_float(f.get("frais_abattage")),
                                           to_float(f.get("frais_main_oeuvre")), to_float(f.get("autres_frais")),
                                           f.get("compte_frais_id", type=int), to_datetime(f.get("date"))),
                      "Transformation terminée : produits entrés en stock avec leur coût de revient.",
                      url_for("transformations.detail", tid=t.id))


@bp.route("/<int:tid>/annuler", methods=["POST"])
@require("transformation.gerer")
def annuler(tid):
    t = db.get_or_404(Transformation, tid)
    return run_action(lambda: svc.annuler(t, request.form.get("motif", "")),
                      "Transformation annulée : poulets réintégrés, écritures contre-passées.",
                      url_for("transformations.detail", tid=t.id))


@bp.route("/simuler", methods=["POST"])
@require("transformation.gerer")
def simuler():
    """Aperçu en direct de la répartition des coûts (même algorithme que la validation)."""
    d = request.get_json(force=True)
    try:
        cout_matiere = to_float(d.get("cout_matiere"))
        if not cout_matiere and d.get("nb_poulets"):
            nb = int(to_float(d["nb_poulets"]))
            q = Lot.query.filter(Lot.produit_id == int(d.get("produit_source_id") or 0),
                                 Lot.entrepot_id == int(d.get("entrepot_source_id") or 0), Lot.qte_restante > 0)
            if d.get("lot_source_id"):
                q = q.filter(Lot.id == int(d["lot_source_id"]))
            reste = nb
            for lot in q.order_by(Lot.date_entree, Lot.id).all():
                pris = min(reste, lot.qte_restante)
                cout_matiere += pris * lot.cout_unitaire
                reste -= pris
                if reste <= 0:
                    break
        frais = sum(to_float(d.get(k)) for k in ("frais_abattage", "frais_main_oeuvre", "autres_frais"))
        lignes = []
        for x in d.get("lignes", []):
            p = db.session.get(Produit, int(x.get("produit_id") or 0))
            q = to_float(x.get("quantite"))
            if not p or q <= 0:
                continue
            poids = q if p.unite == "kg" else to_float(x.get("poids_kg"))
            coef = to_float(x.get("coef")) if x.get("coef") not in (None, "") else (p.coef_valeur or 1)
            lignes.append({"produit_id": p.id, "quantite": q, "poids_kg": poids, "coef": coef,
                           "part_pct": to_float(x.get("part_pct")), "unite": p.unite})
        total = cout_matiere + frais
        couts = svc.repartir(total, lignes, d.get("methode", "poids")) if lignes else []
        poids = sum(l["poids_kg"] for l in lignes)
        pv = to_float(d.get("poids_vif_total"))
        return jsonify(ok=True, cout_matiere=round(cout_matiere, 2), frais=frais, cout_total=round(total, 2), poids_obtenu=poids,
                       rendement=round(poids / pv * 100, 2) if pv else None, pertes_kg=round(pv - poids, 2) if pv else None,
                       lignes=[{"produit_id": l["produit_id"], "cout": c, "cu": round(c / l["quantite"], 2),
                                "cu_kg": round(c / l["poids_kg"], 2) if l["poids_kg"] else None} for l, c in zip(lignes, couts)])
    except BusinessError as e:
        return jsonify(ok=False, error=str(e))
