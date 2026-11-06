from datetime import date, timedelta
from flask import Blueprint, render_template, request, url_for
from flask_login import login_required
from ..extensions import db
from ..models import Employe, Presence, Conge, AvanceSalaire, Paie, CompteTresorerie
from ..permissions import require
from ..services import rh as svc
from ..utils import BusinessError, to_float, to_date, audit
from . import run_action

bp = Blueprint("rh", __name__, url_prefix="/rh")


@bp.before_request
@login_required
@require("rh.gerer")
def _auth():
    pass


@bp.route("/")
def index():
    emps = Employe.query.order_by(Employe.actif.desc(), Employe.nom).all()
    today = date.today()
    jours = [today - timedelta(days=i) for i in range(6, -1, -1)]
    pres = {(p.employe_id, p.date): p.statut for p in Presence.query.filter(Presence.date >= jours[0]).all()}
    avances = {e.id: sum(a.montant for a in svc.avances_en_cours(e)) for e in emps}
    paies = Paie.query.order_by(Paie.annee.desc(), Paie.mois.desc(), Paie.id.desc()).limit(100).all()
    mois_prec = today.replace(day=1) - timedelta(days=1)
    return render_template("rh/index.html", emps=emps, jours=jours, pres=pres, avances=avances, paies=paies,
                           conges=Conge.query.order_by(Conge.debut.desc()).limit(50).all(),
                           comptes=CompteTresorerie.query.filter_by(actif=True).all(), mois_prec=mois_prec,
                           masse=sum(e.salaire_base for e in emps if e.actif))


@bp.route("/employes", methods=["POST"])
def employe_save():
    f = request.form

    def go():
        e = db.session.get(Employe, f.get("id", type=int)) if f.get("id") else Employe()
        e.nom = (f.get("nom") or "").strip()
        if not e.nom:
            raise BusinessError("Nom obligatoire.")
        e.poste, e.telephone = f.get("poste"), f.get("telephone")
        e.date_embauche = to_date(f.get("date_embauche"))
        e.salaire_base = to_float(f.get("salaire_base"))
        e.actif = f.get("actif", "1") == "1"
        db.session.add(e)
        db.session.flush()
        audit("employe", "employe", e.id, {"nom": e.nom})
        return e
    return run_action(go, "Employé enregistré.", url_for("rh.index"))


@bp.route("/presences", methods=["POST"])
def presence():
    f = request.form

    def go():
        eid, d = f.get("employe_id", type=int), to_date(f.get("date"))
        p = Presence.query.filter_by(employe_id=eid, date=d).first() or Presence(employe_id=eid, date=d)
        p.statut = f.get("statut", "present")
        db.session.add(p)
        return p
    return run_action(go, "Présence enregistrée.", url_for("rh.index"))


@bp.route("/conges", methods=["POST"])
def conge():
    f = request.form

    def go():
        if f.get("id"):
            c = db.get_or_404(Conge, f.get("id", type=int))
            c.statut = f.get("statut")
            return c
        d0, d1 = to_date(f.get("debut")), to_date(f.get("fin"))
        if not d0 or not d1 or d1 < d0:
            raise BusinessError("Dates de congé invalides.")
        c = Conge(employe_id=f.get("employe_id", type=int), debut=d0, fin=d1, type_conge=f.get("type_conge", "annuel"))
        db.session.add(c)
        return c
    return run_action(go, "Congé enregistré.", url_for("rh.index"))


@bp.route("/avances", methods=["POST"])
def avance():
    f = request.form
    return run_action(lambda: svc.avance(db.get_or_404(Employe, f.get("employe_id", type=int)), to_float(f.get("montant")),
                                         db.get_or_404(CompteTresorerie, f.get("compte_id", type=int))),
                      "Avance enregistrée (sera déduite du prochain salaire).", url_for("rh.index"))


@bp.route("/paie", methods=["POST"])
def paie():
    f = request.form
    return run_action(lambda: svc.payer_salaire(db.get_or_404(Employe, f.get("employe_id", type=int)), f.get("annee", type=int),
                                                f.get("mois", type=int),
                                                db.get_or_404(CompteTresorerie, f.get("compte_id", type=int)),
                                                to_float(f.get("primes")), to_float(f.get("retenues"))),
                      lambda p: f"Salaire payé : net {int(p.net_a_payer):,} FCFA (avances déduites : {int(p.avances_deduites):,}).".replace(",", " "),
                      url_for("rh.index"))
