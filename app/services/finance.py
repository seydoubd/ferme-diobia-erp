"""Trésorerie, règlements clients/fournisseurs, dépenses."""
from datetime import datetime, date, timedelta
from sqlalchemy import func
from ..extensions import db
from ..models import (CompteTresorerie, MouvementTresorerie, Paiement, PaiementAllocation, Vente, Achat, Client,
                      Fournisseur, Depense, CategorieDepense, Rapprochement)
from ..utils import BusinessError, EPS, current_uid, next_numero, audit, fcfa
from . import compta

MODES = {"especes": "Espèces", "wave": "Wave", "orange_money": "Orange Money", "virement": "Virement bancaire",
         "cheque": "Chèque", "credit": "À crédit"}
MODE_TYPE_COMPTE = {"especes": "caisse", "wave": "wave", "orange_money": "orange_money", "virement": "banque",
                    "cheque": "banque"}


def compte_pour_mode(mode):
    t = MODE_TYPE_COMPTE.get(mode)
    if not t:
        return None
    return CompteTresorerie.query.filter_by(type_compte=t, actif=True).order_by(CompteTresorerie.id).first()


# ─────────── Trésorerie ───────────
def solde(compte, fin=None):
    q = db.session.query(func.coalesce(func.sum(MouvementTresorerie.montant), 0)).filter(
        MouvementTresorerie.compte_id == compte.id)
    if fin:
        q = q.filter(MouvementTresorerie.date <= fin)
    return round((compte.solde_initial or 0) + (q.scalar() or 0), 2)


def tresorerie_totale():
    return round(sum(solde(c) for c in CompteTresorerie.query.filter_by(actif=True).all()), 2)


def mouvement_tresorerie(compte, montant, type_mvt, libelle, ref_type=None, ref_id=None, d=None, controle_solde=True):
    if compte is None:
        raise BusinessError("Compte de trésorerie obligatoire.")
    if montant < 0 and controle_solde and compte.type_compte != "banque":
        dispo = solde(compte)
        if dispo + montant < -0.5:
            raise BusinessError(f"Solde insuffisant sur « {compte.nom} » : {fcfa(dispo)} disponibles.")
    m = MouvementTresorerie(compte_id=compte.id, montant=round(montant, 2), type_mvt=type_mvt, libelle=libelle[:255],
                            ref_type=ref_type, ref_id=ref_id, date=d or datetime.now(), user_id=current_uid())
    db.session.add(m)
    return m


def creer_compte(nom, type_compte, solde_initial=0, numero=None):
    cpt, jr = {"caisse": ("571", "CA"), "banque": ("521", "BQ"), "wave": ("5521", "WV"),
               "orange_money": ("5522", "OM")}[type_compte]
    c = CompteTresorerie(nom=nom, type_compte=type_compte, compte_comptable=cpt, journal_code=jr, numero=numero,
                         solde_initial=0)
    db.session.add(c)
    db.session.flush()
    if solde_initial:
        # Le solde d'ouverture est un mouvement d'apport tracé et comptabilisé (À-nouveau)
        mouvement_tresorerie(c, solde_initial, "ouverture", f"Solde d'ouverture {nom}", "compte", c.id)
        compta.passer("AN", date.today(), f"Solde d'ouverture {nom}", [(cpt, solde_initial, 0), ("101", 0, solde_initial)],
                      "ouverture_compte", c.id)
    return c


def transferer(src, dest, montant, libelle="", d=None):
    if src.id == dest.id:
        raise BusinessError("Choisissez deux comptes différents.")
    if montant <= 0:
        raise BusinessError("Montant invalide.")
    d = d or datetime.now()
    compta.verifier_periode_ouverte(d)
    lib = libelle or f"Transfert {src.nom} → {dest.nom}"
    m1 = mouvement_tresorerie(src, -montant, "transfert", lib, d=d)
    db.session.flush()
    mouvement_tresorerie(dest, montant, "transfert", lib, "transfert", m1.id, d=d)
    m1.ref_type, m1.ref_id = "transfert", m1.id
    compta.passer("OD", d, lib, [(dest.compte_comptable, montant, 0), (src.compte_comptable, 0, montant)],
                  "transfert", m1.id)
    audit("transfert_fonds", "compte_tresorerie", src.id, {"vers": dest.nom, "montant": montant})
    return m1


