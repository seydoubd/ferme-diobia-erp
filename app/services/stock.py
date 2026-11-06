"""Gestion des stocks par lots, valorisation FIFO.

Règle d'or : la quantité d'un lot ne change JAMAIS sans un MouvementStock associé.
"""
from collections import defaultdict
from datetime import datetime, timedelta, date
from sqlalchemy import func
from ..extensions import db
from ..models import (Lot, MouvementStock, Reservation, Produit, Entrepot, OperationStock, Inventaire,
                      InventaireLigne)
from ..utils import BusinessError, EPS, current_uid, next_numero, audit
from . import compta

ETATS_VENDABLES = ("frais", "congele")

TYPES_MVT = {
    "entree_achat": "Entrée achat", "sortie_vente": "Sortie vente", "sortie_transformation": "Sortie vers transformation",
    "entree_transformation": "Entrée de transformation", "transfert_sortie": "Transfert (sortie)",
    "transfert_entree": "Transfert (entrée)", "perte": "Perte", "mortalite": "Mortalité", "peremption": "Péremption",
    "casse": "Casse / dommage", "declassement_sortie": "Déclassement (sortie)", "declassement_entree": "Déclassement (entrée)",
    "congelation_sortie": "Congélation (sortie frais)", "congelation_entree": "Congélation (entrée congelé)",
    "ajustement_plus": "Ajustement +", "ajustement_moins": "Ajustement −", "entree_manuelle": "Entrée manuelle",
    "retour_client": "Retour client", "retour_fournisseur": "Retour fournisseur",
    "annulation_vente": "Annulation de vente", "annulation_achat": "Annulation d'achat",
    "annulation_transformation": "Annulation de transformation",
}
TYPES_PERTE = ("perte", "mortalite", "peremption", "casse")


# ─────────── Lecture ───────────
def stock_physique(produit_id, entrepot_id=None, etats=ETATS_VENDABLES):
    q = db.session.query(func.coalesce(func.sum(Lot.qte_restante), 0)).filter(Lot.produit_id == produit_id)
    if entrepot_id:
        q = q.filter(Lot.entrepot_id == entrepot_id)
    if etats:
        q = q.filter(Lot.etat.in_(etats))
    return round(q.scalar() or 0, 3)


def reserve(produit_id, entrepot_id=None, exclure_commande_lignes=None):
    q = db.session.query(func.coalesce(func.sum(Reservation.quantite), 0)).filter(
        Reservation.produit_id == produit_id, Reservation.active.is_(True))
    if entrepot_id:
        q = q.filter(Reservation.entrepot_id == entrepot_id)
    if exclure_commande_lignes:
        q = q.filter(~Reservation.commande_ligne_id.in_(exclure_commande_lignes))
    return round(q.scalar() or 0, 3)


def perime(produit_id, entrepot_id=None, jour=None):
    jour = jour or date.today()
    q = db.session.query(func.coalesce(func.sum(Lot.qte_restante), 0)).filter(
        Lot.produit_id == produit_id, Lot.etat.in_(ETATS_VENDABLES), Lot.date_peremption.isnot(None),
        Lot.date_peremption < jour, Lot.qte_restante > EPS)
    if entrepot_id:
        q = q.filter(Lot.entrepot_id == entrepot_id)
    return round(q.scalar() or 0, 3)


def disponible(produit_id, entrepot_id=None, exclure_commande_lignes=None, jour=None):
    """Quantité vendable : stock frais/congelé, hors lots périmés (à la date `jour`) et hors réservations."""
    return round(stock_physique(produit_id, entrepot_id) - perime(produit_id, entrepot_id, jour)
                 - reserve(produit_id, entrepot_id, exclure_commande_lignes), 3)


def declarer_perimes(d=None):
    """Déclare en perte (péremption) tous les lots périmés à la date d. Retourne les opérations créées."""
    d = d or datetime.now()
    lots = Lot.query.filter(Lot.qte_restante > EPS, Lot.etat.in_(ETATS_VENDABLES), Lot.date_peremption.isnot(None),
                            Lot.date_peremption < d.date()).all()
    return [declarer_perte(l.produit_id, l.entrepot_id, l.qte_restante, "peremption", "Lot périmé " + l.code, d, l.id)
            for l in lots]


