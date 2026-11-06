"""Approvisionnement : bons de commande, réceptions, retours fournisseurs."""
from datetime import datetime, timedelta
from ..extensions import db
from ..models import Achat, AchatLigne, Produit, Fournisseur, Lot, MouvementStock, CompteTresorerie
from ..utils import BusinessError, EPS, current_uid, next_numero, audit, tva_taux, to_float
from . import compta, stock, finance


def creer_commande(fournisseur_id, entrepot_id, lignes, d=None, frais_transport=0, autres_frais=0, note="",
                   reference_facture=None, compte_frais_id=None):
    """lignes : [{produit_id, quantite, prix_unitaire, poids_total?}]"""
    if not lignes:
        raise BusinessError("Ajoutez au moins une ligne d'achat.")
    f = db.session.get(Fournisseur, fournisseur_id)
    if not f:
        raise BusinessError("Fournisseur introuvable.")
    d = d or datetime.now()
    a = Achat(numero=next_numero(Achat, "ACH", d), fournisseur_id=f.id, entrepot_id=entrepot_id, date=d,
              statut="commande", frais_transport=frais_transport or 0, autres_frais=autres_frais or 0, note=note,
              reference_facture=reference_facture, compte_frais_id=compte_frais_id or None, user_id=current_uid())
    for l in lignes:
        p = db.session.get(Produit, int(l["produit_id"]))
        q, pu = to_float(l["quantite"]), to_float(l["prix_unitaire"])
        if not p or q <= 0 or pu < 0:
            raise BusinessError("Ligne d'achat invalide (produit, quantité > 0 et prix ≥ 0 obligatoires).")
        a.lignes.append(AchatLigne(produit_id=p.id, quantite=q, prix_unitaire=pu, poids_total=l.get("poids_total") or None))
    _recalculer(a)
    db.session.add(a)
    db.session.flush()
    audit("bon_commande", "achat", a.id, {"fournisseur": f.nom, "total": a.total_ttc})
    return a


def _recalculer(a):
    a.total_marchandises = round(sum(l.quantite * l.prix_unitaire for l in a.lignes), 2)
    taux = tva_taux()
    a.tva = round(sum(l.quantite * l.prix_unitaire * taux for l in a.lignes if l.produit and l.produit.soumis_tva), 2) \
        if taux else 0
    frais_fournisseur = 0 if a.compte_frais_id else (a.frais_transport or 0) + (a.autres_frais or 0)
    a.total_ttc = round(a.total_marchandises + a.tva + frais_fournisseur, 2)


def receptionner(achat, d=None, mortalite=0, paiement=None, echeance=None, quantites_recues=None):
    """Valide la réception : entrées en stock au coût réel (prix + quote-part des frais), écritures, paiement éventuel.
    paiement : {"montant", "compte_id", "mode"}"""
    if achat.statut != "commande":
        raise BusinessError("Cette commande a déjà été réceptionnée ou annulée.")
    d = d or datetime.now()
    compta.verifier_periode_ouverte(d)
    if quantites_recues:
        for l in achat.lignes:
            if l.id in quantites_recues:
                q = to_float(quantites_recues[l.id])
                if q <= 0:
                    raise BusinessError("Quantité reçue invalide.")
                l.quantite = q
    for l in achat.lignes:
        l.produit = l.produit or db.session.get(Produit, l.produit_id)
    _recalculer(achat)
    frais = (achat.frais_transport or 0) + (achat.autres_frais or 0)
    base = achat.total_marchandises or 1
    reste = round(frais, 2)
    lignes_cpt = []
    for i, l in enumerate(achat.lignes):
        part = reste if i == len(achat.lignes) - 1 else round(frais * l.quantite * l.prix_unitaire / base, 2)
        reste = round(reste - part, 2)
        l.frais_alloues = part
        l.cout_unitaire_final = round((l.quantite * l.prix_unitaire + part) / l.quantite, 4)
        p = l.produit
        poids_moyen = (l.poids_total / l.quantite) if (l.poids_total and p.type_produit == "vivant") else None
        lot = stock.creer_lot(p, achat.entrepot_id, l.quantite, l.cout_unitaire_final, "achat", "entree_achat", d,
                              "achat", achat.id, date_peremption=l.date_peremption, poids_moyen=poids_moyen,
                              achat_ligne_id=l.id)
        valeur = round(l.quantite * l.cout_unitaire_final, 2)
        lignes_cpt += [(lot.compte_stock, valeur, 0), (compta.compte_variation(lot.compte_stock), 0, valeur)]
    achat.statut = "recu"
    achat.date_reception = d
    achat.echeance = echeance or (d + timedelta(days=achat.fournisseur.delai_paiement_jours or 0)).date()
    achat.mortalite_transport = int(mortalite or 0)

    # Facture fournisseur
    f = achat.fournisseur
    lignes_ac = []
    for l in achat.lignes:
        lignes_ac.append((compta.compte_achat(l.produit), round(l.quantite * l.prix_unitaire, 2), 0))
    if achat.tva:
        lignes_ac.append(("4452", achat.tva, 0))
    if frais and not achat.compte_frais_id:
        lignes_ac.append(("6015", frais, 0))
    lignes_ac.append(("401", 0, achat.total_ttc))
    compta.passer("AC", d, f"Achat {achat.numero} – {f.nom}", lignes_ac, "achat", achat.id, tiers=f.nom)
    if frais and achat.compte_frais_id:
        cpt = db.session.get(CompteTresorerie, achat.compte_frais_id)
        finance.mouvement_tresorerie(cpt, -frais, "frais_achat", f"Frais d'approche {achat.numero}", "achat", achat.id, d)
        compta.passer(cpt.journal_code, d, f"Frais d'approche {achat.numero}", [("6015", frais, 0), (cpt.compte_comptable, 0, frais)],
                      "achat", achat.id, nature="frais")
    compta.passer("OD", d, f"Entrée en stock {achat.numero}", lignes_cpt, "achat", achat.id, nature="stock")

    # Mortalité constatée à la réception : identifiée et valorisée (pas « cachée » dans le coût)
    if mortalite:
        vivants = [l for l in achat.lignes if l.produit.type_produit == "vivant"]
        if not vivants:
            raise BusinessError("Mortalité déclarée sans poulets vivants dans l'achat.")
        lot = Lot.query.filter_by(achat_ligne_id=vivants[0].id).first()
        stock.declarer_perte(lot.produit_id, lot.entrepot_id, int(mortalite), "mortalite",
                             f"Mortalité transport {achat.numero}", d, lot_id=lot.id)

    if paiement and to_float(paiement.get("montant")) > 0:
        db.session.flush()
        cpt = db.session.get(CompteTresorerie, int(paiement["compte_id"]))
        finance.payer_fournisseur(f, to_float(paiement["montant"]), cpt, paiement.get("mode", "especes"), d,
                                  allocations={achat.id: to_float(paiement["montant"])})
    audit("reception", "achat", achat.id, {"total": achat.total_ttc, "mortalite": mortalite})
    return achat


