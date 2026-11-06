"""Scénarios réels : stocks, coûts, créances, trésorerie et comptabilité doivent rester exacts après chaque opération."""
from datetime import date
import pytest
from app.extensions import db
from app.models import (Entrepot, Fournisseur, Client, Produit, CompteTresorerie, Lot, Vente, Ecriture, LigneEcriture)
from app.services import achats, ventes, transformation as trf, stock, finance, compta, analytics
from app.utils import BusinessError


def P(code):
    return Produit.query.filter_by(code=code).first()


def cohérence():
    """Contrôles transverses exécutés après chaque étape."""
    td = db.session.query(db.func.sum(LigneEcriture.debit)).scalar() or 0
    tc = db.session.query(db.func.sum(LigneEcriture.credit)).scalar() or 0
    assert abs(td - tc) < 0.05, "Comptabilité déséquilibrée"
    for r in stock.rapprochement_stock():
        assert abs(r["ecart"]) < 0.05, f"Écart stock physique/comptable sur {r['compte']} : {r}"
    for c in CompteTresorerie.query.all():
        assert abs(finance.solde(c) - compta.solde_compte(c.compte_comptable)) < 0.05, f"Trésorerie ≠ compta ({c.nom})"
    creances = sum(r["du"] for r in finance.creances_clients())
    assert abs(creances - compta.solde_compte("411")) < 0.05, "Créances ≠ compte 411"
    dettes = sum(r["du"] for r in finance.dettes_fournisseurs())
    assert abs(dettes + compta.solde_compte("401")) < 0.05, "Dettes ≠ compte 401"
    assert Lot.query.filter(Lot.qte_restante < -1e-6).count() == 0, "Stock négatif non autorisé"


@pytest.fixture()
def base(app):
    ferme = Entrepot(nom="Ferme", type_entrepot="ferme")
    cf = Entrepot(nom="Chambre froide", type_entrepot="chambre_froide")
    f = Fournisseur(nom="Élevage Test", delai_paiement_jours=15)
    g = Client(nom="Grossiste Test", type_client="grossiste", tarif="grossiste", delai_paiement_jours=15)
    part = Client(nom="Particulier Test", type_client="particulier", tarif="particulier")
    db.session.add_all([ferme, cf, f, g, part])
    db.session.flush()
    caisse = CompteTresorerie.query.filter_by(type_compte="caisse").first()
    finance.apport(caisse, 2_000_000)
    db.session.commit()
    return dict(ferme=ferme, cf=cf, f=f, g=g, part=part, caisse=caisse)