def rapprocher(compte, date_releve, solde_releve, mouvement_ids):
    mvts = MouvementTresorerie.query.filter(MouvementTresorerie.id.in_(mouvement_ids),
                                            MouvementTresorerie.compte_id == compte.id).all()
    r = Rapprochement(compte_id=compte.id, date_releve=date_releve, solde_releve=solde_releve, solde_comptable=0,
                      user_id=current_uid())
    db.session.add(r)
    db.session.flush()
    for m in mvts:
        m.rapproche = True
        m.rapprochement_id = r.id
    rapproches = db.session.query(func.coalesce(func.sum(MouvementTresorerie.montant), 0)).filter(
        MouvementTresorerie.compte_id == compte.id, MouvementTresorerie.rapproche.is_(True)).scalar() or 0
    r.solde_comptable = round((compte.solde_initial or 0) + rapproches, 2)
    r.ecart = round(solde_releve - r.solde_comptable, 2)
    audit("rapprochement", "compte_tresorerie", compte.id, {"releve": solde_releve, "ecart": r.ecart})
    return r


def prevision(jours=60, pas=7):
    """Prévision : trésorerie actuelle + créances à encaisser − dettes à payer, par échéance."""
    t0 = tresorerie_totale()
    today = date.today()
    ventes = [v for v in Vente.query.filter_by(statut="valide").all() if v.solde > 0.5]
    achats = [a for a in Achat.query.filter_by(statut="recu").all() if a.solde > 0.5]
    rows = []
    cumul = t0
    debut = today
    retard_c = sum(v.solde for v in ventes if (v.echeance or today) < today)
    retard_f = sum(a.solde for a in achats if (a.echeance or today) < today)
    rows.append({"periode": "En retard (échu)", "entrees": round(retard_c, 2), "sorties": round(retard_f, 2)})
    cumul += retard_c - retard_f
    rows[-1]["solde"] = round(cumul, 2)
    while debut < today + timedelta(days=jours):
        fin = debut + timedelta(days=pas)
        e = sum(v.solde for v in ventes if debut <= (v.echeance or today) < fin)
        s = sum(a.solde for a in achats if debut <= (a.echeance or today) < fin)
        cumul += e - s
        rows.append({"periode": f"{debut.strftime('%d/%m')} – {(fin - timedelta(days=1)).strftime('%d/%m')}",
                     "entrees": round(e, 2), "sorties": round(s, 2), "solde": round(cumul, 2)})
        debut = fin
    return t0, rows


# ─────────── Règlements ───────────
def _numero_paiement(sens, d):
    return next_numero(Paiement, "ENC" if sens == "encaissement" else "DEC", d)


def encaisser(client, montant, compte, mode, d=None, reference=None, allocations=None):
    """Encaissement client, éventuellement partiel. allocations : {vente_id: montant} ; sinon FIFO sur les plus anciennes."""
    montant = round(montant, 2)
    if montant <= 0:
        raise BusinessError("Le montant encaissé doit être positif.")
    if mode not in MODES or mode == "credit":
        raise BusinessError("Mode de paiement invalide.")
    d = d or datetime.now()
    compta.verifier_periode_ouverte(d)
    ventes = Vente.query.filter_by(client_id=client.id, statut="valide").order_by(Vente.date, Vente.id).all()
    ouvertes = {v.id: v for v in ventes if v.solde > 0.005}
    if allocations is None:
        allocations, reste = {}, montant
        for v in ouvertes.values():
            if reste <= 0.005:
                break
            a = min(v.solde, reste)
            allocations[v.id] = round(a, 2)
            reste = round(reste - a, 2)
        if reste > 0.5:
            raise BusinessError(f"Le montant dépasse le total dû par le client ({fcfa(sum(v.solde for v in ouvertes.values()))}).")
    total_alloc = round(sum(allocations.values()), 2)
    if abs(total_alloc - montant) > 0.5:
        raise BusinessError("La répartition du règlement ne correspond pas au montant encaissé.")
    p = Paiement(numero=_numero_paiement("encaissement", d), date=d, sens="encaissement", client_id=client.id,
                 compte_id=compte.id, mode=mode, montant=montant, reference=reference, user_id=current_uid())
    db.session.add(p)
    db.session.flush()
    for vid, a in allocations.items():
        v = ouvertes.get(int(vid))
        if not v:
            raise BusinessError("Facture introuvable ou déjà soldée.")
        if a - v.solde > 0.5:
            raise BusinessError(f"Le règlement affecté à {v.numero} dépasse son solde ({fcfa(v.solde)}).")
        p.allocations.append(PaiementAllocation(vente_id=v.id, montant=a))
        v.montant_paye = round((v.montant_paye or 0) + a, 2)
    mouvement_tresorerie(compte, montant, "encaissement_client", f"Règlement {client.nom}", "paiement", p.id, d)
    compta.passer(compte.journal_code, d, f"Règlement client {client.nom} ({MODES[mode]})",
                  [(compte.compte_comptable, montant, 0), ("411", 0, montant)], "paiement", p.id, tiers=client.nom)
    audit("encaissement", "paiement", p.id, {"client": client.nom, "montant": montant, "mode": mode})
    return p


