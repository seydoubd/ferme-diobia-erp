from datetime import datetime
from urllib.parse import quote
from flask import Blueprint, render_template, request, url_for, send_file, abort
from flask_login import login_required, current_user
from ..extensions import db
from ..models import (Vente, VenteLigne, Client, Produit, Entrepot, CompteTresorerie, Commande, Livreur, CLIENT_TYPES)
from ..permissions import require
from ..services import ventes as svc, finance, stock
from ..utils import BusinessError, to_float, to_datetime, to_date, fcfa, get_param
from . import run_action, lignes_form, periode_args

bp = Blueprint("ventes", __name__)


@bp.before_request
@login_required
def _auth():
    pass


def _refs():
    return {"clients": Client.query.filter_by(actif=True).order_by(Client.nom).all(),
            "produits": Produit.query.filter_by(actif=True, vendable=True).order_by(Produit.type_produit, Produit.nom).all(),
            "entrepots": Entrepot.query.filter_by(actif=True).order_by(Entrepot.nom).all(),
            "comptes": CompteTresorerie.query.filter_by(actif=True).all(), "MODES": finance.MODES,
            "livreurs": Livreur.query.filter_by(actif=True).all()}


@bp.route("/ventes")
@require("ventes.voir")
def index():
    debut, fin = periode_args("30j")
    q = Vente.query.filter(Vente.date >= datetime.combine(debut, datetime.min.time()),
                           Vente.date <= datetime.combine(fin, datetime.max.time()))
    if request.args.get("client"):
        q = q.filter_by(client_id=request.args.get("client", type=int))
    if request.args.get("canal"):
        q = q.filter_by(canal=request.args["canal"])
    if request.args.get("entrepot"):
        q = q.filter_by(entrepot_id=request.args.get("entrepot", type=int))
    ventes = q.order_by(Vente.date.desc()).all()
    st = request.args.get("paiement")
    if st:
        ventes = [v for v in ventes if v.statut_paiement == st or (st == "echue" and v.en_retard)]
    valides = [v for v in ventes if v.statut == "valide"]
    tot = {"ca": sum(v.total_ttc for v in valides), "encaisse": sum(v.montant_paye for v in valides),
           "du": sum(v.solde for v in valides), "marge": sum(v.marge for v in valides), "nb": len(valides)}
    return render_template("ventes/index.html", ventes=ventes, tot=tot, debut=debut, fin=fin, **_refs())


@bp.route("/ventes/nouvelle", methods=["GET", "POST"])
@require("ventes.creer")
def nouvelle():
    if request.method == "POST":
        f = request.form

        def go():
            lignes = lignes_form("produit_id", "quantite", "prix_unitaire", "remise_pct")
            mode = f.get("mode", "especes")
            paiement = {"mode": mode, "montant": 0 if mode == "credit" else to_float(f.get("montant_paye")),
                        "compte_id": f.get("compte_id", type=int), "reference": f.get("reference")}
            return svc.creer_vente(f.get("client_id", type=int), f.get("entrepot_id", type=int), lignes, current_user,
                                   to_datetime(f.get("date")) if f.get("date") else None, to_float(f.get("remise_globale")),
                                   paiement, to_date(f.get("echeance")), bool(f.get("forcer_stock")),
                                   frais_livraison=to_float(f.get("frais_livraison")), note=f.get("note", ""))
        return run_action(go, lambda v: f"Vente {v.numero} enregistrée – {fcfa(v.total_ttc)}"
                          + (f" (reste dû {fcfa(v.solde)})" if v.solde > 0.5 else " (payée)."),
                          lambda v: url_for("ventes.detail", vid=v.id, nouveau=1))
    refs = _refs()
    return render_template("ventes/nouvelle.html", client_id=request.args.get("client", type=int), **refs)


@bp.route("/ventes/<int:vid>")
@require("ventes.voir")
def detail(vid):
    v = db.get_or_404(Vente, vid)
    lien = url_for("ventes.imprimer", vid=v.id, _external=True)
    msg = (f"{get_param('nom_entreprise')} – Facture {v.numero} du {v.date.strftime('%d/%m/%Y')}\n"
           f"Montant : {fcfa(v.total_ttc)}\nPayé : {fcfa(v.montant_paye)}\nReste dû : {fcfa(v.solde)}\nMerci de votre confiance !")
    tel = "".join(ch for ch in (v.client.telephone or "") if ch.isdigit())
    wa = f"https://wa.me/{tel}?text={quote(msg)}" if tel else f"https://wa.me/?text={quote(msg)}"
    return render_template("ventes/detail.html", v=v, wa=wa, lien=lien, **_refs())


@bp.route("/ventes/<int:vid>/imprimer")
@require("ventes.voir")
def imprimer(vid):
    v = db.get_or_404(Vente, vid)
    tpl = "ventes/ticket.html" if request.args.get("format") == "ticket" else "ventes/facture.html"
    return render_template(tpl, v=v, params={k: get_param(k, "") for k in
                                             ("nom_entreprise", "slogan", "adresse", "telephone", "email", "ninea", "rccm",
                                              "mentions_facture")})


@bp.route("/ventes/<int:vid>/pdf")
@require("ventes.voir")
def pdf(vid):
    from ..services.pdf import facture_pdf
    v = db.get_or_404(Vente, vid)
    return send_file(facture_pdf(v), mimetype="application/pdf", as_attachment=True, download_name=f"{v.numero}.pdf")


