"""Ventes, avoirs (retours clients), commandes et livraisons."""
from datetime import datetime, timedelta
from ..extensions import db
from ..models import (Vente, VenteLigne, VenteLigneLot, Avoir, Client, Produit, CompteTresorerie, Commande,
                      CommandeLigne, Reservation, TARIF_PAR_TYPE)
from ..utils import BusinessError, EPS, current_uid, next_numero, audit, tva_taux, fcfa, to_float
from . import compta, stock, finance

REMISE_MAX_SANS_AUTORISATION = 10.0


def prix_suggere(produit, client):
    return produit.prix_pour(client.tarif or TARIF_PAR_TYPE.get(client.type_client, "particulier"))


def creer_vente(client_id, entrepot_id, lignes, user, d=None, remise_globale=0, paiement=None, echeance=None,
                forcer_stock=False, commande=None, frais_livraison=0, note=""):
    """lignes : [{produit_id, quantite, prix_unitaire?, remise_pct?}]
    paiement : {"montant", "mode", "compte_id"?} — mode "credit" ou montant 0 = vente à crédit."""
    client = db.session.get(Client, int(client_id))
    if not client:
        raise BusinessError("Client introuvable.")
    lignes = [l for l in lignes if l.get("produit_id") and to_float(l.get("quantite")) > 0]
    if not lignes:
        raise BusinessError("Ajoutez au moins un produit à la vente.")
    d = d or datetime.now()
    compta.verifier_periode_ouverte(d)
    if forcer_stock and not user.can("ventes.sans_stock"):
        raise BusinessError("Vous n'êtes pas autorisé à vendre au-delà du stock disponible.")
    v = Vente(numero=next_numero(Vente, "FAC", d), date=d, client_id=client.id, entrepot_id=entrepot_id,
              canal=client.canal, tarif=client.tarif, stock_force=bool(forcer_stock), note=note,
              commande_id=commande.id if commande else None, user_id=user.id,
              frais_livraison=round(to_float(frais_livraison), 2))
    db.session.add(v)
    db.session.flush()
    taux = tva_taux()
    exclure = [cl.id for cl in commande.lignes] if commande else None
    # 1. Contrôles et lignes brutes
    brut = []
    for l in lignes:
        p = db.session.get(Produit, int(l["produit_id"]))
        if not p or not p.vendable:
            raise BusinessError("Produit invalide ou non vendable.")
        q = to_float(l["quantite"])
        if p.unite in ("pièce", "tête", "carton") and abs(q - round(q)) > EPS:
            raise BusinessError(f"« {p.nom} » se vend à l'unité : quantité entière attendue.")
        sugg = prix_suggere(p, client)
        pu = to_float(l.get("prix_unitaire"), sugg) if l.get("prix_unitaire") not in (None, "") else sugg
        rem = to_float(l.get("remise_pct"), client.remise_defaut_pct or 0)
        if pu < 0 or rem < 0 or rem > 100:
            raise BusinessError("Prix ou remise invalide.")
        if (abs(pu - sugg) > 0.5 or rem > REMISE_MAX_SANS_AUTORISATION) and not user.can("ventes.prix_libre"):
            raise BusinessError(f"Modification du tarif de « {p.nom} » non autorisée (tarif {client.tarif} : {fcfa(sugg)}). "
                                f"Remise maximale sans autorisation : {int(REMISE_MAX_SANS_AUTORISATION)} %.")
        dispo = stock.disponible(p.id, entrepot_id, exclure, d.date())
        if dispo + EPS < q and not forcer_stock:
            raise BusinessError(f"Stock disponible insuffisant pour « {p.nom} » : {round(dispo, 3)} {p.unite} "
                                f"(demandé {q}). Les quantités réservées par des commandes confirmées sont exclues.")
        brut.append((p, q, pu, rem, sugg, round(q * pu * (1 - rem / 100), 2)))
    total_brut = sum(b[5] for b in brut)
    remise_globale = round(to_float(remise_globale), 2)
    if remise_globale < 0 or remise_globale > total_brut:
        raise BusinessError("Remise globale invalide.")
    if total_brut and remise_globale / total_brut * 100 > REMISE_MAX_SANS_AUTORISATION and not user.can("ventes.prix_libre"):
        raise BusinessError("Remise globale supérieure au plafond autorisé.")
    v.remise_globale = remise_globale

    # Libère les réservations de la commande livrée avant de consommer le stock
    if commande:
        for cl in commande.lignes:
            Reservation.query.filter_by(commande_ligne_id=cl.id, active=True).update({"active": False})
        db.session.flush()

    # 2. Sortie FIFO, coûts réels, répartition de la remise globale
    reste_remise = remise_globale
    lignes_vt, lignes_cmv = {}, []
    for i, (p, q, pu, rem, sugg, mht) in enumerate(brut):
        part = reste_remise if i == len(brut) - 1 else (round(remise_globale * mht / total_brut, 2) if total_brut else 0)
        reste_remise = round(reste_remise - part, 2)
        ht = round(mht - part, 2)
        tva = round(ht * taux, 2) if p.soumis_tva else 0
        vl = VenteLigne(produit_id=p.id, quantite=q, prix_suggere=sugg, prix_unitaire=pu, remise_pct=rem,
                        montant_ht=ht, tva=tva)
        v.lignes.append(vl)
        db.session.flush()
        conso = stock.consommer(p.id, entrepot_id, q, "sortie_vente", "vente", v.id, d, autoriser_negatif=forcer_stock,
                                note=v.numero)
        vl.cout_total = round(sum(qq * cu for _, qq, cu in conso), 2)
        for lot, qq, cu in conso:
            vl.lots.append(VenteLigneLot(lot_id=lot.id, quantite=qq, cout_unitaire=cu))
        # Produit : 702 si le stock vient de la transformation, sinon 701 (au prorata des quantités)
        q361 = sum(qq for lot, qq, _ in conso if lot.compte_stock == "361")
        ht361 = round(ht * q361 / q, 2) if q else 0
        lignes_vt["702"] = lignes_vt.get("702", 0) + ht361
        lignes_vt["701"] = lignes_vt.get("701", 0) + ht - ht361
        lignes_cmv += stock.lignes_compta_sortie(conso)
    v.total_ht = round(sum(l.montant_ht for l in v.lignes) + v.frais_livraison, 2)
    v.tva = round(sum(l.tva for l in v.lignes), 2)
    v.total_ttc = round(v.total_ht + v.tva, 2)
    v.cout_total = round(sum(l.cout_total for l in v.lignes), 2)
    v.echeance = echeance or (d + timedelta(days=client.delai_paiement_jours or 0)).date()

    # 3. Plafond de crédit
    montant_paye = to_float((paiement or {}).get("montant")) if paiement and paiement.get("mode") != "credit" else 0
    if montant_paye - v.total_ttc > 0.5:
        raise BusinessError(f"Le montant encaissé dépasse le total de la facture ({fcfa(v.total_ttc)}).")
    if client.plafond_credit and client.plafond_credit > 0:
        encours = finance.solde_client(client) - v.solde + (v.total_ttc - montant_paye)
        if encours > client.plafond_credit + 0.5 and not user.can("*"):
            raise BusinessError(f"Plafond de crédit dépassé pour {client.nom} : encours après vente {fcfa(encours)} "
                                f"pour un plafond de {fcfa(client.plafond_credit)}.")

    # 4. Écritures : facture + coût des ventes
    lignes_ecr = [("411", v.total_ttc, 0)] + [(c, 0, m) for c, m in lignes_vt.items()]
    if v.frais_livraison:
        lignes_ecr.append(("706", 0, v.frais_livraison))
    if v.tva:
        lignes_ecr.append(("4431", 0, v.tva))
    compta.passer("VT", d, f"Facture {v.numero} – {client.nom}", lignes_ecr, "vente", v.id, tiers=client.nom)
    compta.passer("OD", d, f"Coût des ventes {v.numero}", lignes_cmv, "vente", v.id, nature="cout")

    # 5. Encaissement immédiat (total ou partiel)
    if montant_paye > 0:
        mode = paiement.get("mode", "especes")
        cpt = db.session.get(CompteTresorerie, int(paiement["compte_id"])) if paiement.get("compte_id") \
            else finance.compte_pour_mode(mode)
        if not cpt:
            raise BusinessError("Aucun compte de trésorerie pour ce mode de paiement.")
        finance.encaisser(client, montant_paye, cpt, mode, d, paiement.get("reference"), allocations={v.id: montant_paye})
    audit("vente", "vente", v.id, {"client": client.nom, "total": v.total_ttc, "paye": montant_paye,
                                   "stock_force": bool(forcer_stock)})
    return v