def valeur_stock(produit_id=None, entrepot_id=None, type_produit=None, etats=None):
    q = db.session.query(func.coalesce(func.sum(Lot.qte_restante * Lot.cout_unitaire), 0)).join(Produit)
    if produit_id:
        q = q.filter(Lot.produit_id == produit_id)
    if entrepot_id:
        q = q.filter(Lot.entrepot_id == entrepot_id)
    if type_produit:
        if isinstance(type_produit, (list, tuple)):
            q = q.filter(Produit.type_produit.in_(type_produit))
        else:
            q = q.filter(Produit.type_produit == type_produit)
    if etats:
        q = q.filter(Lot.etat.in_(etats))
    return round(q.scalar() or 0, 2)


def dernier_cout(produit_id, entrepot_id=None):
    q = Lot.query.filter_by(produit_id=produit_id)
    if entrepot_id:
        q = q.filter_by(entrepot_id=entrepot_id)
    lot = q.order_by(Lot.date_entree.desc(), Lot.id.desc()).first()
    if lot:
        return lot.cout_unitaire, lot
    p = db.session.get(Produit, produit_id)
    return (p.prix_revient or 0), None


def cmup(produit_id, entrepot_id=None):
    """Coût moyen du stock restant (indicatif)."""
    q = db.session.query(func.sum(Lot.qte_restante * Lot.cout_unitaire), func.sum(Lot.qte_restante)).filter(
        Lot.produit_id == produit_id, Lot.qte_restante > EPS)
    if entrepot_id:
        q = q.filter(Lot.entrepot_id == entrepot_id)
    v, qte = q.one()
    return (v / qte) if qte else dernier_cout(produit_id, entrepot_id)[0]


def etat_stock(entrepot_id=None, type_produit=None):
    """Synthèse par produit × entrepôt."""
    q = db.session.query(Lot.produit_id, Lot.entrepot_id, Lot.etat, func.sum(Lot.qte_restante),
                         func.sum(Lot.qte_restante * Lot.cout_unitaire)).join(Produit).filter(
        func.abs(Lot.qte_restante) > EPS)
    if entrepot_id:
        q = q.filter(Lot.entrepot_id == entrepot_id)
    if type_produit:
        q = q.filter(Produit.type_produit == type_produit)
    data = defaultdict(lambda: {"frais": 0, "congele": 0, "endommage": 0, "valeur": 0})
    for pid, eid, etat, qte, val in q.group_by(Lot.produit_id, Lot.entrepot_id, Lot.etat).all():
        d = data[(pid, eid)]
        d[etat if etat in d else "frais"] += qte or 0
        d["valeur"] += val or 0
    out = []
    for (pid, eid), d in data.items():
        p = db.session.get(Produit, pid)
        e = db.session.get(Entrepot, eid)
        vend = d["frais"] + d["congele"]
        res = reserve(pid, eid)
        out.append({"produit": p, "entrepot": e, "frais": round(d["frais"], 3), "congele": round(d["congele"], 3),
                    "endommage": round(d["endommage"], 3), "vendable": round(vend, 3), "reserve": res,
                    "disponible": round(vend - res, 3), "valeur": round(d["valeur"], 2),
                    "alerte": p.seuil_alerte and vend <= p.seuil_alerte})
    out.sort(key=lambda r: (r["produit"].type_produit != "vivant", r["produit"].nom, r["entrepot"].nom))
    return out


def lots_proches_peremption(jours=3):
    limite = date.today() + timedelta(days=jours)
    return Lot.query.filter(Lot.qte_restante > EPS, Lot.date_peremption.isnot(None), Lot.date_peremption <= limite,
                            Lot.etat.in_(ETATS_VENDABLES)).order_by(Lot.date_peremption).all()


def produits_en_alerte():
    out = []
    for p in Produit.query.filter(Produit.actif.is_(True), Produit.seuil_alerte > 0).all():
        q = stock_physique(p.id)
        if q <= p.seuil_alerte:
            out.append((p, q))
    return out


# ─────────── Écriture ───────────
def _code_lot(produit, d):
    base = f"L{d.strftime('%y%m%d')}-{(produit.code or 'P' + str(produit.id))[:8]}-"
    n = Lot.query.filter(Lot.code.like(base + "%")).count() + 1
    code = f"{base}{n:03d}"
    while Lot.query.filter_by(code=code).first():
        n += 1
        code = f"{base}{n:03d}"
    return code


def mouvement(type_mvt, lot, quantite, ref_type=None, ref_id=None, note=None, d=None, cout=None):
    cu = lot.cout_unitaire if cout is None else cout
    m = MouvementStock(date=d or datetime.now(), type_mvt=type_mvt, produit_id=lot.produit_id, lot_id=lot.id,
                       entrepot_id=lot.entrepot_id, quantite=round(quantite, 4), cout_unitaire=cu,
                       valeur=round(quantite * cu, 2), ref_type=ref_type, ref_id=ref_id, user_id=current_uid(),
                       note=note)
    db.session.add(m)
    return m


