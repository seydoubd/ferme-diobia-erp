"""Moteur comptable — SYSCOHADA révisé (inventaire permanent).

Schéma retenu (justifié pour un expert-comptable) :
- Achat marchandises :          D 601 (+6015 frais, +4452 TVA) / C 401
  Entrée en stock (coût réel) : D 311|312 / C 6031           → la marge se calcule sur coûts réels
- Vente :                        D 411 / C 701|702 (+706 livraison, +4431 TVA)
  Coût des ventes :              D 6031 / C 311|312  (marchandises)   ·  D 736 / C 361 (produits finis)
- Transformation (démarrage) :   D 341 En-cours / C 311 (poulets vivants)
  Transformation (fin) :         D 361 / C 341 (matière) / C 736 (frais incorporés)
  Frais d'abattage / main-d'œuvre : D 637 / C 57x (charge par nature, compensée par 736)
- Pertes / mortalités :          D 6031 / C 311  ·  D 736 / C 361
- Règlement client :             D 57x|52x|552x / C 411  ·  fournisseur : D 401 / C 5x
"""
from collections import defaultdict
from datetime import datetime, date
from sqlalchemy import func
from ..extensions import db
from ..models import Ecriture, LigneEcriture, Periode, CompteComptable, Journal
from ..utils import BusinessError, get_param, current_uid

PLAN_COMPTABLE = [
    ("101", "Capital social", 1), ("1013", "Capital souscrit, appelé, versé", 1),
    ("121", "Report à nouveau créditeur", 1), ("131", "Résultat net : bénéfice", 1), ("139", "Résultat net : perte", 1),
    ("162", "Emprunts auprès des établissements de crédit", 1),
    ("231", "Bâtiments industriels", 2), ("241", "Matériel et outillage (chambres froides, plumeuses…)", 2),
    ("245", "Matériel de transport", 2), ("2441", "Matériel de bureau et informatique", 2),
    ("311", "Marchandises – Poulets vivants", 3), ("312", "Marchandises – Produits avicoles achetés", 3),
    ("322", "Matières et fournitures consommables", 3), ("335", "Emballages", 3),
    ("341", "Produits en cours – Transformation", 3), ("361", "Produits finis – Produits de découpe", 3),
    ("401", "Fournisseurs", 4), ("4011", "Fournisseurs – Prestataires divers", 4),
    ("411", "Clients", 4), ("416", "Clients douteux ou litigieux", 4),
    ("421", "Personnel – Rémunérations dues", 4), ("4251", "Personnel – Avances et acomptes", 4),
    ("4431", "État – TVA facturée sur ventes", 4), ("4452", "État – TVA récupérable sur achats", 4),
    ("4441", "État – TVA due", 4), ("441", "État – Impôt sur les bénéfices", 4), ("471", "Débiteurs et créditeurs divers", 4),
    ("521", "Banques", 5), ("5521", "Monnaie électronique – Wave", 5), ("5522", "Monnaie électronique – Orange Money", 5),
    ("571", "Caisse", 5), ("585", "Virements de fonds", 5),
    ("601", "Achats de marchandises", 6), ("6015", "Frais sur achats (transport, manutention)", 6),
    ("6031", "Variation des stocks de marchandises", 6), ("6033", "Variation des stocks d'autres approvisionnements", 6),
    ("604", "Achats stockés de matières et fournitures consommables", 6), ("6051", "Fournitures non stockables – Eau", 6),
    ("6052", "Fournitures non stockables – Électricité", 6), ("6053", "Fournitures non stockables – Carburant", 6),
    ("608", "Achats d'emballages", 6), ("618", "Autres frais de transport", 6), ("6222", "Locations de bâtiments", 6),
    ("6242", "Entretien et réparations des biens mobiliers (chambres froides)", 6), ("6243", "Maintenance", 6),
    ("637", "Rémunérations de personnel extérieur (abattage, main-d'œuvre)", 6), ("638", "Autres charges externes", 6),
    ("641", "Impôts et taxes directs", 6), ("658", "Charges diverses", 6), ("661", "Rémunérations directes versées au personnel", 6),
    ("891", "Impôts sur les bénéfices de l'exercice", 8),
    ("701", "Ventes de marchandises – Poulets sur pied", 7), ("702", "Ventes de produits finis – Produits transformés", 7),
    ("706", "Services vendus – Livraisons", 7), ("736", "Variation des stocks de produits finis et en-cours", 7),
    ("758", "Produits divers", 7),
]