def annuler_vente(v, motif, user):
    if v.statut == "annulee":
        raise BusinessError("Vente déjà annulée.")
    if not motif:
        raise BusinessError("Le motif d'annulation est obligatoire.")
    if any(a.paiement.statut == "valide" for a in v.allocations):
        raise BusinessError("Des règlements sont affectés à cette facture : annulez-les (remboursement) avant d'annuler la vente.")
    if v.avoirs:
        raise BusinessError("Cette vente a déjà fait l'objet d'un avoir : annulation complète impossible.")
    d = datetime.now()
    compta.verifier_periode_ouverte(d)
    for l in v.lignes:
        for vll in l.lots:
            stock.restaurer(vll.lot, vll.quantite, "annulation_vente", "vente", v.id, d, f"Annulation {v.numero}")
    compta.contre_passer("vente", v.id, d, f"facture {v.numero}")
    v.statut = "annulee"
    v.motif_annulation = motif
    if v.commande_id:
        c = db.session.get(Commande, v.commande_id)
        if c and c.statut == "livree":
            c.statut = "annulee"
    audit("annulation_vente", "vente", v.id, {"motif": motif, "total": v.total_ttc})
    return v


def creer_avoir(v, ligne, quantite, motif, remise_en_stock=True, etat="frais", d=None):
    """Retour client partiel : réduit la créance (ou crée un crédit client) et remet éventuellement en stock."""
    if v.statut != "valide":
        raise BusinessError("Vente annulée.")
    quantite = to_float(quantite)
    if quantite <= 0 or quantite > ligne.quantite - (ligne.qte_retournee or 0) + EPS:
        raise BusinessError("Quantité retournée invalide.")
    d = d or datetime.now()
    compta.verifier_periode_ouverte(d)
    ratio = quantite / ligne.quantite
    a = Avoir(numero=next_numero(Avoir, "AV", d), date=d, vente_id=v.id, vente_ligne_id=ligne.id, quantite=quantite,
              montant_ht=round(ligne.montant_ht * ratio, 2), tva=round(ligne.tva * ratio, 2),
              cout=round(ligne.cout_total * ratio, 2), remise_en_stock=remise_en_stock, motif=motif,
              user_id=current_uid())
    a.montant_ttc = round(a.montant_ht + a.tva, 2)
    db.session.add(a)
    db.session.flush()
    ligne.qte_retournee = round((ligne.qte_retournee or 0) + quantite, 4)
    v.montant_avoirs = round((v.montant_avoirs or 0) + a.montant_ttc, 2)
    premier = ligne.lots[0].lot if ligne.lots else None
    compte_prod = "702" if premier and premier.compte_stock == "361" else "701"
    lignes = [(compte_prod, a.montant_ht, 0), ("411", 0, a.montant_ttc)]
    if a.tva:
        lignes.append(("4431", a.tva, 0))
    compta.passer("VT", d, f"Avoir {a.numero} sur {v.numero}", lignes, "avoir", a.id, tiers=v.client.nom)
    if remise_en_stock and premier:
        cu = ligne.cout_total / ligne.quantite
        lot = stock.creer_lot(ligne.produit, v.entrepot_id, quantite, cu, "retour_client", "retour_client", d, "avoir",
                              a.id, date_peremption=premier.date_peremption, etat=etat, parent=premier,
                              note=f"Retour {v.numero}")
        cs = lot.compte_stock
        compta.passer("OD", d, f"Retour en stock {a.numero}", [(cs, a.cout, 0), (compta.compte_variation(cs), 0, a.cout)],
                      "avoir", a.id, nature="stock")
    audit("avoir", "vente", v.id, {"avoir": a.numero, "montant": a.montant_ttc, "stock": remise_en_stock})
    return a


