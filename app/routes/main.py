from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Client, Vente, Produit, Lot, Fournisseur, Achat, AuditLog, Entrepot, Transformation, User
from ..permissions import require
from ..services import analytics, stock, finance
from ..utils import fcfa, date_fr
from . import periode_args

bp = Blueprint("main", __name__)


@bp.before_request
@login_required
def _auth():
    pass


@bp.route("/")
def index():
    return redirect(url_for("main.dashboard"))


@bp.route("/offline")
def offline():
    return render_template("erreur.html", code="", titre="Hors connexion",
                           message="Vous êtes hors ligne. Reconnectez-vous pour consulter des données à jour.")


@bp.route("/dashboard")
@require("dashboard.voir")
def dashboard():
    debut, fin = periode_args("mois")
    k = analytics.kpis(debut, fin)
    data = {
        "k": k, "debut": debut, "fin": fin,
        "cles": analytics.stock_par_produit_cle(),
        "peremption": stock.lots_proches_peremption(int(2)),
        "alertes": stock.produits_en_alerte(),
        "top_clients": analytics.top_clients(debut, fin),
        "top_vendus": analytics.top_produits(debut, fin, cle="ca"),
        "top_rentables": analytics.top_produits(debut, fin, cle="marge"),
        "canaux": analytics.par_dimension("canal", debut, fin),
        "circuits": analytics.circuits(debut, fin),
        "serie": analytics.serie_ca(30),
        "mensuel": analytics.serie_mensuelle(6),
        "depenses": analytics.repartition_depenses(debut, fin),
        "evol_stock": analytics.evolution_stock(30),
        "echues": [v for v in Vente.query.filter_by(statut="valide").all() if v.en_retard][:8],
        "en_cours": Transformation.query.filter_by(statut="en_cours").all(),
    }
    if not current_user.can("finances.voir"):
        # Un commercial ne doit pas pouvoir lire les marges, même dans les données des graphiques
        data["serie"] = [{"date": x["date"], "ca": x["ca"]} for x in data["serie"]]
        data["mensuel"] = [{"mois": x["mois"], "ca": x["ca"]} for x in data["mensuel"]]
        data["circuits"] = [{"cle": x["cle"], "ca": x["ca"]} for x in data["circuits"]]
        data["canaux"] = [{"cle": x["cle"], "ca": x["ca"]} for x in data["canaux"]]
        data["top_clients"] = [{"cle": x["cle"], "ca": x["ca"]} for x in data["top_clients"]]
        data["top_vendus"] = [{"cle": x["cle"], "ca": x["ca"], "quantite": x["quantite"], "unite": x["unite"]} for x in data["top_vendus"]]
        data["top_rentables"], data["depenses"], data["evol_stock"] = [], [], []
        data["k"] = {key: v for key, v in k.items() if key not in ("marge_brute", "taux_marge", "resultat_net", "resultat",
                                                                    "valeur_vivant", "valeur_transforme", "valeur_stock_total", "en_cours")}
    return render_template("dashboard.html", **data)


@bp.route("/api/recherche")
def recherche():
    q = (request.args.get("q") or "").strip()
    sections = []
    if len(q) < 2:
        return jsonify(sections=[])
    like = f"%{q}%"
    if current_user.can("clients.gerer") or current_user.can("ventes.voir"):
        cl = Client.query.filter(Client.nom.ilike(like) | Client.telephone.ilike(like)).limit(5).all()
        if cl:
            sections.append({"titre": "Clients", "items": [{"nom": c.nom, "sub": c.telephone or c.type_client, "icone": "👤",
                                                            "url": url_for("tiers.client_detail", cid=c.id)} for c in cl]})
    if current_user.can("ventes.voir"):
        vs = Vente.query.filter(Vente.numero.ilike(like)).order_by(Vente.date.desc()).limit(5).all()
        if vs:
            sections.append({"titre": "Factures", "items": [{"nom": v.numero, "sub": f"{v.client.nom} · {fcfa(v.total_ttc)}",
                                                             "icone": "🧾", "url": url_for("ventes.detail", vid=v.id)} for v in vs]})
    if current_user.can("stock.voir"):
        ps = Produit.query.filter(Produit.nom.ilike(like) | Produit.code.ilike(like)).limit(5).all()
        if ps:
            sections.append({"titre": "Produits", "items": [{"nom": p.nom, "sub": f"Stock : {stock.stock_physique(p.id)} {p.unite}",
                                                             "icone": "🍗", "url": url_for("stocks.index", produit=p.id)} for p in ps]})
        ls = Lot.query.filter(Lot.code.ilike(like)).limit(5).all()
        if ls:
            sections.append({"titre": "Lots", "items": [{"nom": l.code, "sub": f"{l.produit.nom} · reste {l.qte_restante}",
                                                         "icone": "📦", "url": url_for("stocks.lot", lid=l.id)} for l in ls]})
    if current_user.can("achats.voir"):
        fs = Fournisseur.query.filter(Fournisseur.nom.ilike(like)).limit(3).all()
        if fs:
            sections.append({"titre": "Fournisseurs", "items": [{"nom": f.nom, "sub": f.telephone, "icone": "🏭",
                                                                 "url": url_for("tiers.fournisseur_detail", fid=f.id)} for f in fs]})
        acs = Achat.query.filter(Achat.numero.ilike(like)).limit(3).all()
        if acs:
            sections.append({"titre": "Achats", "items": [{"nom": a.numero, "sub": a.fournisseur.nom, "icone": "🛒",
                                                           "url": url_for("achats.detail", aid=a.id)} for a in acs]})
    return jsonify(sections=sections)


@bp.route("/api/stock")
def api_stock():
    eid = request.args.get("entrepot", type=int)
    out = {}
    for p in Produit.query.filter_by(actif=True).all():
        out[p.id] = {"dispo": stock.disponible(p.id, eid), "physique": stock.stock_physique(p.id, eid),
                     "cout": round(stock.cmup(p.id, eid), 2) if current_user.can("finances.voir") else None}
    return jsonify(out)


@bp.route("/api/client/<int:cid>")
def api_client(cid):
    c = db.session.get(Client, cid)
    return jsonify(tarif=c.tarif, remise=c.remise_defaut_pct, plafond=c.plafond_credit, solde=finance.solde_client(c),
                   delai=c.delai_paiement_jours, type=c.type_client)


@bp.route("/audit")
@require("audit.voir")
def audit_log():
    page = request.args.get("page", 1, type=int)
    q = AuditLog.query
    if request.args.get("user"):
        q = q.filter_by(user_id=request.args.get("user", type=int))
    if request.args.get("action"):
        q = q.filter(AuditLog.action.ilike(f"%{request.args['action']}%"))
    pag = q.order_by(AuditLog.date.desc()).paginate(page=page, per_page=50)
    return render_template("audit.html", pag=pag, users=User.query.all())
