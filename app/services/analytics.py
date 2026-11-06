"""Indicateurs de pilotage : CA, marges sur coûts réels, rentabilité par produit / client / canal / circuit.

Quatre notions distinctes :
- Chiffre d'affaires : ventes facturées HT (encaissées ou non).
- Encaissements : argent effectivement reçu sur la période.
- Bénéfice : CA − coût réel des ventes − pertes − charges opérationnelles.
- Trésorerie disponible : soldes caisse + banque + Wave + Orange Money à l'instant T.
"""
from collections import defaultdict
from datetime import datetime, date, timedelta
from sqlalchemy import func
from ..extensions import db
from ..models import (Vente, VenteLigne, Produit, Client, Depense, CategorieDepense, MouvementStock, Paiement, Avoir,
                      Transformation, Lot, Achat)
from ..utils import to_float, get_param
from . import stock, finance

TYPES_PERTES = ("perte", "mortalite", "peremption", "casse", "ajustement_moins", "ajustement_plus")


def bornes(debut=None, fin=None):
    today = date.today()
    debut = debut or today.replace(day=1)
    fin = fin or today
    return datetime.combine(debut, datetime.min.time()), datetime.combine(fin, datetime.max.time())


def _lignes(debut, fin, entrepot_id=None, canal=None, client_id=None, produit_id=None):
    q = db.session.query(VenteLigne, Vente, Produit).join(Vente, VenteLigne.vente_id == Vente.id).join(
        Produit, VenteLigne.produit_id == Produit.id).filter(Vente.statut == "valide", Vente.date >= debut,
                                                             Vente.date <= fin)
    if entrepot_id:
        q = q.filter(Vente.entrepot_id == entrepot_id)
    if canal:
        q = q.filter(Vente.canal == canal)
    if client_id:
        q = q.filter(Vente.client_id == client_id)
    if produit_id:
        q = q.filter(VenteLigne.produit_id == produit_id)
    return q.all()


def _avoirs(debut, fin):
    return Avoir.query.join(Vente).filter(Vente.statut == "valide", Avoir.date >= debut, Avoir.date <= fin).all()


def pertes(debut, fin, entrepot_id=None):
    q = db.session.query(func.coalesce(func.sum(MouvementStock.valeur), 0)).filter(
        MouvementStock.type_mvt.in_(TYPES_PERTES), MouvementStock.date >= debut, MouvementStock.date <= fin)
    if entrepot_id:
        q = q.filter(MouvementStock.entrepot_id == entrepot_id)
    return round(-(q.scalar() or 0), 2)  # sorties négatives → perte positive


def pertes_detail(debut, fin):
    rows = db.session.query(MouvementStock.type_mvt, Produit.nom, func.sum(MouvementStock.quantite),
                            func.sum(MouvementStock.valeur)).join(Produit).filter(
        MouvementStock.type_mvt.in_(TYPES_PERTES), MouvementStock.date >= debut, MouvementStock.date <= fin
    ).group_by(MouvementStock.type_mvt, Produit.nom).all()
    return [{"type": t, "produit": n, "quantite": round(-q, 3), "valeur": round(-v, 2)} for t, n, q, v in rows]


def charges_operationnelles(debut, fin):
    q = db.session.query(CategorieDepense.nom, func.sum(Depense.montant)).join(CategorieDepense).filter(
        Depense.statut == "valide", Depense.incorporee_stock.is_(False), CategorieDepense.nature == "charge",
        Depense.date >= debut, Depense.date <= fin).group_by(CategorieDepense.nom)
    return {n: round(m, 2) for n, m in q.all()}


def investissements(debut, fin):
    return round(db.session.query(func.coalesce(func.sum(Depense.montant), 0)).join(CategorieDepense).filter(
        Depense.statut == "valide", CategorieDepense.nature == "investissement", Depense.date >= debut,
        Depense.date <= fin).scalar() or 0, 2)


def encaissements(debut, fin):
    return round(db.session.query(func.coalesce(func.sum(Paiement.montant), 0)).filter(
        Paiement.sens == "encaissement", Paiement.statut == "valide", Paiement.date >= debut,
        Paiement.date <= fin).scalar() or 0, 2)