# ─────────── Commandes clients ───────────
def creer_commande(client_id, entrepot_id, lignes, d=None, date_livraison_prevue=None, adresse="", frais_livraison=0,
                   note=""):
    client = db.session.get(Client, int(client_id))
    if not client:
        raise BusinessError("Client introuvable.")
    lignes = [l for l in lignes if l.get("produit_id") and to_float(l.get("quantite")) > 0]
    if not lignes:
        raise BusinessError("Ajoutez au moins un produit.")
    d = d or datetime.now()
    c = Commande(numero=next_numero(Commande, "CMD", d), date=d, client_id=client.id, entrepot_id=entrepot_id,
                 date_livraison_prevue=date_livraison_prevue, adresse_livraison=adresse or client.adresse,
                 frais_livraison=to_float(frais_livraison), note=note, user_id=current_uid())
    for l in lignes:
        p = db.session.get(Produit, int(l["produit_id"]))
        pu = to_float(l.get("prix_unitaire"), prix_suggere(p, client)) if l.get("prix_unitaire") not in (None, "") \
            else prix_suggere(p, client)
        c.lignes.append(CommandeLigne(produit_id=p.id, quantite=to_float(l["quantite"]), prix_unitaire=pu))
    db.session.add(c)
    db.session.flush()
    audit("commande", "commande", c.id, {"client": client.nom})
    return c