def creer_lot(produit, entrepot_id, quantite, cout_unitaire, origine, type_mvt, d=None, ref_type=None, ref_id=None,
              date_peremption=None, etat="frais", parent=None, poids_moyen=None, achat_ligne_id=None,
              transformation_id=None, note=None):
    if quantite <= EPS:
        raise BusinessError("Quantité d'entrée invalide.")
    if cout_unitaire < 0:
        raise BusinessError("Coût unitaire négatif.")
    d = d or datetime.now()
    if date_peremption is None and produit.duree_conservation_jours:
        jours = produit.duree_conservation_jours * (6 if etat == "congele" else 1)
        date_peremption = (d + timedelta(days=jours)).date()
    lot = Lot(code=_code_lot(produit, d), produit_id=produit.id, entrepot_id=entrepot_id, origine=origine,
              date_entree=d, date_peremption=date_peremption, qte_initiale=quantite, qte_restante=quantite,
              cout_unitaire=round(cout_unitaire, 4), etat=etat, poids_moyen=poids_moyen,
              achat_ligne_id=achat_ligne_id, transformation_id=transformation_id,
              lot_parent_id=parent.id if parent else None,
              compte_stock=compta.compte_stock_initial(produit, origine, parent))
    lot.produit = produit
    db.session.add(lot)
    db.session.flush()
    mouvement(type_mvt, lot, quantite, ref_type, ref_id, note, d)
    return lot


def consommer(produit_id, entrepot_id, quantite, type_mvt, ref_type=None, ref_id=None, d=None, lot_id=None,
              autoriser_negatif=False, etats=ETATS_VENDABLES, note=None, exclure_perimes=True):
    """Sort `quantite` du stock en FIFO (premier entré, premier sorti). Retourne [(lot, qte, cout_unitaire)].
    Les lots périmés ne sont jamais servis à un client ni transférés : ils doivent être déclarés en perte."""
    if quantite <= EPS:
        raise BusinessError("Quantité de sortie invalide.")
    q = Lot.query.filter(Lot.produit_id == produit_id, Lot.entrepot_id == entrepot_id, Lot.qte_restante > EPS)
    if etats:
        q = q.filter(Lot.etat.in_(etats))
    if exclure_perimes and not lot_id:
        jour = (d or datetime.now()).date()
        q = q.filter((Lot.date_peremption.is_(None)) | (Lot.date_peremption >= jour))
    if lot_id:
        q = q.filter(Lot.id == lot_id)
    lots = q.order_by(Lot.date_entree, Lot.id).with_for_update().all()
    total_dispo = sum(l.qte_restante for l in lots)
    if total_dispo + EPS < quantite and not autoriser_negatif:
        p = db.session.get(Produit, produit_id)
        e = db.session.get(Entrepot, entrepot_id)
        raise BusinessError(f"Stock insuffisant pour « {p.nom} » à {e.nom} : disponible {round(total_dispo, 3)} "
                            f"{p.unite}, demandé {round(quantite, 3)} {p.unite}.")
    reste = quantite
    out = []
    for lot in lots:
        if reste <= EPS:
            break
        pris = min(lot.qte_restante, reste)
        lot.qte_restante = round(lot.qte_restante - pris, 4)
        mouvement(type_mvt, lot, -pris, ref_type, ref_id, note, d)
        out.append((lot, pris, lot.cout_unitaire))
        reste = round(reste - pris, 4)
    if reste > EPS:  # vente au-delà du stock autorisée explicitement : le dernier lot connu passe en négatif
        cu, last = dernier_cout(produit_id, entrepot_id)
        if not last:
            raise BusinessError("Aucun lot de référence pour valoriser une sortie sans stock.")
        last.qte_restante = round(last.qte_restante - reste, 4)
        mouvement(type_mvt, last, -reste, ref_type, ref_id, (note or "") + " [stock négatif autorisé]", d)
        out.append((last, reste, last.cout_unitaire))
    return out


def restaurer(lot, quantite, type_mvt, ref_type=None, ref_id=None, d=None, note=None):
    lot.qte_restante = round(lot.qte_restante + quantite, 4)
    mouvement(type_mvt, lot, quantite, ref_type, ref_id, note, d)


