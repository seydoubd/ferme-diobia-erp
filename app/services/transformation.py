"""Transformation et découpe : poulets vivants → produits commercialisables.

Étape 1 (démarrer) : les poulets SORTENT du stock vivant (FIFO ou lot imposé) et passent « en cours ».
Étape 2 (terminer) : saisie des quantités obtenues, répartition du coût (matière + frais) et entrée en stock.
Une transformation n'est jamais une vente : aucun chiffre d'affaires n'est généré.
"""
from datetime import datetime
from ..extensions import db
from ..models import (Transformation, TransformationSource, TransformationProduit, Produit, Lot, MouvementStock,
                      CompteTresorerie, Depense)
from ..utils import BusinessError, EPS, current_uid, next_numero, audit, to_float
from . import compta, stock, finance

METHODES = {"poids": "Au poids obtenu", "coefficient": "Coefficients de valeur", "manuelle": "Répartition manuelle (%)"}


def demarrer(produit_source_id, entrepot_source_id, entrepot_dest_id, nb_poulets, d=None, responsable="",
             poids_vif_total=None, lot_source_id=None, note=""):
    nb = int(nb_poulets or 0)
    if nb <= 0:
        raise BusinessError("Nombre de poulets à transformer invalide.")
    p = db.session.get(Produit, produit_source_id)
    if not p or p.type_produit != "vivant":
        raise BusinessError("La transformation part obligatoirement d'un stock de poulets vivants.")
    d = d or datetime.now()
    compta.verifier_periode_ouverte(d)
    if stock.disponible(p.id, entrepot_source_id) + EPS < nb and not lot_source_id:
        raise BusinessError(f"Seulement {stock.disponible(p.id, entrepot_source_id)} poulets disponibles (hors réservations).")
    t = Transformation(numero=next_numero(Transformation, "TRF", d), date=d, entrepot_source_id=entrepot_source_id,
                       entrepot_dest_id=entrepot_dest_id, produit_source_id=p.id, lot_source_id=lot_source_id or None,
                       responsable=responsable, nb_poulets=nb, note=note, statut="en_cours", user_id=current_uid())
    db.session.add(t)
    db.session.flush()
    conso = stock.consommer(p.id, entrepot_source_id, nb, "sortie_transformation", "transformation", t.id, d,
                            lot_id=lot_source_id, note=f"Transformation {t.numero}")
    poids_calc = 0
    for lot, q, cu in conso:
        t.sources.append(TransformationSource(lot_id=lot.id, quantite=q, cout_unitaire=cu))
        poids_calc += q * (lot.poids_moyen or 0)
    t.cout_matiere = round(sum(q * cu for _, q, cu in conso), 2)
    t.poids_vif_total = to_float(poids_vif_total) if poids_vif_total else round(poids_calc, 2)
    lignes = []
    for lot, q, cu in conso:
        v = round(q * cu, 2)
        lignes += [("341", v, 0), (lot.compte_stock, 0, v)]
    compta.passer("OD", d, f"Mise en transformation {t.numero} ({nb} poulets)", lignes, "transformation", t.id,
                  nature="demarrage")
    audit("transformation_demarree", "transformation", t.id, {"nb": nb, "cout_matiere": t.cout_matiere})
    return t


def repartir(cout_total, lignes, methode):
    """lignes : [{poids_kg, coef, part_pct}] → liste des coûts alloués (somme exacte = cout_total)."""
    if methode == "poids":
        bases = [l["poids_kg"] for l in lignes]
    elif methode == "coefficient":
        bases = [l["poids_kg"] * l["coef"] for l in lignes]
    elif methode == "manuelle":
        total_pct = sum(l["part_pct"] for l in lignes)
        if abs(total_pct - 100) > 0.01:
            raise BusinessError(f"La répartition manuelle doit totaliser 100 % (actuellement {round(total_pct, 2)} %).")
        bases = [l["part_pct"] for l in lignes]
    else:
        raise BusinessError("Méthode de répartition inconnue.")
    tot = sum(bases)
    if tot <= EPS:
        raise BusinessError("Base de répartition nulle : renseignez des poids ou coefficients positifs.")
    out, reste = [], round(cout_total, 2)
    for i, b in enumerate(bases):
        v = reste if i == len(bases) - 1 else round(cout_total * b / tot, 2)
        reste = round(reste - v, 2)
        out.append(v)
    return out