def resultat(debut=None, fin=None, **filtres):
    debut, fin = bornes(debut, fin)
    lignes = _lignes(debut, fin, **filtres)
    ca_lignes = sum(l.montant_ht for l, v, p in lignes)
    ventes_ids = {v.id for _, v, _ in lignes}
    livraisons = sum(v.frais_livraison or 0 for v in Vente.query.filter(Vente.id.in_(ventes_ids)).all()) if ventes_ids else 0
    avs = _avoirs(debut, fin) if not filtres else []
    ca = ca_lignes + livraisons - sum(a.montant_ht for a in avs)
    cmv = sum(l.cout_total for l, v, p in lignes) - sum(a.cout for a in avs if a.remise_en_stock)
    pts = pertes(debut, fin, filtres.get("entrepot_id")) if not (filtres.get("client_id") or filtres.get("canal")) else 0
    mb = ca - cmv - pts
    charges = charges_operationnelles(debut, fin) if not filtres else {}
    total_charges = sum(charges.values())
    re = mb - total_charges
    taux_is = to_float(get_param("taux_is", "30")) / 100
    impot = max(re, 0) * taux_is
    return {"debut": debut, "fin": fin, "ca": round(ca, 2), "cmv": round(cmv, 2), "pertes": round(pts, 2),
            "marge_brute": round(mb, 2), "taux_marge": round(mb / ca * 100, 1) if ca else 0,
            "charges": charges, "total_charges": round(total_charges, 2), "resultat_exploitation": round(re, 2),
            "impot_estime": round(impot, 2), "resultat_net": round(re - impot, 2),
            "encaissements": encaissements(debut, fin), "investissements": investissements(debut, fin)}


def par_dimension(dimension, debut=None, fin=None, **filtres):
    """dimension : produit | client | canal | circuit"""
    debut, fin = bornes(debut, fin)
    agg = defaultdict(lambda: {"ca": 0, "cout": 0, "qte": 0, "nb": set()})
    for l, v, p in _lignes(debut, fin, **filtres):
        if dimension == "produit":
            k = p.nom
        elif dimension == "client":
            k = v.client.nom
        elif dimension == "canal":
            k = "Vente en gros" if v.canal == "gros" else "Vente au détail"
        else:
            k = "Poulets sur pied" if p.type_produit == "vivant" else "Poulets transformés"
        a = agg[k]
        a["ca"] += l.montant_ht
        a["cout"] += l.cout_total
        a["qte"] += l.quantite
        a["unite"] = p.unite if dimension == "produit" else ""
        a["nb"].add(v.id)
    out = []
    for k, a in agg.items():
        marge = a["ca"] - a["cout"]
        out.append({"cle": k, "ca": round(a["ca"], 2), "cout": round(a["cout"], 2), "marge": round(marge, 2),
                    "taux": round(marge / a["ca"] * 100, 1) if a["ca"] else 0, "quantite": round(a["qte"], 3),
                    "unite": a.get("unite", ""), "nb_ventes": len(a["nb"])})
    return sorted(out, key=lambda r: -r["ca"])


def circuits(debut=None, fin=None):
    """Rentabilité comparée des deux activités. Les frais de transformation sont déjà dans le coût des produits."""
    debut_, fin_ = bornes(debut, fin)
    rows = {r["cle"]: r for r in par_dimension("circuit", debut, fin)}
    pts_vivant = -(db.session.query(func.coalesce(func.sum(MouvementStock.valeur), 0)).join(Produit).filter(
        MouvementStock.type_mvt.in_(TYPES_PERTES), Produit.type_produit == "vivant", MouvementStock.date >= debut_,
        MouvementStock.date <= fin_).scalar() or 0)
    pts_transfo = pertes(debut_, fin_) - pts_vivant
    trf = Transformation.query.filter(Transformation.statut == "terminee", Transformation.date_fin >= debut_,
                                      Transformation.date_fin <= fin_).all()
    out = []
    for cle, pts in (("Poulets sur pied", pts_vivant), ("Poulets transformés", pts_transfo)):
        r = rows.get(cle, {"cle": cle, "ca": 0, "cout": 0, "marge": 0, "taux": 0, "quantite": 0, "nb_ventes": 0})
        r = dict(r)
        r["pertes"] = round(pts, 2)
        r["marge_nette"] = round(r["marge"] - pts, 2)
        out.append(r)
    out[1]["frais_transformation"] = round(sum(t.frais_total for t in trf), 2)
    out[1]["poulets_transformes"] = sum(t.nb_poulets for t in trf)
    out[1]["rendement_moyen"] = round(sum(t.poids_obtenu for t in trf) / sum(t.poids_vif_total for t in trf) * 100, 1) \
        if trf and sum(t.poids_vif_total for t in trf) else 0
    return out


def serie_ca(jours=30, debut=None, fin=None):
    fin = fin or date.today()
    debut = debut or fin - timedelta(days=jours - 1)
    rows = db.session.query(func.date(Vente.date), func.sum(Vente.total_ht), func.sum(Vente.cout_total)).filter(
        Vente.statut == "valide", Vente.date >= datetime.combine(debut, datetime.min.time()),
        Vente.date <= datetime.combine(fin, datetime.max.time())).group_by(func.date(Vente.date)).all()
    m = {str(d): (ca or 0, c or 0) for d, ca, c in rows}
    out = []
    d = debut
    while d <= fin:
        ca, c = m.get(str(d), (0, 0))
        out.append({"date": d.strftime("%d/%m"), "ca": round(ca, 2), "marge": round(ca - c, 2)})
        d += timedelta(days=1)
    return out