JOURNAUX = [("VT", "Journal des ventes"), ("AC", "Journal des achats"), ("CA", "Journal de caisse"),
            ("BQ", "Journal de banque"), ("WV", "Journal Wave"), ("OM", "Journal Orange Money"),
            ("OD", "Opérations diverses"), ("AN", "À-nouveaux")]


def init_plan():
    for num, lib, cl in PLAN_COMPTABLE:
        if not db.session.get(CompteComptable, num):
            db.session.add(CompteComptable(numero=num, intitule=lib, classe=cl))
    for code, lib in JOURNAUX:
        if not db.session.get(Journal, code):
            db.session.add(Journal(code=code, libelle=lib))


# ── Comptes selon la nature du stock ──
def compte_stock(lot):
    return lot.compte_stock


def compte_stock_initial(produit, origine, parent=None):
    """Compte de stock d'un nouveau lot : hérité du lot parent, 361 si issu de transformation, sinon selon le type."""
    if parent is not None:
        return parent.compte_stock
    if origine == "transformation":
        return "361"
    return {"vivant": "311", "emballage": "335", "consommable": "322"}.get(produit.type_produit, "312")


def compte_variation(cpt_stock):
    return {"361": "736", "341": "736", "335": "6033", "322": "6033"}.get(cpt_stock, "6031")


def compte_achat(produit):
    return {"emballage": "608", "consommable": "604"}.get(produit.type_produit, "601")


def compte_vente(lot_compte):
    return "702" if lot_compte == "361" else "701"


# ── Périodes ──
def periode(d):
    p = Periode.query.filter_by(annee=d.year, mois=d.month).first()
    if not p:
        p = Periode(annee=d.year, mois=d.month)
        db.session.add(p)
        db.session.flush()
    return p


def verifier_periode_ouverte(d):
    if isinstance(d, datetime):
        d = d.date()
    p = Periode.query.filter_by(annee=d.year, mois=d.month).first()
    if p and p.cloturee:
        raise BusinessError(f"La période {d.month:02d}/{d.year} est clôturée : aucune opération ne peut y être enregistrée. "
                            "Passez l'opération à une date de la période ouverte.")


def _numero(journal, d):
    base = f"{journal}{d.strftime('%y%m')}-"
    last = db.session.query(func.max(Ecriture.numero)).filter(Ecriture.numero.like(base + "%")).scalar()
    n = int(last.split("-")[-1]) + 1 if last else 1
    return f"{base}{n:05d}"


def passer(journal, d, libelle, lignes, source_type=None, source_id=None, nature="principal", tiers=None,
           contre_passation_de=None):
    """lignes : liste de (compte, debit, credit[, libelle]). Les lignes nulles sont ignorées, les comptes identiques fusionnés."""
    if isinstance(d, datetime):
        d = d.date()
    verifier_periode_ouverte(d)
    if source_type and source_id is not None:
        if Ecriture.query.filter_by(source_type=source_type, source_id=source_id, nature=nature).first():
            raise BusinessError("Écriture déjà générée pour cette opération (double comptabilisation empêchée).")
    agg = defaultdict(lambda: [0.0, 0.0, None])
    for l in lignes:
        cpt, deb, cre = l[0], round(l[1] or 0, 2), round(l[2] or 0, 2)
        if abs(deb) < 0.005 and abs(cre) < 0.005:
            continue
        if not db.session.get(CompteComptable, cpt):
            raise BusinessError(f"Compte comptable {cpt} inexistant dans le plan comptable.")
        a = agg[cpt]
        a[0] += deb
        a[1] += cre
        a[2] = l[3] if len(l) > 3 else a[2]
    if not agg:
        return None
    td = round(sum(a[0] for a in agg.values()), 2)
    tc = round(sum(a[1] for a in agg.values()), 2)
    if abs(td - tc) > 0.02:
        raise BusinessError(f"Écriture déséquilibrée ({td} ≠ {tc}).")
    periode(d)
    statut = "valide" if get_param("compta_validation_auto", "0") == "1" else "brouillard"
    e = Ecriture(journal_code=journal, date=d, libelle=libelle[:255], source_type=source_type, source_id=source_id,
                 nature=nature, statut=statut, numero=_numero(journal, d), user_id=current_uid(),
                 contre_passation_de=contre_passation_de)
    for cpt, (deb, cre, lib) in agg.items():
        # solde net par compte (évite D et C sur la même ligne)
        net = round(deb - cre, 2)
        if abs(net) < 0.005:
            continue
        e.lignes.append(LigneEcriture(compte_numero=cpt, debit=net if net > 0 else 0, credit=-net if net < 0 else 0,
                                      libelle=(lib or libelle)[:255], tiers=tiers))
    if not e.lignes:
        return None
    db.session.add(e)
    db.session.flush()
    return e


