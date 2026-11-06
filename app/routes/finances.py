import os
import uuid
from datetime import datetime
from flask import Blueprint, render_template, request, url_for, send_from_directory, current_app, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from ..extensions import db
from ..models import CompteTresorerie, MouvementTresorerie, Depense, CategorieDepense, Rapprochement, Client, Entrepot
from ..permissions import require
from ..services import finance, analytics
from ..utils import BusinessError, to_float, to_datetime, to_date, fcfa
from . import run_action, periode_args

bp = Blueprint("finances", __name__)
EXT_OK = {"pdf", "jpg", "jpeg", "png", "webp", "heic"}


@bp.before_request
@login_required
def _auth():
    pass


# ─────────── Trésorerie ───────────
@bp.route("/tresorerie")
@require("tresorerie.voir")
def tresorerie():
    comptes = CompteTresorerie.query.order_by(CompteTresorerie.id).all()
    soldes = {c.id: finance.solde(c) for c in comptes}
    cid = request.args.get("compte", type=int)
    debut, fin = periode_args("30j")
    q = MouvementTresorerie.query.filter(MouvementTresorerie.date >= datetime.combine(debut, datetime.min.time()),
                                         MouvementTresorerie.date <= datetime.combine(fin, datetime.max.time()))
    if cid:
        q = q.filter_by(compte_id=cid)
    mvts = q.order_by(MouvementTresorerie.date.desc(), MouvementTresorerie.id.desc()).limit(500).all()
    t0, prev = finance.prevision(60, 7)
    r = analytics.resultat(debut, fin)
    return render_template("finances/tresorerie.html", comptes=comptes, soldes=soldes, mvts=mvts, cid=cid,
                           debut=debut, fin=fin, total=sum(soldes.values()), prev=prev,
                           entrees=sum(m.montant for m in mvts if m.montant > 0),
                           sorties=-sum(m.montant for m in mvts if m.montant < 0), r=r,
                           creances=sum(x["du"] for x in finance.creances_clients()),
                           dettes=sum(x["du"] for x in finance.dettes_fournisseurs()))


@bp.route("/tresorerie/transfert", methods=["POST"])
@require("tresorerie.gerer")
def transfert():
    f = request.form
    return run_action(lambda: finance.transferer(db.get_or_404(CompteTresorerie, f.get("src", type=int)),
                                                 db.get_or_404(CompteTresorerie, f.get("dest", type=int)),
                                                 to_float(f.get("montant")), f.get("libelle", ""), to_datetime(f.get("date"))),
                      "Transfert enregistré.", url_for("finances.tresorerie"))


@bp.route("/tresorerie/apport", methods=["POST"])
@require("tresorerie.gerer")
def apport():
    f = request.form
    return run_action(lambda: finance.apport(db.get_or_404(CompteTresorerie, f.get("compte_id", type=int)),
                                             to_float(f.get("montant")), to_datetime(f.get("date")),
                                             f.get("libelle") or "Apport de l'associé"),
                      "Apport enregistré.", url_for("finances.tresorerie"))


@bp.route("/tresorerie/comptes", methods=["POST"])
@require("tresorerie.gerer")
def compte_save():
    f = request.form

    def go():
        if f.get("id"):
            c = db.get_or_404(CompteTresorerie, f.get("id", type=int))
            c.nom, c.numero = f.get("nom") or c.nom, f.get("numero")
            c.actif = f.get("actif", "1") == "1"
            return c
        if f.get("type_compte") not in ("caisse", "banque", "wave", "orange_money"):
            raise BusinessError("Type de compte invalide.")
        return finance.creer_compte((f.get("nom") or "").strip(), f["type_compte"], to_float(f.get("solde_initial")),
                                    f.get("numero"))
    return run_action(go, "Compte enregistré.", url_for("finances.tresorerie"))


@bp.route("/tresorerie/rapprochement/<int:cid>", methods=["GET", "POST"])
@require("tresorerie.gerer")
def rapprochement(cid):
    c = db.get_or_404(CompteTresorerie, cid)
    if request.method == "POST":
        f = request.form
        ids = [int(x) for x in f.getlist("mvt")]
        return run_action(lambda: finance.rapprocher(c, to_date(f.get("date_releve")), to_float(f.get("solde_releve")), ids),
                          lambda r: "Rapprochement enregistré" + (f" – écart de {fcfa(r.ecart)} à justifier." if abs(r.ecart) > 0.5
                                                                 else " – aucun écart."),
                          url_for("finances.rapprochement", cid=c.id))
    mvts = MouvementTresorerie.query.filter_by(compte_id=c.id, rapproche=False).order_by(MouvementTresorerie.date).all()
    hist = Rapprochement.query.filter_by(compte_id=c.id).order_by(Rapprochement.created_at.desc()).all()
    return render_template("finances/rapprochement.html", c=c, mvts=mvts, hist=hist, solde=finance.solde(c))