def lignes_compta_sortie(consommations, sens_charge=True):
    """Écriture de sortie de stock : D variation / C stock."""
    lignes = []
    for lot, qte, cu in consommations:
        cs = compta.compte_stock(lot)
        v = round(qte * cu, 2)
        lignes.append((compta.compte_variation(cs), v, 0))
        lignes.append((cs, 0, v))
    return lignes


# ─────────── Opérations manuelles ───────────
def _operation(type_op, produit, entrepot_id, quantite, motif, d, entrepot_dest_id=None):
    op = OperationStock(numero=next_numero(OperationStock, "OPS", d), date=d, type_op=type_op, entrepot_id=entrepot_id,
                        entrepot_dest_id=entrepot_dest_id, produit_id=produit.id, quantite=quantite, motif=motif,
                        user_id=current_uid())
    db.session.add(op)
    db.session.flush()
    return op


def declarer_perte(produit_id, entrepot_id, quantite, type_perte, motif="", d=None, lot_id=None):
    if type_perte not in TYPES_PERTE:
        raise BusinessError("Type de perte inconnu.")
    d = d or datetime.now()
    compta.verifier_periode_ouverte(d)
    p = db.session.get(Produit, produit_id)
    if type_perte == "mortalite" and p.type_produit != "vivant":
        raise BusinessError("La mortalité ne concerne que les poulets vivants.")
    etats = None if type_perte in ("peremption", "casse") else ETATS_VENDABLES
    op = _operation(type_perte, p, entrepot_id, quantite, motif, d)
    conso = consommer(produit_id, entrepot_id, quantite, type_perte, "operation", op.id, d, lot_id=lot_id,
                      etats=etats, note=motif, exclure_perimes=False)
    op.valeur = round(sum(q * c for _, q, c in conso), 2)
    compta.passer("OD", d, f"{TYPES_MVT[type_perte]} – {p.nom} ({quantite} {p.unite})", lignes_compta_sortie(conso),
                  "operation", op.id)
    audit("perte", "operation_stock", op.id, {"type": type_perte, "produit": p.nom, "qte": quantite, "valeur": op.valeur})
    return op


def transferer(produit_id, src_id, dest_id, quantite, motif="", d=None):
    if src_id == dest_id:
        raise BusinessError("L'entrepôt de destination doit être différent de l'entrepôt d'origine.")
    d = d or datetime.now()
    p = db.session.get(Produit, produit_id)
    if disponible(produit_id, src_id, jour=d.date()) + EPS < quantite:
        raise BusinessError(f"Stock disponible insuffisant (une partie peut être réservée par des commandes).")
    op = _operation("transfert", p, src_id, quantite, motif, d, dest_id)
    conso = consommer(produit_id, src_id, quantite, "transfert_sortie", "operation", op.id, d, note=motif)
    for lot, qte, cu in conso:
        creer_lot(p, dest_id, qte, cu, "transfert", "transfert_entree", d, "operation", op.id,
                  date_peremption=lot.date_peremption, etat=lot.etat, parent=lot, poids_moyen=lot.poids_moyen)
    op.valeur = round(sum(q * c for _, q, c in conso), 2)
    audit("transfert", "operation_stock", op.id, {"produit": p.nom, "qte": quantite})
    return op


def changer_etat(produit_id, entrepot_id, quantite, nouvel_etat, motif="", d=None, lot_id=None):
    """Congélation (frais → congelé) ou déclassement (→ endommagé / impropre). Même valeur, pas d'impact comptable."""
    d = d or datetime.now()
    p = db.session.get(Produit, produit_id)
    if nouvel_etat not in ("congele", "endommage"):
        raise BusinessError("État cible invalide.")
    prefix = "congelation" if nouvel_etat == "congele" else "declassement"
    etats = ("frais",) if nouvel_etat == "congele" else ETATS_VENDABLES
    op = _operation(prefix, p, entrepot_id, quantite, motif, d)
    conso = consommer(produit_id, entrepot_id, quantite, prefix + "_sortie", "operation", op.id, d, lot_id=lot_id,
                      etats=etats, note=motif)
    for lot, qte, cu in conso:
        dp = None if nouvel_etat == "congele" else lot.date_peremption
        creer_lot(p, entrepot_id, qte, cu, "declassement", prefix + "_entree", d, "operation", op.id,
                  date_peremption=dp, etat=nouvel_etat, parent=lot)
    op.valeur = round(sum(q * c for _, q, c in conso), 2)
    return op