def payer_fournisseur(fournisseur, montant, compte, mode, d=None, reference=None, allocations=None):
    montant = round(montant, 2)
    if montant <= 0:
        raise BusinessError("Le montant doit être positif.")
    d = d or datetime.now()
    compta.verifier_periode_ouverte(d)
    achats = Achat.query.filter_by(fournisseur_id=fournisseur.id, statut="recu").order_by(Achat.date, Achat.id).all()
    ouverts = {a.id: a for a in achats if a.solde > 0.005}
    if allocations is None:
        allocations, reste = {}, montant
        for a in ouverts.values():
            if reste <= 0.005:
                break
            x = min(a.solde, reste)
            allocations[a.id] = round(x, 2)
            reste = round(reste - x, 2)
        if reste > 0.5:
            raise BusinessError(f"Le montant dépasse la dette envers ce fournisseur ({fcfa(sum(a.solde for a in ouverts.values()))}).")
    p = Paiement(numero=_numero_paiement("decaissement", d), date=d, sens="decaissement", fournisseur_id=fournisseur.id,
                 compte_id=compte.id, mode=mode, montant=montant, reference=reference, user_id=current_uid())
    db.session.add(p)
    db.session.flush()
    for aid, x in allocations.items():
        a = ouverts.get(int(aid))
        if not a:
            raise BusinessError("Facture fournisseur introuvable ou soldée.")
        if x - a.solde > 0.5:
            raise BusinessError(f"Le paiement affecté à {a.numero} dépasse son solde.")
        p.allocations.append(PaiementAllocation(achat_id=a.id, montant=x))
        a.montant_paye = round((a.montant_paye or 0) + x, 2)
    mouvement_tresorerie(compte, -montant, "paiement_fournisseur", f"Paiement {fournisseur.nom}", "paiement", p.id, d)
    compta.passer(compte.journal_code, d, f"Paiement fournisseur {fournisseur.nom}",
                  [("401", montant, 0), (compte.compte_comptable, 0, montant)], "paiement", p.id, tiers=fournisseur.nom)
    audit("decaissement", "paiement", p.id, {"fournisseur": fournisseur.nom, "montant": montant})
    return p


def annuler_paiement(p, motif=""):
    if p.statut != "valide":
        raise BusinessError("Règlement déjà annulé.")
    d = datetime.now()
    compta.verifier_periode_ouverte(d)
    for a in p.allocations:
        if a.vente:
            a.vente.montant_paye = round(a.vente.montant_paye - a.montant, 2)
        if a.achat:
            a.achat.montant_paye = round(a.achat.montant_paye - a.montant, 2)
    signe = -1 if p.sens == "encaissement" else 1
    mouvement_tresorerie(p.compte, signe * p.montant, "annulation_reglement", f"Annulation {p.numero} {motif}",
                         "paiement", p.id, d, controle_solde=(p.sens == "encaissement"))
    compta.contre_passer("paiement", p.id, d, f"règlement {p.numero}")
    p.statut = "annule"
    audit("annulation_reglement", "paiement", p.id, {"motif": motif})