def test_scenario_complet(app, admin, base):
    b = base
    pv = P("PV")

    # 1. Achat de 500 poulets à 2 500 FCFA + 50 000 FCFA de transport
    a = achats.creer_commande(b["f"].id, b["ferme"].id, [{"produit_id": pv.id, "quantite": 500, "prix_unitaire": 2500,
                                                         "poids_total": 1000}], frais_transport=50_000)
    achats.receptionner(a)
    db.session.commit()
    assert stock.stock_physique(pv.id) == 500
    lot = Lot.query.filter_by(produit_id=pv.id).first()
    assert lot.cout_unitaire == pytest.approx(2600)          # coût réel = prix + quote-part transport
    assert lot.poids_moyen == pytest.approx(2.0)
    assert a.solde == pytest.approx(1_300_000)
    assert stock.valeur_stock(pv.id) == pytest.approx(1_300_000)
    cohérence()

    # 2. Vente directe de 200 poulets sur pied au grossiste, à crédit
    v1 = ventes.creer_vente(b["g"].id, b["ferme"].id, [{"produit_id": pv.id, "quantite": 200}], admin,
                            paiement={"mode": "credit", "montant": 0})
    db.session.commit()
    assert v1.total_ttc == pytest.approx(200 * 2900)
    assert v1.cout_total == pytest.approx(200 * 2600)
    assert v1.marge == pytest.approx(200 * 300)
    assert stock.stock_physique(pv.id) == 300
    assert finance.solde_client(b["g"]) == pytest.approx(580_000)
    tres0 = finance.tresorerie_totale()
    assert tres0 == pytest.approx(2_000_000)                  # vente à crédit : pas de trésorerie
    cohérence()

    # 3. Transformation de 100 poulets
    t = trf.demarrer(pv.id, b["ferme"].id, b["cf"].id, 100, responsable="Chef découpe")
    db.session.commit()
    assert stock.stock_physique(pv.id) == 200
    assert t.cout_matiere == pytest.approx(260_000)
    assert t.poids_vif_total == pytest.approx(200)
    cohérence()                                               # 341 en-cours = 260 000
    sorties = [("CU", 40), ("BL", 30), ("AI", 12), ("PI", 15), ("CA", 25), ("AB", 8)]
    trf.terminer(t, [{"produit_id": P(c).id, "quantite": q} for c, q in sorties], "poids",
                 frais_abattage=15_000, frais_main_oeuvre=10_000, compte_frais_id=b["caisse"].id)
    db.session.commit()
    assert t.poids_obtenu == pytest.approx(130)
    assert t.rendement_pct == pytest.approx(65)
    assert t.pertes_kg == pytest.approx(70)
    assert t.cout_total == pytest.approx(285_000)
    assert sum(tp.cout_alloue for tp in t.produits) == pytest.approx(285_000)  # aucun FCFA perdu ni créé
    cu_cuisse = [tp for tp in t.produits if tp.produit.code == "CU"][0].cout_unitaire
    assert cu_cuisse == pytest.approx(285_000 / 130, rel=1e-4)
    assert stock.stock_physique(P("CU").id) == 40
    # La transformation n'est pas une vente
    assert Vente.query.count() == 1
    # Les frais sont des dépenses incorporées au stock, exclues des charges de gestion
    r = analytics.resultat()
    assert r["total_charges"] == 0
    assert finance.solde(b["caisse"]) == pytest.approx(2_000_000 - 25_000)
    cohérence()

    # 4. Vente de 30 kg de cuisses, payée comptant
    v2 = ventes.creer_vente(b["part"].id, b["cf"].id, [{"produit_id": P("CU").id, "quantite": 30}], admin,
                            paiement={"mode": "especes", "montant": 30 * 3800})
    db.session.commit()
    assert stock.stock_physique(P("CU").id) == 10
    assert v2.cout_total == pytest.approx(30 * cu_cuisse, abs=0.05)
    assert v2.statut_paiement == "payee"
    assert finance.solde(b["caisse"]) == pytest.approx(2_000_000 - 25_000 + 114_000)
    cohérence()

    # 5. Paiement partiel : facture de 500 000, règlement de 200 000 → reste 300 000
    v3 = ventes.creer_vente(b["g"].id, b["ferme"].id, [{"produit_id": pv.id, "quantite": 100, "prix_unitaire": 5000}], admin,
                            paiement={"mode": "credit", "montant": 0})
    finance.encaisser(b["g"], 200_000, b["caisse"], "especes", allocations={v3.id: 200_000})
    db.session.commit()
    assert v3.total_ttc == pytest.approx(500_000)
    assert v3.solde == pytest.approx(300_000)
    assert v3.statut_paiement == "partielle"
    cohérence()

    # 6. Déclaration de mortalité : 5 poulets
    op = stock.declarer_perte(pv.id, b["ferme"].id, 5, "mortalite", "Chaleur")
    db.session.commit()
    assert stock.stock_physique(pv.id) == 95
    assert op.valeur == pytest.approx(5 * 2600)
    assert analytics.resultat()["pertes"] == pytest.approx(13_000)
    cohérence()

    # 7. Inventaire avec écart : 9 kg de cuisses comptés au lieu de 10
    inv = stock.creer_inventaire(b["cf"].id)
    lc = [l for l in inv.lignes if l.produit.code == "CU"][0]
    stock.valider_inventaire(inv, {lc.id: 9})
    db.session.commit()
    assert lc.ecart == pytest.approx(-1)
    assert stock.stock_physique(P("CU").id) == pytest.approx(9)
    assert lc.valeur_ecart == pytest.approx(-cu_cuisse, abs=0.05)
    cohérence()

    # 8. Annulation d'une vente : refusée tant qu'un règlement existe, puis acceptée
    with pytest.raises(BusinessError):
        ventes.annuler_vente(v2, "Erreur de saisie", admin)
    db.session.rollback()
    p = v2.allocations[0].paiement
    finance.annuler_paiement(p, "Remboursement client")
    ventes.annuler_vente(v2, "Erreur de saisie", admin)
    db.session.commit()
    assert v2.statut == "annulee"
    assert stock.stock_physique(P("CU").id) == pytest.approx(39)
    assert finance.solde(b["caisse"]) == pytest.approx(2_000_000 - 25_000 + 200_000)
    cohérence()

    # Rentabilité : marges calculées sur coûts réels
    r = analytics.resultat()
    assert r["ca"] == pytest.approx(580_000 + 500_000)
    assert r["cmv"] == pytest.approx(200 * 2600 + 100 * 2600)
    circ = {c["cle"]: c for c in analytics.circuits()}
    assert circ["Poulets sur pied"]["marge"] == pytest.approx(1_080_000 - 780_000)

    # 9. Clôture comptable mensuelle
    with pytest.raises(BusinessError):
        compta.cloturer_periode(date.today().year, date.today().month, admin.id)  # écritures non validées
    db.session.rollback()
    compta.valider_ecritures([e.id for e in Ecriture.query.all()], admin.id)
    compta.cloturer_periode(date.today().year, date.today().month, admin.id)
    db.session.commit()
    with pytest.raises(BusinessError):
        stock.declarer_perte(pv.id, b["ferme"].id, 1, "mortalite", "après clôture")
    db.session.rollback()
    cohérence()


