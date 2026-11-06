"""Ressources humaines : avances, paie. Les salaires alimentent dépenses, trésorerie et comptabilité."""
from datetime import datetime
from ..extensions import db
from ..models import Employe, AvanceSalaire, Paie, CompteTresorerie
from ..utils import BusinessError, current_uid, audit, MOIS_FR
from . import compta, finance


def avance(employe, montant, compte, d=None):
    if montant <= 0:
        raise BusinessError("Montant invalide.")
    d = d or datetime.now()
    a = AvanceSalaire(employe_id=employe.id, montant=montant, compte_id=compte.id, date=d)
    db.session.add(a)
    db.session.flush()
    finance.mouvement_tresorerie(compte, -montant, "avance_salaire", f"Avance sur salaire {employe.nom}", "avance", a.id, d)
    compta.passer(compte.journal_code, d, f"Avance sur salaire {employe.nom}",
                  [("4251", montant, 0), (compte.compte_comptable, 0, montant)], "avance", a.id, tiers=employe.nom)
    audit("avance_salaire", "employe", employe.id, {"montant": montant})
    return a


def avances_en_cours(employe):
    return AvanceSalaire.query.filter_by(employe_id=employe.id, paie_id=None).all()


def payer_salaire(employe, annee, mois, compte, primes=0, retenues=0, d=None):
    if Paie.query.filter_by(employe_id=employe.id, annee=annee, mois=mois).first():
        raise BusinessError(f"Le salaire de {MOIS_FR[mois]} {annee} a déjà été payé à {employe.nom}.")
    d = d or datetime.now()
    brut = round((employe.salaire_base or 0) + (primes or 0) - (retenues or 0), 2)
    if brut <= 0:
        raise BusinessError("Salaire brut nul ou négatif.")
    avs = avances_en_cours(employe)
    total_av = round(sum(a.montant for a in avs), 2)
    if total_av > brut:
        raise BusinessError("Les avances dépassent le salaire du mois : déduction partielle à gérer manuellement.")
    net = round(brut - total_av, 2)
    p = Paie(employe_id=employe.id, annee=annee, mois=mois, salaire_base=employe.salaire_base, primes=primes,
             retenues=retenues, avances_deduites=total_av, brut=brut, net_a_payer=net, compte_id=compte.id,
             date_paiement=d)
    db.session.add(p)
    db.session.flush()
    for a in avs:
        a.paie_id = p.id
    # Dépense « Salaires » pour le suivi de gestion (l'écriture est passée ci-dessous, pas de doublon)
    finance.creer_depense(finance.categorie("Salaires"), brut, compte, "virement", d,
                          f"Salaire {MOIS_FR[mois]} {annee} – {employe.nom}", employe.nom, source="paie", paie_id=p.id,
                          comptabiliser=False)
    if net > 0:
        finance.mouvement_tresorerie(compte, -net, "salaire", f"Salaire {employe.nom} {mois:02d}/{annee}", "paie", p.id, d)
    lignes = [("661", brut, 0), ("4251", 0, total_av), (compte.compte_comptable, 0, net)]
    compta.passer(compte.journal_code, d, f"Salaire {MOIS_FR[mois]} {annee} – {employe.nom}", lignes, "paie", p.id,
                  tiers=employe.nom)
    audit("paie", "employe", employe.id, {"mois": f"{mois}/{annee}", "net": net})
    return p