def creances_clients():
    out = []
    for c in Client.query.order_by(Client.nom).all():
        ventes = Vente.query.filter_by(client_id=c.id, statut="valide").all()
        total = sum(v.total_ttc for v in ventes)
        du = sum(v.solde for v in ventes)
        if total == 0 and du == 0:
            continue
        retard = sum(v.solde for v in ventes if v.en_retard)
        echeance = min([v.echeance for v in ventes if v.solde > 0.5 and v.echeance] or [None]) if du > 0.5 else None
        out.append({"client": c, "total": round(total, 2), "encaisse": round(sum(v.montant_paye for v in ventes), 2),
                    "avoirs": round(sum(v.montant_avoirs for v in ventes), 2), "du": round(du, 2),
                    "retard": round(retard, 2), "echeance": echeance})
    return sorted(out, key=lambda r: -r["du"])


def dettes_fournisseurs():
    out = []
    for f in Fournisseur.query.order_by(Fournisseur.nom).all():
        achats = Achat.query.filter_by(fournisseur_id=f.id, statut="recu").all()
        total = sum(a.total_ttc for a in achats)
        if not total:
            continue
        du = sum(a.solde for a in achats)
        out.append({"fournisseur": f, "total": round(total, 2), "paye": round(sum(a.montant_paye for a in achats), 2),
                    "du": round(du, 2), "retard": round(sum(a.solde for a in achats if a.en_retard), 2),
                    "echeance": min([a.echeance for a in achats if a.solde > 0.5 and a.echeance] or [None])})
    return sorted(out, key=lambda r: -r["du"])


def solde_client(client):
    return round(sum(v.solde for v in Vente.query.filter_by(client_id=client.id, statut="valide").all()), 2)


# ─────────── Dépenses ───────────
def creer_depense(categorie, montant, compte, mode=None, d=None, description="", beneficiaire="", justificatif=None,
                  source="manuelle", incorporee_stock=False, transformation_id=None, paie_id=None, comptabiliser=True):
    montant = round(montant, 2)
    if montant <= 0:
        raise BusinessError("Montant de dépense invalide.")
    d = d or datetime.now()
    compta.verifier_periode_ouverte(d)
    dep = Depense(numero=next_numero(Depense, "DEP", d), date=d, categorie_id=categorie.id, montant=montant,
                  description=description, beneficiaire=beneficiaire, compte_id=compte.id if compte else None,
                  mode=mode, justificatif=justificatif, source=source, incorporee_stock=incorporee_stock,
                  transformation_id=transformation_id, paie_id=paie_id, user_id=current_uid())
    db.session.add(dep)
    db.session.flush()
    if comptabiliser:
        mouvement_tresorerie(compte, -montant, "depense", f"{categorie.nom} – {description}"[:255], "depense", dep.id, d)
        compta.passer(compte.journal_code, d, f"{categorie.nom} – {description or beneficiaire}"[:255],
                      [(categorie.compte_comptable, montant, 0), (compte.compte_comptable, 0, montant)],
                      "depense", dep.id, tiers=beneficiaire or None)
    audit("depense", "depense", dep.id, {"categorie": categorie.nom, "montant": montant})
    return dep


def annuler_depense(dep, motif=""):
    if dep.statut != "valide":
        raise BusinessError("Dépense déjà annulée.")
    if dep.source != "manuelle":
        raise BusinessError("Cette dépense est liée à une transformation ou une paie : annulez l'opération d'origine.")
    d = datetime.now()
    mouvement_tresorerie(dep.compte, dep.montant, "annulation_depense", f"Annulation {dep.numero} {motif}", "depense",
                         dep.id, d)
    compta.contre_passer("depense", dep.id, d, f"dépense {dep.numero}")
    dep.statut = "annulee"
    audit("annulation_depense", "depense", dep.id, {"motif": motif})


def categorie(nom):
    c = CategorieDepense.query.filter_by(nom=nom).first()
    if not c:
        raise BusinessError(f"Catégorie de dépense « {nom} » introuvable.")
    return c


def apport(compte, montant, d=None, libelle="Apport en capital"):
    """Apport de fonds par l'associé : trésorerie + écriture D 5x / C 101."""
    if montant <= 0:
        raise BusinessError("Montant invalide.")
    d = d or datetime.now()
    m = mouvement_tresorerie(compte, montant, "apport", libelle, d=d)
    db.session.flush()
    m.ref_type, m.ref_id = "apport", m.id
    compta.passer(compte.journal_code, d, libelle, [(compte.compte_comptable, montant, 0), ("101", 0, montant)],
                  "apport", m.id)
    audit("apport", "compte_tresorerie", compte.id, {"montant": montant})
    return m