def contre_passer(source_type, source_id, d, libelle):
    """Annule toutes les écritures d'une opération par écritures inverses (jamais de suppression)."""
    if isinstance(d, datetime):
        d = d.date()
    ecr = Ecriture.query.filter_by(source_type=source_type, source_id=source_id).filter(
        ~Ecriture.nature.like("annul_%")).all()
    out = []
    for e in ecr:
        lignes = [(l.compte_numero, l.credit, l.debit, "Annulation " + (l.libelle or "")) for l in e.lignes]
        out.append(passer(e.journal_code, d, f"Annulation – {libelle}", lignes, source_type, source_id,
                          nature="annul_" + e.nature, tiers=e.lignes[0].tiers if e.lignes else None,
                          contre_passation_de=e.id))
    return out


def valider_ecritures(ids, user_id):
    n = 0
    for e in Ecriture.query.filter(Ecriture.id.in_(ids), Ecriture.statut == "brouillard").all():
        e.statut = "valide"
        e.valide_par = user_id
        e.valide_le = datetime.now()
        n += 1
    return n


def cloturer_periode(annee, mois, user_id):
    p = Periode.query.filter_by(annee=annee, mois=mois).first()
    if not p:
        p = Periode(annee=annee, mois=mois)
        db.session.add(p)
    if p.cloturee:
        raise BusinessError("Période déjà clôturée.")
    debut = date(annee, mois, 1)
    fin = date(annee + (mois == 12), (mois % 12) + 1, 1)
    nb = Ecriture.query.filter(Ecriture.date >= debut, Ecriture.date < fin, Ecriture.statut == "brouillard").count()
    if nb:
        raise BusinessError(f"{nb} écriture(s) non validée(s) sur la période : validez-les avant la clôture.")
    # Contrôle d'équilibre global de la période
    td, tc = db.session.query(func.coalesce(func.sum(LigneEcriture.debit), 0), func.coalesce(func.sum(LigneEcriture.credit), 0)) \
        .join(Ecriture).filter(Ecriture.date >= debut, Ecriture.date < fin).one()
    if abs(td - tc) > 0.05:
        raise BusinessError("La période n'est pas équilibrée : clôture impossible.")
    p.cloturee = True
    p.cloturee_le = datetime.now()
    p.cloturee_par = user_id
    return p


# ── États ──
def _base_query(debut=None, fin=None):
    q = db.session.query(LigneEcriture).join(Ecriture)
    if debut:
        q = q.filter(Ecriture.date >= debut)
    if fin:
        q = q.filter(Ecriture.date <= fin)
    return q


def balance(debut=None, fin=None):
    q = db.session.query(LigneEcriture.compte_numero, func.sum(LigneEcriture.debit), func.sum(LigneEcriture.credit)) \
        .join(Ecriture)
    if debut:
        q = q.filter(Ecriture.date >= debut)
    if fin:
        q = q.filter(Ecriture.date <= fin)
    rows = []
    for num, d, c in q.group_by(LigneEcriture.compte_numero).order_by(LigneEcriture.compte_numero).all():
        cpt = db.session.get(CompteComptable, num)
        d, c = round(d or 0, 2), round(c or 0, 2)
        rows.append({"numero": num, "intitule": cpt.intitule if cpt else "", "classe": cpt.classe if cpt else 0,
                     "debit": d, "credit": c, "solde_debiteur": max(d - c, 0), "solde_crediteur": max(c - d, 0)})
    return rows


def grand_livre(compte=None, debut=None, fin=None):
    q = _base_query(debut, fin)
    if compte:
        q = q.filter(LigneEcriture.compte_numero.like(compte + "%"))
    q = q.order_by(LigneEcriture.compte_numero, Ecriture.date, Ecriture.id)
    comptes = defaultdict(list)
    for l in q.all():
        comptes[l.compte_numero].append(l)
    out = []
    for num, lignes in comptes.items():
        solde = 0
        mvts = []
        for l in lignes:
            solde += l.debit - l.credit
            mvts.append({"date": l.ecriture.date, "numero": l.ecriture.numero, "journal": l.ecriture.journal_code,
                         "libelle": l.libelle, "debit": l.debit, "credit": l.credit, "solde": round(solde, 2),
                         "statut": l.ecriture.statut})
        cpt = db.session.get(CompteComptable, num)
        out.append({"numero": num, "intitule": cpt.intitule if cpt else "", "mouvements": mvts,
                    "total_debit": round(sum(m["debit"] for m in mvts), 2),
                    "total_credit": round(sum(m["credit"] for m in mvts), 2), "solde": round(solde, 2)})
    return out