def serie_mensuelle(mois=6):
    today = date.today()
    out = []
    y, m = today.year, today.month
    pts = []
    for _ in range(mois):
        pts.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    for y, m in reversed(pts):
        debut = date(y, m, 1)
        fin = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        r = resultat(debut, fin)
        out.append({"mois": f"{m:02d}/{str(y)[2:]}", "ca": r["ca"], "marge": r["marge_brute"],
                    "resultat": r["resultat_net"], "charges": r["total_charges"]})
    return out


def kpis(debut=None, fin=None):
    today = date.today()
    r_jour = resultat(today, today)
    r = resultat(debut, fin)
    vivant = Produit.query.filter_by(type_produit="vivant").all()
    nb_vivants = sum(stock.stock_physique(p.id) for p in vivant)
    creances = sum(x["du"] for x in finance.creances_clients())
    dettes = sum(x["du"] for x in finance.dettes_fournisseurs())
    debut_, fin_ = bornes(debut, fin)
    vendus_sur_pied = db.session.query(func.coalesce(func.sum(VenteLigne.quantite), 0)).join(Vente).join(Produit).filter(
        Vente.statut == "valide", Produit.type_produit == "vivant", Vente.date >= debut_, Vente.date <= fin_).scalar() or 0
    transformes = db.session.query(func.coalesce(func.sum(Transformation.nb_poulets), 0)).filter(
        Transformation.statut.in_(["terminee", "en_cours"]), Transformation.date >= debut_,
        Transformation.date <= fin_).scalar() or 0
    depenses_mois = round(db.session.query(func.coalesce(func.sum(Depense.montant), 0)).filter(
        Depense.statut == "valide", Depense.date >= debut_, Depense.date <= fin_).scalar() or 0, 2)
    return {
        "ca_jour": r_jour["ca"], "ca_periode": r["ca"], "depenses_periode": depenses_mois,
        "marge_brute": r["marge_brute"], "taux_marge": r["taux_marge"], "resultat_net": r["resultat_net"],
        "resultat": r, "tresorerie": finance.tresorerie_totale(), "creances": round(creances, 2),
        "dettes": round(dettes, 2), "nb_vivants": int(round(nb_vivants)),
        "valeur_vivant": stock.valeur_stock(type_produit="vivant"),
        "valeur_transforme": stock.valeur_stock(type_produit=["abattu", "decoupe", "abats"]),
        "valeur_stock_total": stock.valeur_stock(),
        "en_cours": round(sum(t.cout_matiere for t in Transformation.query.filter_by(statut="en_cours").all()), 2),
        "vendus_sur_pied": int(vendus_sur_pied), "transformes": int(transformes),
        "encaissements": r["encaissements"],
    }


def stock_par_produit_cle(noms=("Cuisses", "Blanc de poulet", "Pilons", "Poulet entier", "Ailes")):
    out = []
    for n in noms:
        p = Produit.query.filter_by(nom=n).first()
        if p:
            out.append({"produit": p, "qte": stock.stock_physique(p.id), "dispo": stock.disponible(p.id)})
    return out


def top_clients(debut=None, fin=None, n=5):
    return par_dimension("client", debut, fin)[:n]


def top_produits(debut=None, fin=None, n=5, cle="ca"):
    return sorted(par_dimension("produit", debut, fin), key=lambda r: -r[cle])[:n]


def repartition_depenses(debut=None, fin=None):
    debut, fin = bornes(debut, fin)
    q = db.session.query(CategorieDepense.nom, func.sum(Depense.montant)).join(CategorieDepense).filter(
        Depense.statut == "valide", Depense.date >= debut, Depense.date <= fin).group_by(CategorieDepense.nom)
    return sorted([{"cle": n, "montant": round(m, 2)} for n, m in q.all()], key=lambda r: -r["montant"])


def evolution_stock(jours=30):
    """Valeur du stock reconstituée jour par jour à partir des mouvements."""
    fin = date.today()
    debut = fin - timedelta(days=jours - 1)
    total_actuel = stock.valeur_stock()
    rows = db.session.query(func.date(MouvementStock.date), func.sum(MouvementStock.valeur)).filter(
        MouvementStock.date > datetime.combine(debut, datetime.min.time())).group_by(func.date(MouvementStock.date)).all()
    var = {str(d): v or 0 for d, v in rows}
    out = []
    val = total_actuel
    d = fin
    while d >= debut:
        out.append({"date": d.strftime("%d/%m"), "valeur": round(val, 2)})
        val -= var.get(str(d), 0)
        d -= timedelta(days=1)
    return list(reversed(out))