# ─────────── Dépenses ───────────
@bp.route("/depenses")
@require("depenses.gerer")
def depenses():
    debut, fin = periode_args("mois")
    q = Depense.query.filter(Depense.date >= datetime.combine(debut, datetime.min.time()),
                             Depense.date <= datetime.combine(fin, datetime.max.time()))
    if request.args.get("categorie"):
        q = q.filter_by(categorie_id=request.args.get("categorie", type=int))
    deps = q.order_by(Depense.date.desc()).all()
    valides = [d for d in deps if d.statut == "valide"]
    return render_template("finances/depenses.html", deps=deps, debut=debut, fin=fin,
                           cats=CategorieDepense.query.order_by(CategorieDepense.nom).all(),
                           comptes=CompteTresorerie.query.filter_by(actif=True).all(), MODES=finance.MODES,
                           repart=analytics.repartition_depenses(debut, fin),
                           total=sum(d.montant for d in valides),
                           charges=sum(d.montant for d in valides if d.categorie.nature == "charge" and not d.incorporee_stock),
                           incorporees=sum(d.montant for d in valides if d.incorporee_stock),
                           invest=sum(d.montant for d in valides if d.categorie.nature == "investissement"))


@bp.route("/depenses/nouvelle", methods=["POST"])
@require("depenses.gerer")
def depense_nouvelle():
    f = request.form
    fichier = request.files.get("justificatif")

    def go():
        nom = None
        if fichier and fichier.filename:
            ext = fichier.filename.rsplit(".", 1)[-1].lower()
            if ext not in EXT_OK:
                raise BusinessError("Justificatif : formats acceptés PDF, JPG, PNG.")
            nom = f"{uuid.uuid4().hex}_{secure_filename(fichier.filename)}"
            fichier.save(os.path.join(current_app.config["UPLOAD_FOLDER"], nom))
        cat = db.get_or_404(CategorieDepense, f.get("categorie_id", type=int))
        cpt = db.session.get(CompteTresorerie, f.get("compte_id", type=int))
        return finance.creer_depense(cat, to_float(f.get("montant")), cpt, f.get("mode"), to_datetime(f.get("date")),
                                     f.get("description", ""), f.get("beneficiaire", ""), nom)
    return run_action(go, lambda d: f"Dépense {d.numero} de {fcfa(d.montant)} enregistrée.", url_for("finances.depenses"))


@bp.route("/depenses/<int:did>/annuler", methods=["POST"])
@require("depenses.gerer")
def depense_annuler(did):
    d = db.get_or_404(Depense, did)
    return run_action(lambda: finance.annuler_depense(d, request.form.get("motif", "")), "Dépense annulée.",
                      url_for("finances.depenses"))


@bp.route("/depenses/justificatif/<path:nom>")
@require("depenses.gerer")
def justificatif(nom):
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], secure_filename(nom))


@bp.route("/depenses/categories", methods=["POST"])
@require("parametres.gerer")
def categorie_depense():
    f = request.form

    def go():
        c = db.session.get(CategorieDepense, f.get("id", type=int)) if f.get("id") else CategorieDepense()
        c.nom = (f.get("nom") or "").strip()
        c.compte_comptable = f.get("compte_comptable") or "638"
        c.nature = f.get("nature", "charge")
        if not c.nom:
            raise BusinessError("Nom obligatoire.")
        from ..models import CompteComptable
        if not db.session.get(CompteComptable, c.compte_comptable):
            raise BusinessError("Compte comptable inexistant.")
        db.session.add(c)
        return c
    return run_action(go, "Catégorie enregistrée.", url_for("finances.depenses"))


# ─────────── Rentabilité ───────────
@bp.route("/rentabilite")
@require("finances.voir")
def rentabilite():
    debut, fin = periode_args("mois")
    filtres = {}
    if request.args.get("entrepot"):
        filtres["entrepot_id"] = request.args.get("entrepot", type=int)
    if request.args.get("canal"):
        filtres["canal"] = request.args["canal"]
    if request.args.get("client"):
        filtres["client_id"] = request.args.get("client", type=int)
    return render_template("finances/rentabilite.html", debut=debut, fin=fin,
                           r=analytics.resultat(debut, fin, **filtres),
                           produits=analytics.par_dimension("produit", debut, fin, **filtres),
                           clients=analytics.par_dimension("client", debut, fin, **filtres),
                           canaux=analytics.par_dimension("canal", debut, fin, **filtres),
                           circuits=analytics.circuits(debut, fin), pertes=analytics.pertes_detail(
                               *analytics.bornes(debut, fin)), filtres=filtres,
                           entrepots=Entrepot.query.all(), clients_list=Client.query.order_by(Client.nom).all(),
                           mensuel=analytics.serie_mensuelle(6))