def test_pas_de_double_comptabilisation(app, admin, base):
    a = achats.creer_commande(base["f"].id, base["ferme"].id, [{"produit_id": P("PV").id, "quantite": 10, "prix_unitaire": 2500}])
    achats.receptionner(a)
    db.session.commit()
    with pytest.raises(BusinessError):
        achats.receptionner(a)                     # une réception ne peut être validée deux fois
    db.session.rollback()
    with pytest.raises(BusinessError):
        compta.passer("AC", date.today(), "doublon", [("601", 1, 0), ("401", 0, 1)], "achat", a.id)
    db.session.rollback()
    assert stock.stock_physique(P("PV").id) == 10


def test_vente_sans_stock_refusee_sauf_autorisation(app, admin, base):
    a = achats.creer_commande(base["f"].id, base["ferme"].id, [{"produit_id": P("PV").id, "quantite": 10, "prix_unitaire": 2500}])
    achats.receptionner(a)
    db.session.commit()
    with pytest.raises(BusinessError):
        ventes.creer_vente(base["g"].id, base["ferme"].id, [{"produit_id": P("PV").id, "quantite": 11}], admin,
                           paiement={"mode": "credit"})
    db.session.rollback()
    v = ventes.creer_vente(base["g"].id, base["ferme"].id, [{"produit_id": P("PV").id, "quantite": 11}], admin,
                           paiement={"mode": "credit"}, forcer_stock=True)
    db.session.commit()
    assert v.stock_force and stock.stock_physique(P("PV").id) == -1


def test_reservation_commande(app, admin, base):
    a = achats.creer_commande(base["f"].id, base["ferme"].id, [{"produit_id": P("PV").id, "quantite": 50, "prix_unitaire": 2500}])
    achats.receptionner(a)
    c = ventes.creer_commande(base["g"].id, base["ferme"].id, [{"produit_id": P("PV").id, "quantite": 40}])
    ventes.confirmer_commande(c)
    db.session.commit()
    assert stock.disponible(P("PV").id, base["ferme"].id) == 10
    with pytest.raises(BusinessError):              # la marchandise réservée ne peut pas être vendue deux fois
        ventes.creer_vente(base["part"].id, base["ferme"].id, [{"produit_id": P("PV").id, "quantite": 20}], admin,
                           paiement={"mode": "credit"})
    db.session.rollback()
    v = ventes.livrer_commande(c, admin, {"mode": "credit"})
    db.session.commit()
    assert stock.stock_physique(P("PV").id) == 10 and stock.disponible(P("PV").id) == 10
    assert c.statut == "livree" and v.commande_id == c.id


def test_commercial_ne_voit_pas_les_benefices(app, base):
    from app.models import User
    u = User(nom="Caissier", email="c@x.sn", role="commercial")
    assert not u.can("finances.voir") and not u.can("couts.modifier")
    assert u.can("ventes.creer")


def test_repartition_coefficients_et_manuelle(app):
    lignes = [{"poids_kg": 30, "coef": 1.6, "part_pct": 50}, {"poids_kg": 25, "coef": 0.25, "part_pct": 50}]
    c = trf.repartir(100_000, lignes, "coefficient")
    assert sum(c) == pytest.approx(100_000) and c[0] > c[1]
    assert trf.repartir(100_000, lignes, "manuelle") == [50_000, 50_000]
    with pytest.raises(BusinessError):
        trf.repartir(100_000, [{"poids_kg": 1, "coef": 1, "part_pct": 60}], "manuelle")


def test_demo_complete_et_supprimable(app):
    from app.seed import seed_demo
    from app.demo import purger_demo
    seed_demo()
    assert Vente.query.count() > 50
    assert Client.query.filter_by(demo=True).count() == 20
    assert Fournisseur.query.filter_by(demo=True).count() == 3
    assert Entrepot.query.filter_by(demo=True).count() == 3
    cohérence()
    purger_demo()
    assert Vente.query.count() == 0 and Lot.query.count() == 0 and Ecriture.query.count() == 0
    assert Produit.query.count() > 10                 # le catalogue de base est conservé