def _solde_prefixes(bal, prefixes, sens="D"):
    tot = 0
    for r in bal:
        if any(r["numero"].startswith(p) for p in prefixes):
            tot += (r["debit"] - r["credit"]) if sens == "D" else (r["credit"] - r["debit"])
    return round(tot, 2)


def compte_resultat(debut=None, fin=None):
    bal = balance(debut, fin)
    s = lambda pref, sens: _solde_prefixes(bal, pref, sens)
    ventes_march = s(["701"], "C")
    achats_march = s(["601"], "D")
    var_march = s(["6031"], "D")
    marge_commerciale = ventes_march - achats_march - var_march
    ventes_prod = s(["702"], "C")
    services = s(["706"], "C")
    prod_stockee = s(["736"], "C")
    produits_divers = s(["758"], "C")
    achats_mat = s(["604", "605", "608"], "D") + s(["6033"], "D")
    transports = s(["61"], "D")
    services_ext = s(["62", "63"], "D")
    impots = s(["64"], "D")
    autres_ch = s(["65"], "D")
    personnel = s(["66"], "D")
    va = marge_commerciale + ventes_prod + services + prod_stockee + produits_divers - achats_mat - transports - services_ext - impots - autres_ch
    ebe = va - personnel
    re = ebe - s(["68"], "D")
    is_ = s(["89"], "D")
    lignes = [
        ("TA", "Ventes de marchandises (poulets sur pied)", ventes_march), ("RA", "Achats de marchandises", -achats_march),
        ("RB", "Variation de stocks de marchandises", -var_march), ("XA", "MARGE COMMERCIALE", marge_commerciale),
        ("TB", "Ventes de produits fabriqués (transformés)", ventes_prod), ("TC", "Travaux, services vendus", services),
        ("TD", "Production stockée (ou déstockage)", prod_stockee), ("TL", "Autres produits", produits_divers),
        ("RC", "Achats de matières, emballages, fournitures (y.c. variation)", -achats_mat),
        ("RE", "Transports", -transports), ("RF", "Services extérieurs", -services_ext), ("RG", "Impôts et taxes", -impots),
        ("RH", "Autres charges", -autres_ch), ("XC", "VALEUR AJOUTÉE", va), ("RI", "Charges de personnel", -personnel),
        ("XD", "EXCÉDENT BRUT D'EXPLOITATION", ebe), ("XE", "RÉSULTAT D'EXPLOITATION", re),
        ("RS", "Impôts sur le résultat", -is_), ("XI", "RÉSULTAT NET", re - is_),
    ]
    return {"lignes": lignes, "resultat": round(re - is_, 2)}


def bilan(fin=None):
    bal = balance(None, fin)
    cr = compte_resultat(None, fin)
    s = lambda pref, sens="D": _solde_prefixes(bal, pref, sens)
    # Tiers : soldes débiteurs à l'actif, créditeurs au passif (par compte)
    creances = sum(r["solde_debiteur"] for r in bal if r["classe"] == 4)
    dettes_tiers = sum(r["solde_crediteur"] for r in bal if r["classe"] == 4)
    tresorerie_actif = sum(r["solde_debiteur"] for r in bal if r["classe"] == 5)
    tresorerie_passif = sum(r["solde_crediteur"] for r in bal if r["classe"] == 5)
    actif = [("Immobilisations corporelles", s(["2"])), ("Stocks de marchandises", s(["31"])),
             ("Stocks matières et emballages", s(["32", "33"])), ("En-cours de production", s(["34"])),
             ("Produits finis", s(["36"])), ("Créances clients et autres débiteurs", round(creances, 2)),
             ("Trésorerie – actif", round(tresorerie_actif, 2))]
    passif = [("Capital", s(["101"], "C")), ("Report à nouveau", s(["12"], "C")),
              ("Résultat net de l'exercice", cr["resultat"]), ("Emprunts", s(["16"], "C")),
              ("Fournisseurs et autres dettes", round(dettes_tiers, 2)), ("Trésorerie – passif", round(tresorerie_passif, 2))]
    return {"actif": actif, "passif": passif, "total_actif": round(sum(v for _, v in actif), 2),
            "total_passif": round(sum(v for _, v in passif), 2)}


def solde_compte(prefix, fin=None):
    q = db.session.query(func.coalesce(func.sum(LigneEcriture.debit - LigneEcriture.credit), 0)).join(Ecriture) \
        .filter(LigneEcriture.compte_numero.like(prefix + "%"))
    if fin:
        q = q.filter(Ecriture.date <= fin)
    return round(q.scalar() or 0, 2)
