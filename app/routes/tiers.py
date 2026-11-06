from flask import Blueprint, render_template, request, url_for, abort
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Client, Fournisseur, Vente, Achat, Paiement, CompteTresorerie, CLIENT_TYPES, TARIF_PAR_TYPE
from ..permissions import require
from ..services import finance, analytics
from ..utils import BusinessError, to_float, to_datetime, audit, fcfa
from . import run_action

bp = Blueprint("tiers", __name__)
TYPES_LIB = {"grossiste": "Grossiste", "revendeur": "Revendeur", "restaurant": "Restaurant", "hotel": "Hôtel",
             "supermarche": "Supermarché", "boucherie": "Boucherie", "particulier": "Particulier"}
TARIFS = {"grossiste": "Prix grossiste", "revendeur": "Prix revendeur", "restaurant": "Prix restaurant",
          "particulier": "Prix particulier"}


@bp.before_request
@login_required
def _auth():
    pass


def _comptes():
    return CompteTresorerie.query.filter_by(actif=True).all()


# ─────────── Clients ───────────
@bp.route("/clients")
@require("clients.gerer")
def clients():
    q = Client.query
    if request.args.get("type"):
        q = q.filter_by(type_client=request.args["type"])
    cs = q.order_by(Client.nom).all()
    soldes = {c.id: finance.solde_client(c) for c in cs}
    return render_template("tiers/clients.html", cs=cs, soldes=soldes, TYPES=TYPES_LIB)


@bp.route("/clients/nouveau", methods=["GET", "POST"])
@bp.route("/clients/<int:cid>/modifier", methods=["GET", "POST"])
@require("clients.gerer")
def client_edit(cid=None):
    c = db.session.get(Client, cid) if cid else Client(type_client="particulier", tarif="particulier", actif=True)
    if request.method == "POST":
        f = request.form

        def go():
            c.nom = (f.get("nom") or "").strip()
            if not c.nom:
                raise BusinessError("Nom ou raison sociale obligatoire.")
            if f.get("type_client") not in CLIENT_TYPES:
                raise BusinessError("Type de client invalide.")
            c.telephone, c.adresse, c.email = f.get("telephone"), f.get("adresse"), f.get("email")
            c.type_client = f["type_client"]
            c.tarif = f.get("tarif") or TARIF_PAR_TYPE[c.type_client]
            c.delai_paiement_jours = int(to_float(f.get("delai_paiement_jours")))
            if current_user.can("finances.voir") or current_user.can("*"):
                c.plafond_credit = to_float(f.get("plafond_credit"))
                c.remise_defaut_pct = min(to_float(f.get("remise_defaut_pct")), 100)
            c.actif = bool(f.get("actif", "1"))
            db.session.add(c)
            db.session.flush()
            audit("client", "client", c.id, {"nom": c.nom})
            return c
        return run_action(go, "Client enregistré.", lambda c: url_for("tiers.client_detail", cid=c.id))
    return render_template("tiers/client_edit.html", c=c, TYPES=TYPES_LIB, TARIFS=TARIFS, TARIF_PAR_TYPE=TARIF_PAR_TYPE)


@bp.route("/clients/<int:cid>")
@require("clients.gerer")
def client_detail(cid):
    c = db.get_or_404(Client, cid)
    ventes = Vente.query.filter_by(client_id=c.id).order_by(Vente.date.desc()).all()
    valides = [v for v in ventes if v.statut == "valide"]
    paiements = Paiement.query.filter_by(client_id=c.id).order_by(Paiement.date.desc()).all()
    stats = {"total": sum(v.total_ttc for v in valides), "paye": sum(v.montant_paye for v in valides),
             "du": sum(v.solde for v in valides), "retard": sum(v.solde for v in valides if v.en_retard),
             "marge": sum(v.marge for v in valides), "nb": len(valides)}
    return render_template("tiers/client_detail.html", c=c, ventes=ventes, paiements=paiements, stats=stats,
                           comptes=_comptes(), MODES=finance.MODES, TYPES=TYPES_LIB)


@bp.route("/clients/<int:cid>/encaisser", methods=["POST"])
@require("encaissements.gerer")
def client_encaisser(cid):
    c = db.get_or_404(Client, cid)
    f = request.form

    def go():
        cpt = db.session.get(CompteTresorerie, f.get("compte_id", type=int)) or finance.compte_pour_mode(f.get("mode"))
        return finance.encaisser(c, to_float(f.get("montant")), cpt, f.get("mode", "especes"), to_datetime(f.get("date")),
                                 f.get("reference"))
    return run_action(go, lambda p: f"Règlement {p.numero} de {fcfa(p.montant)} réparti sur les factures les plus anciennes.",
                      request.referrer or url_for("tiers.client_detail", cid=c.id))


