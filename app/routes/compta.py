from datetime import date, datetime
from flask import Blueprint, render_template, request, url_for
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Ecriture, Journal, CompteComptable, Periode, LigneEcriture
from ..permissions import require
from ..services import compta as svc, stock
from ..utils import BusinessError, to_float, to_date, audit
from . import run_action, periode_args, lignes_form

bp = Blueprint("compta", __name__, url_prefix="/comptabilite")


@bp.before_request
@login_required
def _auth():
    pass


@bp.route("/")
@require("compta.voir")
def index():
    debut, fin = periode_args("mois")
    j = request.args.get("journal")
    q = Ecriture.query.filter(Ecriture.date >= debut, Ecriture.date <= fin)
    if j:
        q = q.filter_by(journal_code=j)
    if request.args.get("statut"):
        q = q.filter_by(statut=request.args["statut"])
    ecr = q.order_by(Ecriture.date.desc(), Ecriture.id.desc()).limit(600).all()
    nb_brouillard = Ecriture.query.filter_by(statut="brouillard").count()
    return render_template("compta/index.html", ecr=ecr, journaux=Journal.query.all(), j=j, debut=debut, fin=fin,
                           nb_brouillard=nb_brouillard, comptes=CompteComptable.query.order_by(CompteComptable.numero).all())


@bp.route("/ecritures/<int:eid>")
@require("compta.voir")
def ecriture(eid):
    e = db.get_or_404(Ecriture, eid)
    liees = Ecriture.query.filter_by(source_type=e.source_type, source_id=e.source_id).filter(Ecriture.id != e.id).all() \
        if e.source_type else []
    return render_template("compta/ecriture.html", e=e, liees=liees)


@bp.route("/valider", methods=["POST"])
@require("compta.gerer")
def valider():
    ids = [int(x) for x in request.form.getlist("ids")]
    if request.form.get("tout"):
        debut, fin = to_date(request.form.get("debut")), to_date(request.form.get("fin"))
        q = Ecriture.query.filter_by(statut="brouillard")
        if debut:
            q = q.filter(Ecriture.date >= debut)
        if fin:
            q = q.filter(Ecriture.date <= fin)
        ids = [e.id for e in q.all()]

    def go():
        n = svc.valider_ecritures(ids, current_user.id)
        audit("validation_ecritures", "ecriture", None, {"nb": n})
        return n
    return run_action(go, lambda n: f"{n} écriture(s) validée(s).", request.referrer or url_for("compta.index"))


@bp.route("/od", methods=["GET", "POST"])
@require("compta.gerer")
def od():
    if request.method == "POST":
        f = request.form

        def go():
            lignes = [(l["compte"], to_float(l["debit"]), to_float(l["credit"]), l["libelle"] or f.get("libelle"))
                      for l in lignes_form("compte", "debit", "credit", "libelle", required="compte")]
            if len(lignes) < 2:
                raise BusinessError("Une écriture comporte au moins deux lignes.")
            if not (f.get("libelle") or "").strip():
                raise BusinessError("Libellé obligatoire (justification de la correction).")
            e = svc.passer(f.get("journal", "OD"), to_date(f.get("date"), date.today()), f["libelle"], lignes)
            if not e:
                raise BusinessError("Écriture vide.")
            audit("ecriture_manuelle", "ecriture", e.id, {"libelle": e.libelle})
            return e
        return run_action(go, lambda e: f"Écriture {e.numero} enregistrée (brouillard).",
                          lambda e: url_for("compta.ecriture", eid=e.id))
    return render_template("compta/od.html", comptes=CompteComptable.query.order_by(CompteComptable.numero).all(),
                           journaux=Journal.query.all())