def annuler(achat, motif=""):
    if achat.statut == "annule":
        raise BusinessError("Achat déjà annulé.")
    if achat.statut == "commande":
        achat.statut = "annule"
        audit("annulation_bon_commande", "achat", achat.id, {"motif": motif})
        return achat
    if any(p.paiement.statut == "valide" for p in achat.allocations):
        raise BusinessError("Des paiements sont rattachés à cet achat : annulez-les d'abord.")
    d = datetime.now()
    lots = Lot.query.join(Lot.produit).filter(Lot.achat_ligne_id.in_([l.id for l in achat.lignes])).all()
    for lot in lots:
        autres = MouvementStock.query.filter(MouvementStock.lot_id == lot.id,
                                             MouvementStock.type_mvt.notin_(["entree_achat", "mortalite"])).count()
        if autres:
            raise BusinessError(f"Le lot {lot.code} a déjà été utilisé (vente, transformation…) : annulation impossible. "
                                "Utilisez un retour fournisseur.")
    for lot in lots:
        if lot.qte_restante > EPS:
            q = lot.qte_restante
            lot.qte_restante = 0
            stock.mouvement("annulation_achat", lot, -q, "achat", achat.id, motif, d)
    compta.contre_passer("achat", achat.id, d, f"achat {achat.numero}")
    for op_id, in db.session.query(MouvementStock.ref_id).filter(MouvementStock.ref_type == "operation",
                                                                 MouvementStock.lot_id.in_([l.id for l in lots]),
                                                                 MouvementStock.type_mvt == "mortalite").distinct():
        compta.contre_passer("operation", op_id, d, "mortalité réception annulée")
        from ..models import OperationStock
        op = db.session.get(OperationStock, op_id)
        op.statut = "annule"
    if achat.compte_frais_id and (achat.frais_transport or achat.autres_frais):
        cpt = db.session.get(CompteTresorerie, achat.compte_frais_id)
        finance.mouvement_tresorerie(cpt, (achat.frais_transport or 0) + (achat.autres_frais or 0), "annulation_achat",
                                     f"Annulation frais {achat.numero}", "achat", achat.id, d)
    achat.statut = "annule"
    audit("annulation_achat", "achat", achat.id, {"motif": motif})
    return achat


def retour_fournisseur(achat, ligne, quantite, motif="", d=None):
    """Renvoie des marchandises au fournisseur : sortie du lot d'origine et avoir sur la dette."""
    if achat.statut != "recu":
        raise BusinessError("Seuls les achats réceptionnés peuvent faire l'objet d'un retour.")
    d = d or datetime.now()
    lot = Lot.query.filter_by(achat_ligne_id=ligne.id).first()
    if not lot or lot.qte_restante + EPS < quantite:
        raise BusinessError("Quantité de retour supérieure au stock restant du lot d'origine.")
    conso = stock.consommer(lot.produit_id, lot.entrepot_id, quantite, "retour_fournisseur", "achat", achat.id, d,
                            lot_id=lot.id, etats=None, note=motif)
    montant = round(quantite * ligne.prix_unitaire, 2)
    tva = round(montant * tva_taux(), 2) if ligne.produit.soumis_tva else 0
    achat.montant_avoirs = round((achat.montant_avoirs or 0) + montant + tva, 2)
    n = MouvementStock.query.filter_by(ref_type="achat", ref_id=achat.id, type_mvt="retour_fournisseur").count()
    lignes = [("401", montant + tva, 0), (compta.compte_achat(ligne.produit), 0, montant)]
    if tva:
        lignes.append(("4452", 0, tva))
    compta.passer("AC", d, f"Retour fournisseur {achat.numero}", lignes, "achat", achat.id, nature=f"retour{n}",
                  tiers=achat.fournisseur.nom)
    compta.passer("OD", d, f"Sortie stock retour {achat.numero}", stock.lignes_compta_sortie(conso), "achat", achat.id,
                  nature=f"retour_stock{n}")
    audit("retour_fournisseur", "achat", achat.id, {"qte": quantite, "montant": montant})
    return montant