@bp.route("/ventes/<int:vid>/annuler", methods=["POST"])
@require("ventes.annuler")
def annuler(vid):
    v = db.get_or_404(Vente, vid)
    return run_action(lambda: svc.annuler_vente(v, request.form.get("motif", "").strip(), current_user),
                      "Vente annulée : stock réintégré et écritures contre-passées.", url_for("ventes.detail", vid=v.id))


@bp.route("/ventes/<int:vid>/avoir", methods=["POST"])
@require("ventes.annuler")
def avoir(vid):
    v = db.get_or_404(Vente, vid)
    l = db.get_or_404(VenteLigne, request.form.get("ligne_id", type=int))
    if l.vente_id != v.id:
        abort(400)
    return run_action(lambda: svc.creer_avoir(v, l, request.form.get("quantite"), request.form.get("motif", ""),
                                              bool(request.form.get("remise_en_stock")), request.form.get("etat", "frais")),
                      lambda a: f"Avoir {a.numero} de {fcfa(a.montant_ttc)} créé.", url_for("ventes.detail", vid=v.id))


@bp.route("/ventes/<int:vid>/encaisser", methods=["POST"])
@require("encaissements.gerer")
def encaisser(vid):
    v = db.get_or_404(Vente, vid)
    f = request.form

    def go():
        cpt = db.session.get(CompteTresorerie, f.get("compte_id", type=int)) or finance.compte_pour_mode(f.get("mode"))
        m = to_float(f.get("montant"))
        return finance.encaisser(v.client, m, cpt, f.get("mode", "especes"), to_datetime(f.get("date")),
                                 f.get("reference"), allocations={v.id: m})
    return run_action(go, lambda p: f"Règlement {p.numero} de {fcfa(p.montant)} enregistré.", url_for("ventes.detail", vid=v.id))


# ─────────── Commandes & livraisons ───────────
@bp.route("/commandes")
@require("commandes.gerer")
def commandes():
    st = request.args.get("statut")
    q = Commande.query
    if st:
        q = q.filter_by(statut=st)
    cmds = q.order_by(Commande.date.desc()).limit(300).all()
    compteurs = {s: Commande.query.filter_by(statut=s).count() for s in ("attente", "confirmee", "preparation", "livree", "annulee")}
    return render_template("ventes/commandes.html", cmds=cmds, compteurs=compteurs, st=st, **_refs())


@bp.route("/commandes/nouvelle", methods=["GET", "POST"])
@require("commandes.gerer")
def commande_nouvelle():
    if request.method == "POST":
        f = request.form

        def go():
            c = svc.creer_commande(f.get("client_id", type=int), f.get("entrepot_id", type=int),
                                   lignes_form("produit_id", "quantite", "prix_unitaire"), None,
                                   to_date(f.get("date_livraison_prevue")), f.get("adresse", ""),
                                   to_float(f.get("frais_livraison")), f.get("note", ""))
            if f.get("confirmer"):
                svc.confirmer_commande(c)
            return c
        return run_action(go, lambda c: f"Commande {c.numero} enregistrée" + (" et stock réservé." if c.statut == "confirmee" else "."),
                          lambda c: url_for("ventes.commande", cid=c.id))
    return render_template("ventes/commande_nouvelle.html", **_refs())


@bp.route("/commandes/<int:cid>")
@require("commandes.gerer")
def commande(cid):
    c = db.get_or_404(Commande, cid)
    dispo = {l.id: stock.disponible(l.produit_id, c.entrepot_id) for l in c.lignes}
    return render_template("ventes/commande.html", c=c, dispo=dispo, **_refs())


@bp.route("/commandes/<int:cid>/<action>", methods=["POST"])
@require("commandes.gerer")
def commande_action(cid, action):
    c = db.get_or_404(Commande, cid)
    f = request.form
    if action == "confirmer":
        return run_action(lambda: svc.confirmer_commande(c), "Commande confirmée : stock réservé.", url_for("ventes.commande", cid=c.id))
    if action == "preparer":
        return run_action(lambda: svc.passer_en_preparation(c, f.get("livreur_id", type=int)), "Commande en préparation.",
                          url_for("ventes.commande", cid=c.id))
    if action == "livrer":
        if not current_user.can("ventes.creer") and not current_user.can("livraisons.gerer"):
            abort(403)
        mode = f.get("mode", "credit")
        paiement = {"mode": mode, "montant": 0 if mode == "credit" else to_float(f.get("montant_paye")),
                    "compte_id": f.get("compte_id", type=int)}
        return run_action(lambda: svc.livrer_commande(c, current_user, paiement),
                          lambda v: f"Commande livrée : facture {v.numero} générée.", url_for("ventes.commande", cid=c.id))
    if action == "annuler":
        return run_action(lambda: svc.annuler_commande(c, f.get("motif", "")), "Commande annulée : réservation libérée.",
                          url_for("ventes.commande", cid=c.id))
    abort(404)


@bp.route("/commandes/<int:cid>/bon-livraison")
@require("commandes.gerer")
def bon_livraison(cid):
    return render_template("ventes/bon_livraison.html", c=db.get_or_404(Commande, cid),
                           params={k: get_param(k, "") for k in ("nom_entreprise", "adresse", "telephone")})


@bp.route("/commandes/livreurs", methods=["POST"])
@require("commandes.gerer")
def livreur_save():
    def go():
        lid = request.form.get("id", type=int)
        l = db.session.get(Livreur, lid) if lid else Livreur()
        l.nom = (request.form.get("nom") or "").strip()
        if not l.nom:
            raise BusinessError("Nom du livreur obligatoire.")
        l.telephone, l.vehicule = request.form.get("telephone"), request.form.get("vehicule")
        db.session.add(l)
        return l
    return run_action(go, "Livreur enregistré.", url_for("ventes.commandes"))