@bp.route("/ecritures/<int:eid>/contre-passer", methods=["POST"])
@require("compta.gerer")
def contre_passer(eid):
    e = db.get_or_404(Ecriture, eid)

    def go():
        if e.source_type:
            raise BusinessError("Écriture automatique : annulez l'opération d'origine (vente, achat…) pour la contre-passer.")
        if e.contre_passation_de or Ecriture.query.filter_by(contre_passation_de=e.id).first():
            raise BusinessError("Cette écriture est déjà une contre-passation ou a déjà été contre-passée.")
        lignes = [(l.compte_numero, l.credit, l.debit, "Contre-passation " + (l.libelle or "")) for l in e.lignes]
        n = svc.passer(e.journal_code, date.today(), f"Contre-passation {e.numero} – {request.form.get('motif', '')}",
                       lignes, contre_passation_de=e.id)
        audit("contre_passation", "ecriture", e.id, {"motif": request.form.get("motif")})
        return n
    return run_action(go, "Écriture contre-passée.", lambda n: url_for("compta.ecriture", eid=n.id))


@bp.route("/grand-livre")
@require("compta.voir")
def grand_livre():
    debut, fin = periode_args("annee")
    cpt = request.args.get("compte") or None
    return render_template("compta/grand_livre.html", gl=svc.grand_livre(cpt, debut, fin), debut=debut, fin=fin, cpt=cpt,
                           comptes=CompteComptable.query.order_by(CompteComptable.numero).all())


@bp.route("/balance")
@require("compta.voir")
def balance():
    debut, fin = periode_args("annee")
    bal = svc.balance(debut, fin)
    tot = {k: round(sum(r[k] for r in bal), 2) for k in ("debit", "credit", "solde_debiteur", "solde_crediteur")}
    tva = {"collectee": -svc.solde_compte("4431", fin), "deductible": svc.solde_compte("4452", fin)}
    return render_template("compta/balance.html", bal=bal, tot=tot, debut=debut, fin=fin, tva=tva)


@bp.route("/etats")
@require("compta.voir")
def etats():
    debut, fin = periode_args("annee")
    return render_template("compta/etats.html", cr=svc.compte_resultat(debut, fin), bil=svc.bilan(fin), debut=debut, fin=fin,
                           rappro=stock.rapprochement_stock())


@bp.route("/periodes", methods=["GET", "POST"])
@require("compta.voir")
def periodes():
    if request.method == "POST":
        if not current_user.can("compta.gerer"):
            from flask import abort
            abort(403)
        a, m = request.form.get("annee", type=int), request.form.get("mois", type=int)

        def go():
            p = svc.cloturer_periode(a, m, current_user.id)
            audit("cloture_periode", "periode", p.id, {"periode": f"{m:02d}/{a}"})
            return p
        return run_action(go, f"Période {m:02d}/{a} clôturée : plus aucune écriture ne peut y être passée.",
                          url_for("compta.periodes"))
    ps = Periode.query.order_by(Periode.annee.desc(), Periode.mois.desc()).all()
    stats = {}
    for p in ps:
        d0 = date(p.annee, p.mois, 1)
        d1 = date(p.annee + (p.mois == 12), p.mois % 12 + 1, 1)
        q = Ecriture.query.filter(Ecriture.date >= d0, Ecriture.date < d1)
        stats[p.id] = {"total": q.count(), "brouillard": q.filter_by(statut="brouillard").count()}
    return render_template("compta/periodes.html", ps=ps, stats=stats)


@bp.route("/plan", methods=["GET", "POST"])
@require("compta.voir")
def plan():
    if request.method == "POST":
        if not current_user.can("compta.gerer"):
            from flask import abort
            abort(403)
        f = request.form

        def go():
            num = (f.get("numero") or "").strip()
            if not num.isdigit() or not f.get("intitule"):
                raise BusinessError("Numéro (chiffres) et intitulé obligatoires.")
            c = db.session.get(CompteComptable, num) or CompteComptable(numero=num, classe=int(num[0]))
            c.intitule = f["intitule"]
            db.session.add(c)
            audit("plan_comptable", "compte", None, {"numero": num})
            return c
        return run_action(go, "Compte enregistré.", url_for("compta.plan"))
    return render_template("compta/plan.html", comptes=CompteComptable.query.order_by(CompteComptable.numero).all())