def confirmer_commande(c):
    """Réserve le stock : la marchandise ne peut plus être vendue à un autre client."""
    if c.statut != "attente":
        raise BusinessError("Seules les commandes en attente peuvent être confirmées.")
    for l in c.lignes:
        dispo = stock.disponible(l.produit_id, c.entrepot_id)
        if dispo + EPS < l.quantite:
            raise BusinessError(f"Stock disponible insuffisant pour réserver « {l.produit.nom} » ({round(dispo, 3)} {l.produit.unite}).")
        db.session.add(Reservation(commande_ligne_id=l.id, produit_id=l.produit_id, entrepot_id=c.entrepot_id,
                                   quantite=l.quantite))
    c.statut = "confirmee"
    audit("commande_confirmee", "commande", c.id)


def passer_en_preparation(c, livreur_id=None):
    if c.statut != "confirmee":
        raise BusinessError("La commande doit être confirmée avant préparation.")
    c.statut = "preparation"
    if livreur_id:
        c.livreur_id = livreur_id


def livrer_commande(c, user, paiement=None, d=None):
    if c.statut not in ("confirmee", "preparation"):
        raise BusinessError("Commande non confirmée.")
    lignes = [{"produit_id": l.produit_id, "quantite": l.quantite, "prix_unitaire": l.prix_unitaire,
               "remise_pct": 0} for l in c.lignes]
    v = creer_vente(c.client_id, c.entrepot_id, lignes, user, d, paiement=paiement, commande=c,
                    frais_livraison=c.frais_livraison, note=f"Livraison {c.numero}")
    c.vente_id = v.id
    c.statut = "livree"
    c.date_livraison = d or datetime.now()
    audit("livraison", "commande", c.id, {"vente": v.numero})
    return v


def annuler_commande(c, motif=""):
    if c.statut in ("livree", "annulee"):
        raise BusinessError("Commande déjà livrée ou annulée.")
    for l in c.lignes:
        Reservation.query.filter_by(commande_ligne_id=l.id, active=True).update({"active": False})
    c.statut = "annulee"
    c.note = ((c.note or "") + f" | Annulée : {motif}")[:255]
    audit("commande_annulee", "commande", c.id, {"motif": motif})