def terminer(t, produits, methode="poids", frais_abattage=0, frais_main_oeuvre=0, autres_frais=0, compte_frais_id=None,
             d=None):
    """produits : [{produit_id, quantite, poids_kg?, coef?, part_pct?, etat?}]"""
    if t.statut != "en_cours":
        raise BusinessError("Cette transformation n'est pas en cours.")
    produits = [x for x in produits if to_float(x.get("quantite")) > 0]
    if not produits:
        raise BusinessError("Renseignez au moins un produit obtenu.")
    d = d or datetime.now()
    compta.verifier_periode_ouverte(d)
    t.frais_abattage, t.frais_main_oeuvre, t.autres_frais = to_float(frais_abattage), to_float(frais_main_oeuvre), to_float(autres_frais)
    t.methode_repartition = methode
    frais = t.frais_total
    if frais > 0 and not compte_frais_id:
        raise BusinessError("Indiquez le compte de paiement des frais de transformation.")
    t.compte_frais_id = compte_frais_id or None

    lignes = []
    for x in produits:
        p = db.session.get(Produit, int(x["produit_id"]))
        if not p or p.type_produit == "vivant":
            raise BusinessError("Produit obtenu invalide.")
        q = to_float(x["quantite"])
        poids = to_float(x.get("poids_kg")) if p.unite != "kg" else q
        if p.unite != "kg" and poids <= 0:
            raise BusinessError(f"Indiquez le poids total (kg) pour « {p.nom} » (vendu à la {p.unite}).")
        lignes.append({"produit": p, "quantite": q, "poids_kg": poids,
                       "coef": to_float(x.get("coef"), p.coef_valeur or 1) if x.get("coef") not in (None, "") else (p.coef_valeur or 1),
                       "part_pct": to_float(x.get("part_pct")), "etat": x.get("etat") or "frais"})
    t.poids_obtenu = round(sum(l["poids_kg"] for l in lignes), 3)
    if t.poids_vif_total and t.poids_obtenu > t.poids_vif_total + EPS:
        raise BusinessError("Le poids obtenu ne peut pas dépasser le poids vif : vérifiez la saisie.")
    t.cout_total = round(t.cout_matiere + frais, 2)
    couts = repartir(t.cout_total, lignes, methode)

    for l, c in zip(lignes, couts):
        tp = TransformationProduit(produit_id=l["produit"].id, quantite=l["quantite"], poids_kg=l["poids_kg"],
                                   coef=l["coef"], part_manuelle_pct=l["part_pct"], cout_alloue=c,
                                   cout_unitaire=round(c / l["quantite"], 4), etat=l["etat"])
        t.produits.append(tp)
        lot = stock.creer_lot(l["produit"], t.entrepot_dest_id, l["quantite"], tp.cout_unitaire, "transformation",
                              "entree_transformation", d, "transformation", t.id, etat=l["etat"],
                              transformation_id=t.id, note=f"Issu de {t.numero}")
        tp.lot_id = lot.id

    if t.poids_vif_total:
        t.rendement_pct = round(t.poids_obtenu / t.poids_vif_total * 100, 2)
        t.pertes_kg = round(t.poids_vif_total - t.poids_obtenu, 3)
        t.valeur_pertes = round(t.cout_matiere * t.pertes_kg / t.poids_vif_total, 2)

    # Frais : charges réelles payées, incorporées au coût des produits (exclues des charges de gestion)
    compte = db.session.get(CompteTresorerie, compte_frais_id) if compte_frais_id else None
    for nom_cat, montant, lib in [("Abattage", t.frais_abattage, "Frais d'abattage"),
                                  ("Main-d'œuvre transformation", t.frais_main_oeuvre, "Main-d'œuvre"),
                                  ("Autres frais de transformation", t.autres_frais, "Autres frais")]:
        if montant > 0:
            finance.creer_depense(finance.categorie(nom_cat), montant, compte, "especes", d, f"{lib} {t.numero}",
                                  source="transformation", incorporee_stock=True, transformation_id=t.id)
    lignes_cpt = [("361", t.cout_total, 0), ("341", 0, t.cout_matiere)]
    if frais:
        lignes_cpt.append(("736", 0, frais))
    compta.passer("OD", d, f"Produits obtenus {t.numero}", lignes_cpt, "transformation", t.id, nature="production")
    t.statut = "terminee"
    t.date_fin = d
    audit("transformation_terminee", "transformation", t.id,
          {"poids": t.poids_obtenu, "rendement": t.rendement_pct, "cout_total": t.cout_total})
    return t


def annuler(t, motif=""):
    if t.statut == "annulee":
        raise BusinessError("Transformation déjà annulée.")
    d = datetime.now()
    compta.verifier_periode_ouverte(d)
    if t.statut == "terminee":
        for tp in t.produits:
            lot = tp.lot
            if lot and abs(lot.qte_restante - lot.qte_initiale) > EPS:
                raise BusinessError(f"Le lot {lot.code} issu de cette transformation a déjà été utilisé : annulation impossible. "
                                    "Déclarez plutôt une régularisation.")
        for tp in t.produits:
            lot = tp.lot
            q = lot.qte_restante
            lot.qte_restante = 0
            stock.mouvement("annulation_transformation", lot, -q, "transformation", t.id, motif, d)
        for dep in Depense.query.filter_by(transformation_id=t.id, statut="valide").all():
            finance.mouvement_tresorerie(dep.compte, dep.montant, "annulation_depense", f"Annulation {dep.numero}",
                                         "depense", dep.id, d)
            compta.contre_passer("depense", dep.id, d, f"dépense {dep.numero}")
            dep.statut = "annulee"
    for s in t.sources:
        stock.restaurer(s.lot, s.quantite, "annulation_transformation", "transformation", t.id, d, motif)
    compta.contre_passer("transformation", t.id, d, f"transformation {t.numero}")
    t.statut = "annulee"
    t.note = ((t.note or "") + f" | Annulée : {motif}")[:255]
    audit("transformation_annulee", "transformation", t.id, {"motif": motif})
    return t


def tracabilite_lot(lot):
    """Remonte un lot de produit transformé jusqu'aux lots de poulets vivants et aux achats d'origine."""
    chaine = []
    cur = lot
    while cur is not None and cur.parent is not None:
        cur = cur.parent
    if cur and cur.transformation_id:
        t = db.session.get(Transformation, cur.transformation_id)
        chaine.append(("transformation", t))
        for s in t.sources:
            chaine.append(("lot_vivant", s.lot))
    return chaine