@bp.route("/paiements/<int:pid>/annuler", methods=["POST"])
@require("encaissements.gerer")
def paiement_annuler(pid):
    p = db.get_or_404(Paiement, pid)
    if p.sens == "decaissement" and not current_user.can("decaissements.gerer"):
        abort(403)
    return run_action(lambda: finance.annuler_paiement(p, request.form.get("motif", "")),
                      "Règlement annulé (trésorerie et écritures régularisées).", request.referrer or "/")


# ─────────── Fournisseurs ───────────
@bp.route("/fournisseurs")
@require("achats.voir")
def fournisseurs():
    fs = Fournisseur.query.order_by(Fournisseur.nom).all()
    dettes = {d["fournisseur"].id: d for d in finance.dettes_fournisseurs()}
    return render_template("tiers/fournisseurs.html", fs=fs, dettes=dettes)


@bp.route("/fournisseurs/nouveau", methods=["GET", "POST"])
@bp.route("/fournisseurs/<int:fid>/modifier", methods=["GET", "POST"])
@require("achats.gerer")
def fournisseur_edit(fid=None):
    fo = db.session.get(Fournisseur, fid) if fid else Fournisseur(actif=True)
    if request.method == "POST":
        f = request.form

        def go():
            fo.nom = (f.get("nom") or "").strip()
            if not fo.nom:
                raise BusinessError("Nom obligatoire.")
            fo.telephone, fo.adresse, fo.email, fo.ninea = f.get("telephone"), f.get("adresse"), f.get("email"), f.get("ninea")
            fo.delai_paiement_jours = int(to_float(f.get("delai_paiement_jours")))
            fo.actif = bool(f.get("actif", "1"))
            db.session.add(fo)
            db.session.flush()
            audit("fournisseur", "fournisseur", fo.id, {"nom": fo.nom})
            return fo
        return run_action(go, "Fournisseur enregistré.", lambda fo: url_for("tiers.fournisseur_detail", fid=fo.id))
    return render_template("tiers/fournisseur_edit.html", f=fo)


@bp.route("/fournisseurs/<int:fid>")
@require("achats.voir")
def fournisseur_detail(fid):
    fo = db.get_or_404(Fournisseur, fid)
    achats = Achat.query.filter_by(fournisseur_id=fo.id).order_by(Achat.date.desc()).all()
    recus = [a for a in achats if a.statut == "recu"]
    paiements = Paiement.query.filter_by(fournisseur_id=fo.id).order_by(Paiement.date.desc()).all()
    stats = {"total": sum(a.total_ttc for a in recus), "paye": sum(a.montant_paye for a in recus),
             "du": sum(a.solde for a in recus), "nb": len(recus),
             "poulets": sum(l.quantite for a in recus for l in a.lignes if l.produit.type_produit == "vivant")}
    return render_template("tiers/fournisseur_detail.html", f=fo, achats=achats, paiements=paiements, stats=stats,
                           comptes=_comptes(), MODES=finance.MODES)


@bp.route("/fournisseurs/<int:fid>/payer", methods=["POST"])
@require("decaissements.gerer")
def fournisseur_payer(fid):
    fo = db.get_or_404(Fournisseur, fid)
    f = request.form
    alloc = None
    if f.get("achat_id"):
        alloc = {f.get("achat_id", type=int): to_float(f.get("montant"))}

    def go():
        cpt = db.session.get(CompteTresorerie, f.get("compte_id", type=int))
        return finance.payer_fournisseur(fo, to_float(f.get("montant")), cpt, f.get("mode", "virement"),
                                         to_datetime(f.get("date")), f.get("reference"), alloc)
    return run_action(go, lambda p: f"Paiement {p.numero} de {fcfa(p.montant)} enregistré.",
                      request.referrer or url_for("tiers.fournisseur_detail", fid=fo.id))


# ─────────── Crédits & impayés ───────────
@bp.route("/credits")
@require("encaissements.gerer")
def credits():
    creances = finance.creances_clients()
    dettes = finance.dettes_fournisseurs() if current_user.can("achats.voir") else []
    echues = sorted([v for v in Vente.query.filter_by(statut="valide").all() if v.en_retard], key=lambda v: v.echeance)
    achats_echus = sorted([a for a in Achat.query.filter_by(statut="recu").all() if a.en_retard], key=lambda a: a.echeance)
    return render_template("tiers/credits.html", creances=creances, dettes=dettes, echues=echues,
                           achats_echus=achats_echus, comptes=_comptes(), MODES=finance.MODES)
