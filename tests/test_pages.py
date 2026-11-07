"""Toutes les pages s'affichent sans erreur, pour chaque rôle, et les droits sont respectés."""
import re
import pytest
from app.extensions import db


@pytest.fixture()
def demo(app):
    from app.seed import seed_demo
    seed_demo()
    return app


def login(client, email, pwd):
    return client.post("/login", data={"email": email, "password": pwd})


def urls(app):
    from app.models import Vente, Achat, Transformation, Client, Fournisseur, Lot, Commande, Inventaire, Ecriture, CompteTresorerie, Produit
    out = ["/dashboard", "/rentabilite", "/ventes", "/ventes/nouvelle", "/commandes", "/commandes/nouvelle", "/transformations",
           "/transformations/nouvelle", "/stocks/", "/stocks/lots", "/stocks/mouvements", "/stocks/operation", "/stocks/operations",
           "/stocks/inventaires", "/stocks/entrepots", "/achats/", "/achats/nouveau", "/clients", "/clients/nouveau", "/fournisseurs",
           "/fournisseurs/nouveau", "/credits", "/tresorerie", "/depenses", "/comptabilite/", "/comptabilite/grand-livre?compte=411",
           "/comptabilite/balance", "/comptabilite/etats", "/comptabilite/periodes", "/comptabilite/plan", "/comptabilite/od", "/rh/",
           "/rapports/", "/produits/", "/produits/nouveau", "/parametres", "/utilisateurs", "/audit", "/profil", "/api/stock",
           "/api/recherche?q=Dio", "/offline"]
    v = Vente.query.first()
    out += [f"/ventes/{v.id}", f"/ventes/{v.id}/imprimer", f"/ventes/{v.id}/imprimer?format=ticket", f"/ventes/{v.id}/pdf"]
    a = Achat.query.first()
    out += [f"/achats/{a.id}", f"/achats/{a.id}/imprimer"]
    for t in Transformation.query.all()[:1] + Transformation.query.filter_by(statut="en_cours").all()[:1]:
        out.append(f"/transformations/{t.id}")
    c = Client.query.first()
    out += [f"/clients/{c.id}", f"/clients/{c.id}/modifier", f"/api/client/{c.id}"]
    f = Fournisseur.query.first()
    out += [f"/fournisseurs/{f.id}", f"/fournisseurs/{f.id}/modifier"]
    lot = Lot.query.filter(Lot.transformation_id.isnot(None)).first()
    out += [f"/stocks/lots/{lot.id}", f"/stocks/lots/{Lot.query.first().id}"]
    for cmd in Commande.query.all():
        out += [f"/commandes/{cmd.id}", f"/commandes/{cmd.id}/bon-livraison"]
    out.append(f"/stocks/inventaires/{Inventaire.query.first().id}")
    out.append(f"/comptabilite/ecritures/{Ecriture.query.first().id}")
    out.append(f"/tresorerie/rapprochement/{CompteTresorerie.query.first().id}")
    out.append(f"/produits/{Produit.query.first().id}")
    from app.services.rapports import RAPPORTS
    for code in RAPPORTS:
        out += [f"/rapports/{code}", f"/rapports/{code}?format=pdf", f"/rapports/{code}?format=xlsx"]
    return out


def test_toutes_les_pages_admin(demo):
    c = demo.test_client()
    assert login(c, "admin@fermediobia.sn", "Diobia2026!").status_code == 302
    erreurs = []
    for u in urls(demo):
        r = c.get(u)
        if r.status_code != 200:
            erreurs.append((u, r.status_code))
    assert not erreurs, erreurs


def test_commercial_restreint(demo):
    c = demo.test_client()
    login(c, "caisse@fermediobia.sn", "Demo2026!")
    assert c.get("/dashboard").status_code == 200
    html = c.get("/dashboard").data.decode()
    assert "Bénéfice net estimé" not in html.split("<script")[0]
    for cle in ('"marge"', '"resultat"', '"marge_nette"', '"cout"'):
        assert cle not in html, cle          # aucune donnée de marge transmise au navigateur
    assert c.get("/ventes/nouvelle").status_code == 200
    for u in ["/rentabilite", "/comptabilite/", "/parametres", "/utilisateurs", "/tresorerie", "/rh/", "/produits/"]:
        assert c.get(u).status_code == 403, u
    page = c.get("/ventes").data.decode()
    assert "Marge" not in page


def test_parcours_vente_http(demo):
    """Vente via le formulaire réel, protection anti double soumission comprise."""
    from app.models import Vente, Client, Produit, Entrepot
    from app.services import stock
    c = demo.test_client()
    login(c, "admin@fermediobia.sn", "Diobia2026!")
    cl = Client.query.filter_by(nom="Mme Aminata Sow").first()
    p = Produit.query.filter_by(code="PV").first()
    e = Entrepot.query.filter_by(type_entrepot="ferme").first()
    avant = stock.stock_physique(p.id, e.id)
    n = Vente.query.count()
    data = {"_ft": "jeton-unique-123", "client_id": cl.id, "entrepot_id": e.id, "produit_id[]": [p.id], "quantite[]": ["2"],
            "prix_unitaire[]": [""], "remise_pct[]": [""], "mode": "especes", "montant_paye": "6600"}
    r = c.post("/ventes/nouvelle", data=data, headers={"X-Requested-With": "fetch"})
    assert r.json["ok"], r.json
    r2 = c.post("/ventes/nouvelle", data=data, headers={"X-Requested-With": "fetch"})
    assert not r2.json["ok"] and "déjà été envoyé" in r2.json["error"]
    assert Vente.query.count() == n + 1
    assert stock.stock_physique(p.id, e.id) == avant - 2
    # Saisie invalide : refus clair, rien n'est enregistré
    data["_ft"] = "autre-jeton"
    data["quantite[]"] = ["999999"]
    r3 = c.post("/ventes/nouvelle", data=data, headers={"X-Requested-With": "fetch"})
    assert not r3.json["ok"] and "insuffisant" in r3.json["error"]
    assert Vente.query.count() == n + 1


def test_simulation_transformation(demo):
    from app.models import Produit, Entrepot
    c = demo.test_client()
    login(c, "admin@fermediobia.sn", "Diobia2026!")
    pv = Produit.query.filter_by(code="PV").first()
    e = Entrepot.query.filter_by(type_entrepot="ferme").first()
    body = {"nb_poulets": 50, "produit_source_id": pv.id, "entrepot_source_id": e.id, "methode": "poids", "frais_abattage": 7500,
            "poids_vif_total": 100, "lignes": [{"produit_id": Produit.query.filter_by(code="CU").first().id, "quantite": 30},
                                               {"produit_id": Produit.query.filter_by(code="BL").first().id, "quantite": 30}]}
    r = c.post("/transformations/simuler", json=body)
    d = r.json
    assert d["ok"] and d["rendement"] == 60 and abs(sum(l["cout"] for l in d["lignes"]) - d["cout_total"]) < 0.01


def test_mode_sans_connexion(app):
    """Par défaut (paramètre désactivé), l'application s'ouvre directement sur le tableau de bord."""
    from app.utils import set_param
    set_param("connexion_obligatoire", "0")
    db.session.commit()
    c = app.test_client()
    assert c.get("/dashboard").status_code == 200
    assert c.get("/ventes").status_code == 200
    assert c.get("/login").headers["Location"].endswith("/dashboard")
    assert "Déconnexion" not in c.get("/dashboard").data.decode()