def entree_manuelle(produit_id, entrepot_id, quantite, cout_unitaire, motif="", d=None, date_peremption=None,
                    poids_moyen=None):
    """Stock initial / régularisation positive valorisée."""
    d = d or datetime.now()
    p = db.session.get(Produit, produit_id)
    op = _operation("entree_manuelle", p, entrepot_id, quantite, motif, d)
    lot = creer_lot(p, entrepot_id, quantite, cout_unitaire, "entree", "entree_manuelle", d, "operation", op.id,
                    date_peremption=date_peremption, poids_moyen=poids_moyen, note=motif)
    op.valeur = round(quantite * cout_unitaire, 2)
    cs = lot.compte_stock
    compta.passer("OD", d, f"Entrée de stock – {p.nom}", [(cs, op.valeur, 0), (compta.compte_variation(cs), 0, op.valeur)],
                  "operation", op.id)
    audit("entree_stock", "operation_stock", op.id, {"produit": p.nom, "qte": quantite, "cout": cout_unitaire})
    return op


# ─────────── Inventaire physique ───────────
def creer_inventaire(entrepot_id, d=None, note=""):
    d = d or datetime.now()
    inv = Inventaire(numero=next_numero(Inventaire, "INV", d), date=d, entrepot_id=entrepot_id, user_id=current_uid(),
                     note=note)
    db.session.add(inv)
    produits = Produit.query.filter_by(actif=True).order_by(Produit.type_produit, Produit.nom).all()
    for p in produits:
        th = stock_physique(p.id, entrepot_id)
        if abs(th) > EPS or p.type_produit in ("vivant", "decoupe", "abattu", "abats"):
            inv.lignes.append(InventaireLigne(produit_id=p.id, qte_theorique=th, qte_physique=th))
    db.session.flush()
    return inv


def valider_inventaire(inv, comptages, d=None):
    """comptages : {ligne_id: qte_physique}. Les écarts génèrent des ajustements tracés et comptabilisés."""
    if inv.statut != "brouillon":
        raise BusinessError("Inventaire déjà validé.")
    d = d or datetime.now()
    compta.verifier_periode_ouverte(d)
    lignes_cpt = []
    for l in inv.lignes:
        if l.id in comptages:
            l.qte_physique = comptages[l.id]
        l.qte_theorique = stock_physique(l.produit_id, inv.entrepot_id)  # recalcul au moment de la validation
        l.ecart = round(l.qte_physique - l.qte_theorique, 4)
        if abs(l.ecart) <= EPS:
            l.valeur_ecart = 0
            continue
        p = l.produit
        if l.ecart < 0:
            conso = consommer(p.id, inv.entrepot_id, -l.ecart, "ajustement_moins", "inventaire", inv.id, d,
                              note=f"Écart d'inventaire {inv.numero}", exclure_perimes=False)
            l.valeur_ecart = -round(sum(q * c for _, q, c in conso), 2)
            lignes_cpt += lignes_compta_sortie(conso)
        else:
            cu, ref = dernier_cout(p.id, inv.entrepot_id)
            lot = creer_lot(p, inv.entrepot_id, l.ecart, cu, "ajustement", "ajustement_plus", d, "inventaire", inv.id,
                            parent=ref, note=f"Écart d'inventaire {inv.numero}")
            l.valeur_ecart = round(l.ecart * cu, 2)
            cs = lot.compte_stock
            lignes_cpt += [(cs, l.valeur_ecart, 0), (compta.compte_variation(cs), 0, l.valeur_ecart)]
    inv.statut = "valide"
    compta.passer("OD", d, f"Écarts d'inventaire {inv.numero}", lignes_cpt, "inventaire", inv.id)
    audit("inventaire_valide", "inventaire", inv.id, {"ecarts": sum(1 for l in inv.lignes if abs(l.ecart) > EPS)})
    return inv


def rapprochement_stock():
    """Compare la valeur des lots (stock physique valorisé) aux soldes comptables 31/32/33/34/36."""
    from ..models import Transformation
    rows = []
    for cpt, lib in [("311", "Poulets vivants"), ("312", "Produits avicoles achetés"), ("322", "Consommables"),
                     ("335", "Emballages"), ("341", "En-cours de transformation"), ("361", "Produits finis")]:
        if cpt == "341":
            phys = round(sum(t.cout_matiere for t in Transformation.query.filter_by(statut="en_cours").all()), 2)
        else:
            phys = round(db.session.query(func.coalesce(func.sum(Lot.qte_restante * Lot.cout_unitaire), 0)).filter(
                Lot.compte_stock == cpt).scalar() or 0, 2)
        cptb = compta.solde_compte(cpt)
        rows.append({"compte": cpt, "libelle": lib, "physique": phys, "comptable": cptb, "ecart": round(phys - cptb, 2)})
    return rows
