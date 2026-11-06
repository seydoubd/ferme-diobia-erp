"""Données de base (référentiels) et jeu de démonstration réaliste et supprimable."""
import os
import random
from datetime import datetime, timedelta, date
from .extensions import db
from .models import (Parametre, User, Categorie, Produit, Entrepot, Fournisseur, Client, CompteTresorerie,
                     CategorieDepense, Employe, Livreur, TARIF_PAR_TYPE, Lot)
from .utils import set_param, get_param
from .services import compta

PARAMS = {
    "nom_entreprise": "La Ferme Diobia", "slogan": "Volailles fraîches – de la ferme à votre table",
    "adresse": "Diobia, Sénégal", "telephone": "+221 77 000 00 00", "email": "contact@fermediobia.sn",
    "ninea": "", "rccm": "", "devise": "FCFA", "tva_active": "0", "tva_taux": "18", "taux_is": "30",
    "compta_validation_auto": "0", "alerte_peremption_jours": "2",
    "mentions_facture": "Merci de votre confiance. Paiement par Wave, Orange Money, espèces ou virement.",
}

CATEGORIES = [("Volailles vivantes", "Poulets de chair sur pied"), ("Poulets entiers", "Poulets abattus, plumés, prêts à cuire"),
              ("Découpes", "Pièces de découpe"), ("Abats", "Abats, foies, gésiers"), ("Emballages", "Sachets, barquettes, cartons"),
              ("Consommables", "Produits d'hygiène, glace, etc.")]

# code, nom, catégorie, type, unité, revient, gros, revendeur, restaurant, détail, seuil, conservation (j), coef
PRODUITS = [
    ("PV", "Poulet vivant", "Volailles vivantes", "vivant", "tête", 2500, 2900, 3000, 3100, 3300, 150, 0, 1),
    ("PE", "Poulet entier", "Poulets entiers", "abattu", "pièce", 3200, 3800, 3900, 4100, 4500, 20, 5, 1.0),
    ("BL", "Blanc de poulet", "Découpes", "decoupe", "kg", 2900, 4300, 4500, 4600, 5000, 10, 3, 1.6),
    ("FI", "Filets", "Découpes", "decoupe", "kg", 3200, 4500, 4700, 4800, 5000, 5, 3, 1.8),
    ("CU", "Cuisses", "Découpes", "decoupe", "kg", 2000, 3300, 3400, 3500, 3800, 15, 3, 1.1),
    ("HC", "Hauts de cuisse", "Découpes", "decoupe", "kg", 2200, 3400, 3500, 3600, 3800, 5, 3, 1.2),
    ("PI", "Pilons", "Découpes", "decoupe", "kg", 2000, 3300, 3400, 3500, 3800, 10, 3, 1.1),
    ("AI", "Ailes", "Découpes", "decoupe", "kg", 1600, 2900, 3000, 3100, 3300, 8, 3, 0.9),
    ("CA", "Carcasses", "Découpes", "decoupe", "kg", 450, 700, 750, 800, 900, 0, 2, 0.25),
    ("DO", "Dos", "Découpes", "decoupe", "kg", 550, 800, 850, 900, 1000, 0, 2, 0.3),
    ("AB", "Abats", "Abats", "abats", "kg", 650, 1100, 1150, 1200, 1300, 0, 2, 0.35),
    ("FO", "Foies", "Abats", "abats", "kg", 1400, 2200, 2300, 2400, 2500, 2, 2, 0.8),
    ("GE", "Gésiers", "Abats", "abats", "kg", 1400, 2200, 2300, 2400, 2500, 2, 2, 0.8),
    ("SV", "Sachets sous vide", "Emballages", "emballage", "pièce", 50, 0, 0, 0, 0, 200, 0, 0),
    ("CT", "Cartons isothermes", "Emballages", "emballage", "carton", 600, 0, 0, 0, 0, 20, 0, 0),
]

CATEGORIES_DEPENSES = [
    ("Transport", "618", "charge"), ("Électricité", "6052", "charge"), ("Eau", "6051", "charge"),
    ("Salaires", "661", "charge"), ("Loyer", "6222", "charge"), ("Carburant", "6053", "charge"),
    ("Abattage", "637", "charge"), ("Main-d'œuvre transformation", "637", "charge"),
    ("Autres frais de transformation", "638", "charge"), ("Emballages (non stockés)", "608", "charge"),
    ("Maintenance", "6243", "charge"), ("Entretien des chambres froides", "6242", "charge"),
    ("Impôts et taxes", "641", "charge"), ("Autres charges", "638", "charge"),
    ("Investissement – Matériel", "241", "investissement"), ("Investissement – Véhicule", "245", "investissement"),
]


def init_base():
    """Idempotent : crée les référentiels manquants sans toucher aux données existantes."""
    for k, v in PARAMS.items():
        if not db.session.get(Parametre, k):
            db.session.add(Parametre(cle=k, valeur=v))
    compta.init_plan()
    for nom, desc in CATEGORIES:
        if not Categorie.query.filter_by(nom=nom).first():
            db.session.add(Categorie(nom=nom, description=desc))
    db.session.flush()
    for code, nom, cat, t, u, rev, gros, revd, rest, det, seuil, cons, coef in PRODUITS:
        if not Produit.query.filter_by(code=code).first():
            c = Categorie.query.filter_by(nom=cat).first()
            db.session.add(Produit(code=code, nom=nom, categorie_id=c.id, type_produit=t, unite=u, prix_revient=rev,
                                   prix_gros=gros, prix_revendeur=revd, prix_restaurant=rest, prix_detail=det,
                                   seuil_alerte=seuil, duree_conservation_jours=cons, coef_valeur=coef,
                                   vendable=t not in ("emballage", "consommable")))
    for nom, cpt, nature in CATEGORIES_DEPENSES:
        if not CategorieDepense.query.filter_by(nom=nom).first():
            db.session.add(CategorieDepense(nom=nom, compte_comptable=cpt, nature=nature))
    for nom, t, cpt, jr in [("Caisse principale", "caisse", "571", "CA"), ("Banque – compte courant", "banque", "521", "BQ"),
                            ("Wave", "wave", "5521", "WV"), ("Orange Money", "orange_money", "5522", "OM")]:
        if not CompteTresorerie.query.filter_by(type_compte=t).first():
            db.session.add(CompteTresorerie(nom=nom, type_compte=t, compte_comptable=cpt, journal_code=jr))
    if not User.query.filter_by(role="admin").first():
        u = User(nom="Administrateur", email=os.environ.get("ADMIN_EMAIL", "admin@fermediobia.sn"), role="admin")
        u.set_password(os.environ.get("ADMIN_PASSWORD", "Diobia2026!"))
        db.session.add(u)
    db.session.commit()


# ───────────────────────────── Démo ─────────────────────────────
FOURNISSEURS = [("Élevage Ndiaye & Fils", "Thiès", "77 512 34 56", 15), ("Coopérative avicole de Mbour", "Mbour", "76 220 11 09", 7),
                ("Keur Massar Volailles & Emballages", "Keur Massar", "78 455 90 12", 0)]
CLIENTS = [
    ("Grossiste Sandaga – M. Diop", "grossiste", "Marché Sandaga, Dakar", 2_000_000, 15),
    ("Volailles Tilène SARL", "grossiste", "Marché Tilène, Dakar", 1_500_000, 15),
    ("Thiaroye Distribution", "grossiste", "Thiaroye", 1_000_000, 10),
    ("Revendeuse Awa Ndiaye", "revendeur", "Marché Castors", 300_000, 7),
    ("Boutique Keur Fatou", "revendeur", "Parcelles Assainies", 200_000, 7),
    ("Moustapha Fall – revendeur", "revendeur", "Pikine", 250_000, 7),
    ("Restaurant Le Lagon Bleu", "restaurant", "Corniche Ouest, Dakar", 500_000, 30),
    ("Chez Loutcha", "restaurant", "Médina, Dakar", 300_000, 15),
    ("Dibiterie Al Baraka", "restaurant", "Grand Yoff", 200_000, 7),
    ("Snack Teranga Grill", "restaurant", "Almadies", 250_000, 15),
    ("Hôtel Savana Plage", "hotel", "Saly Portudal", 1_000_000, 30),
    ("Hôtel Les Filaos", "hotel", "Somone", 800_000, 30),
    ("Supermarché Baobab", "supermarche", "Point E, Dakar", 1_500_000, 30),
    ("Mini-market Liberté 6", "supermarche", "Liberté 6, Dakar", 600_000, 15),
    ("Boucherie Serigne Touba", "boucherie", "Marché HLM", 400_000, 7),
    ("Boucherie moderne Fass", "boucherie", "Fass", 300_000, 7),
    ("Mme Aminata Sow", "particulier", "Mermoz", 0, 0),
    ("M. Ibrahima Ba", "particulier", "Sacré-Cœur", 0, 0),
    ("Mme Khady Faye", "particulier", "Ouakam", 50_000, 0),
    ("Client comptoir", "particulier", "Point de vente", 0, 0),
]


class _U:
    """Utilisateur technique pour la génération de démo (droits complets)."""
    id = None

    def can(self, p):
        return True


def seed_demo(seed=42):
    from .demo import mode_demo
    from .services import finance, achats, transformation as trf, ventes, stock, rh
    from .models import User as U
    rnd = random.Random(seed)
    today = datetime.now().replace(second=0, microsecond=0)
    J = lambda n, h=9: min((today - timedelta(days=n)).replace(hour=h, minute=(n * 7 + h * 13) % 60), today)
    admin = U.query.filter_by(role="admin").first()
    user = _U()
    user.id = admin.id
    with mode_demo():
        # Utilisateurs de démonstration (un par rôle)
        for nom, email, role in [("Moussa Sarr", "stock@fermediobia.sn", "stock"),
                                 ("Fatou Diallo", "caisse@fermediobia.sn", "commercial"),
                                 ("Cheikh Mbaye", "compta@fermediobia.sn", "comptable"),
                                 ("Ousmane Gueye", "entrepot@fermediobia.sn", "entrepot")]:
            if not U.query.filter_by(email=email).first():
                u = U(nom=nom, email=email, role=role)
                u.set_password("Demo2026!")
                db.session.add(u)
        ferme = Entrepot(nom="Ferme Diobia – Élevage", type_entrepot="ferme", localisation="Diobia")
        cf = Entrepot(nom="Chambre froide – Ferme", type_entrepot="chambre_froide", localisation="Diobia")
        pdv = Entrepot(nom="Point de vente Dakar", type_entrepot="magasin", localisation="Dakar")
        db.session.add_all([ferme, cf, pdv])
        fours = []
        for nom, ville, tel, delai in FOURNISSEURS:
            f = Fournisseur(nom=nom, adresse=ville, telephone="+221 " + tel, delai_paiement_jours=delai)
            db.session.add(f)
            fours.append(f)
        clients = []
        for i, (nom, t, adr, plafond, delai) in enumerate(CLIENTS):
            c = Client(nom=nom, type_client=t, tarif=TARIF_PAR_TYPE[t], adresse=adr, plafond_credit=plafond,
                       delai_paiement_jours=delai, telephone=f"+221 7{rnd.choice('0678')} {rnd.randint(100, 999)} "
                                                         f"{rnd.randint(10, 99)} {rnd.randint(10, 99)}")
            db.session.add(c)
            clients.append(c)
        livreurs = [Livreur(nom="Mamadou Diagne", telephone="+221 77 640 22 18", vehicule="Tricycle frigorifique"),
                    Livreur(nom="Pape Ndour", telephone="+221 76 118 45 70", vehicule="Fourgonnette")]
        db.session.add_all(livreurs)
        emps = [Employe(nom="Moussa Sarr", poste="Responsable de stock", salaire_base=200_000, date_embauche=date(2024, 3, 1)),
                Employe(nom="Fatou Diallo", poste="Caissière / commerciale", salaire_base=150_000, date_embauche=date(2024, 6, 1)),
                Employe(nom="Abdou Faye", poste="Agent d'abattage", salaire_base=100_000, date_embauche=date(2025, 1, 15)),
                Employe(nom="Ousmane Gueye", poste="Magasinier", salaire_base=120_000, date_embauche=date(2025, 2, 1))]
        db.session.add_all(emps)
        db.session.flush()

        caisse = CompteTresorerie.query.filter_by(type_compte="caisse").first()
        banque = CompteTresorerie.query.filter_by(type_compte="banque").first()
        wave = CompteTresorerie.query.filter_by(type_compte="wave").first()
        om = CompteTresorerie.query.filter_by(type_compte="orange_money").first()
        finance.apport(caisse, 2_500_000, J(42, 8), "Apport initial en caisse (démo)")
        finance.apport(banque, 9_000_000, J(42, 8), "Apport initial en banque (démo)")
        finance.apport(wave, 300_000, J(42, 8), "Approvisionnement Wave (démo)")
        finance.apport(om, 200_000, J(42, 8), "Approvisionnement Orange Money (démo)")
        P = {p.code: p for p in Produit.query.all()}
        pv = P["PV"]

        EV = []  # (date, ordre, action) — exécutés dans l'ordre chronologique pour une traçabilité FIFO réaliste

        def ev(d, fn, ordre=5):
            EV.append((d, ordre, len(EV), fn))

        # ── 5 lots de poulets vivants (achats) + emballages ──
        plan_achats = [(40, 0, 1000, 2500, 2000, 100_000, 6, {"montant": 1_300_000, "mode": "virement", "compte_id": banque.id}),
                       (33, 1, 600, 2450, 1230, 60_000, 3, {"montant": 1_530_000, "mode": "virement", "compte_id": banque.id}),
                       (24, 0, 800, 2550, 1640, 80_000, 4, None),
                       (14, 1, 700, 2500, 1400, 70_000, 0, {"montant": 900_000, "mode": "virement", "compte_id": banque.id}),
                       (4, 0, 1000, 2600, 2100, 100_000, 5, None)]
        for jours, fi, q, pu, poids, transport, morts, paie in plan_achats:
            def _achat(jours=jours, fi=fi, q=q, pu=pu, poids=poids, transport=transport, morts=morts, paie=paie):
                a = achats.creer_commande(fours[fi].id, ferme.id, [{"produit_id": pv.id, "quantite": q, "prix_unitaire": pu,
                                                                   "poids_total": poids}], J(jours + 2), frais_transport=transport)
                achats.receptionner(a, J(jours, 7), mortalite=morts, paiement=paie)
            ev(J(jours, 7), _achat, 0)

        def _emb():
            a = achats.creer_commande(fours[2].id, cf.id, [{"produit_id": P["SV"].id, "quantite": 2000, "prix_unitaire": 50},
                                                            {"produit_id": P["CT"].id, "quantite": 40, "prix_unitaire": 600}], J(39))
            achats.receptionner(a, J(38), paiement={"montant": 44_000, "mode": "wave", "compte_id": wave.id})
        ev(J(38, 8), _emb, 0)

        # ── Transformations ──
        def sortie(nb, jours, en_cours=False):
            nb = min(nb, int(stock.disponible(pv.id, ferme.id)))
            if nb < 10:
                return None
            t = trf.demarrer(pv.id, ferme.id, cf.id, nb, J(jours, 6), "Moussa Sarr")
            if en_cours:
                return t
            k = nb / 100
            f = lambda x: round(x * k * rnd.uniform(0.95, 1.05), 1)
            ent = max(1, int(round(20 * k)))
            produits = [{"produit_id": P["PE"].id, "quantite": ent, "poids_kg": round(ent * 1.5, 1)},
                        {"produit_id": P["BL"].id, "quantite": f(26)}, {"produit_id": P["CU"].id, "quantite": f(22)},
                        {"produit_id": P["PI"].id, "quantite": f(14)}, {"produit_id": P["AI"].id, "quantite": f(11)},
                        {"produit_id": P["CA"].id, "quantite": f(22)}, {"produit_id": P["FO"].id, "quantite": f(3.5)},
                        {"produit_id": P["GE"].id, "quantite": f(4)}, {"produit_id": P["AB"].id, "quantite": f(7)}]
            trf.terminer(t, produits, rnd.choice(["coefficient", "coefficient", "poids"]), frais_abattage=150 * nb,
                         frais_main_oeuvre=100 * nb, compte_frais_id=caisse.id, d=J(jours, 7))
            return t
        for nb, jours in [(150, 38), (120, 31), (150, 26), (150, 21), (130, 13), (150, 8), (150, 5), (100, 2)]:
            ev(J(jours, 6), (lambda nb=nb, jours=jours: sortie(nb, jours)), 1)
        ev(J(0, 6), lambda: sortie(60, 0, en_cours=True), 1)

        # ── Mortalités ──
        ev(J(20, 16), lambda: stock.declarer_perte(pv.id, ferme.id, 3, "mortalite", "Coup de chaleur", J(20, 16)))
        ev(J(9, 16), lambda: stock.declarer_perte(pv.id, ferme.id, 2, "mortalite", "Maladie respiratoire", J(9, 16)))

        def transfert(jours):
            for code, part in [("PE", .2), ("BL", .2), ("CU", .2), ("PI", .2), ("AI", .15), ("GE", .2), ("FO", .2)]:
                q = stock.disponible(P[code].id, cf.id, jour=J(jours, 8).date())
                q = round(q * part, 1) if P[code].unite == "kg" else int(q * part)
                if q > 0:
                    stock.transferer(P[code].id, cf.id, pdv.id, q, "Réassort point de vente", J(jours, 8))
        for jours in list(range(37, -1, -2)) + [0]:
            ev(J(jours, 8), (lambda jours=jours: transfert(jours)), 2)

        # ── Ventes : sur pied (gros) et transformés (gros & détail) ──
        def vendre(c, lignes, jours, entrepot, part_payee, mode="especes", h=11):
            ok = []
            for code, q in lignes:
                dispo = stock.disponible(P[code].id, entrepot.id, jour=J(jours, h).date())
                q = min(q, dispo)
                q = int(q) if P[code].unite != "kg" else round(q, 1)
                if q > 0:
                    ok.append({"produit_id": P[code].id, "quantite": q})
            if not ok:
                return None
            cpt = {"especes": caisse, "wave": wave, "orange_money": om, "virement": banque}[mode]
            v = ventes.creer_vente(c.id, entrepot.id, ok, user, J(jours, h), paiement={"montant": 0, "mode": "credit"})
            montant = round(v.total_ttc * part_payee / 100) * 100 if part_payee < 1 else v.total_ttc
            if montant > 0:
                finance.encaisser(c, montant, cpt, mode, J(jours, h), allocations={v.id: montant})
            return v

        for jours, ci, q, part in [(38, 0, 250, .5), (36, 1, 150, 1), (34, 2, 120, 1), (31, 0, 200, 1), (29, 2, 150, .4),
                                   (27, 1, 120, 1), (24, 0, 250, 1), (22, 3, 60, 1), (20, 1, 150, .6), (17, 5, 50, 1),
                                   (15, 0, 200, 1), (12, 2, 120, 0), (10, 0, 220, .7), (8, 4, 40, 1), (6, 1, 160, 1),
                                   (3, 5, 50, 1), (1, 0, 150, .3)]:
            mode = rnd.choice(["especes", "virement", "wave"])
            ev(J(jours, 10), (lambda jours=jours, ci=ci, q=q, part=part, mode=mode:
                              vendre(clients[ci], [("PV", q)], jours, ferme, part, mode, 10)), 3)
        for jours in range(38, -1, -1):
            ev(J(jours, 20), (lambda jours=jours: stock.declarer_perimes(J(jours, 20))), 9)
        for jours in range(36, -1, -1):
            for _pro in range(2):
                c = rnd.choice(clients[6:16])
                lignes = rnd.sample([("BL", rnd.uniform(6, 14)), ("CU", rnd.uniform(8, 16)), ("PI", rnd.uniform(5, 10)),
                                     ("AI", rnd.uniform(4, 9)), ("PE", rnd.randint(5, 12)), ("CA", rnd.uniform(10, 20)),
                                     ("AB", rnd.uniform(4, 8)), ("FO", rnd.uniform(1, 3)), ("GE", rnd.uniform(1, 3))], 4)
                part = rnd.choice([1, 1, .5, 0, .3])
                mode = rnd.choice(["virement", "wave", "orange_money", "especes"])
                ev(J(jours, 11), (lambda c=c, lignes=lignes, jours=jours, part=part, mode=mode:
                                  vendre(c, lignes, jours, cf, part, mode, 11)), 4)
            for _ in range(rnd.randint(4, 7)):
                c = rnd.choice(clients[16:])
                lignes = rnd.sample([("BL", rnd.uniform(.5, 2)), ("CU", rnd.uniform(1, 3)), ("PI", rnd.uniform(.5, 2)),
                                     ("PE", rnd.randint(1, 2)), ("AI", rnd.uniform(.5, 1.5)), ("GE", rnd.uniform(.3, 1)),
                                     ("FO", rnd.uniform(.3, 1))], 2)
                h = rnd.randint(9, 19)
                mode = rnd.choice(["especes", "especes", "wave", "orange_money"])
                ev(J(jours, h), (lambda c=c, lignes=lignes, jours=jours, mode=mode, h=h:
                                 vendre(c, lignes, jours, pdv, 1, mode, h)), 4)

        # Grossistes et boucheries : carcasses, dos et abats en vrac tous les 2 jours
        for jours in range(36, -1, -2):
            c = rnd.choice([clients[0], clients[2], clients[14], clients[15]])
            ev(J(jours, 12), (lambda c=c, jours=jours: vendre(c, [("CA", 60), ("AB", 25), ("DO", 20), ("AI", 15)], jours, cf,
                                                             rnd.choice([1, .5, 0]), "especes", 12)), 4)

        # Congélation des invendus frais la veille de leur péremption (prolonge la conservation)
        def congeler(jours):
            limite = J(jours, 19).date() + timedelta(days=1)
            for lot in Lot.query.filter(Lot.entrepot_id.in_([cf.id, pdv.id]), Lot.etat == "frais", Lot.qte_restante > 0.05,
                                        Lot.date_peremption.isnot(None), Lot.date_peremption <= limite).all():
                if lot.date_peremption < J(jours, 19).date():
                    continue
                stock.changer_etat(lot.produit_id, lot.entrepot_id, lot.qte_restante, "congele", "Congélation des invendus", J(jours, 19), lot.id)
        for jours in range(38, -1, -1):
            ev(J(jours, 19), (lambda jours=jours: congeler(jours)), 8)

        # ── Inventaire avec écart ──
        def _inventaire():
            inv = stock.creer_inventaire(cf.id, J(8, 18), "Inventaire hebdomadaire chambre froide")
            compt = {}
            for l in inv.lignes:
                if l.produit.code in ("CU", "AI") and l.qte_theorique > 2:
                    compt[l.id] = round(l.qte_theorique - rnd.uniform(.5, 1.5), 1)
            stock.valider_inventaire(inv, compt, J(8, 18))
        ev(J(8, 18), _inventaire, 6)

        # ── Dépenses, avances et salaires ──
        for jours, cat, montant, cpt, desc in [
            (35, "Loyer", 250_000, banque, "Loyer point de vente Dakar"), (5, "Loyer", 250_000, banque, "Loyer point de vente Dakar"),
            (30, "Électricité", 185_000, banque, "Facture Senelec – chambre froide"),
            (2, "Électricité", 196_000, wave, "Facture Senelec – chambre froide"),
            (28, "Eau", 32_000, wave, "Facture SEN'EAU"), (1, "Eau", 29_500, wave, "Facture SEN'EAU"),
            (25, "Carburant", 60_000, caisse, "Gasoil livraisons"), (11, "Carburant", 55_000, caisse, "Gasoil livraisons"),
            (3, "Carburant", 45_000, caisse, "Gasoil livraisons"), (19, "Maintenance", 75_000, caisse, "Réparation plumeuse"),
            (16, "Entretien des chambres froides", 90_000, banque, "Recharge gaz frigorifique"),
            (8, "Transport", 35_000, om, "Location camion – livraison Saly"),
            (6, "Autres charges", 15_000, caisse, "Produits d'hygiène"),
            (27, "Investissement – Matériel", 450_000, banque, "Balance électronique 150 kg + scelleuse"),
        ]:
            ev(J(jours, 12), (lambda jours=jours, cat=cat, montant=montant, cpt=cpt, desc=desc:
                              finance.creer_depense(finance.categorie(cat), montant, cpt,
                                                    "especes" if cpt == caisse else "virement", J(jours, 12), desc)), 7)
        mois_prec = (today.replace(day=1) - timedelta(days=1))
        for e in emps:
            if e.salaire_base >= 150_000:
                ev(J(18, 17), (lambda e=e: rh.avance(e, 25_000, caisse, J(18, 17))), 7)
        d_paie = datetime.combine(mois_prec.replace(day=min(mois_prec.day, 30)), datetime.min.time()).replace(hour=17)
        for e in emps:
            ev(d_paie, (lambda e=e: rh.payer_salaire(e, mois_prec.year, mois_prec.month, banque,
                                                     primes=10_000 if e.poste.startswith("Agent") else 0, d=d_paie)), 7)

        for d, _, _, fn in sorted(EV, key=lambda x: (x[0], x[1], x[2])):
            fn()

        # ── Commandes : une confirmée (stock réservé), une en attente, une livrée ──
        c1 = ventes.creer_commande(clients[10].id, cf.id, [{"produit_id": P["BL"].id, "quantite": 8},
                                                          {"produit_id": P["CU"].id, "quantite": 10}], J(1),
                                   (today + timedelta(days=2)).date(), frais_livraison=10_000)
        try:
            ventes.confirmer_commande(c1)
        except Exception:
            pass
        ventes.creer_commande(clients[12].id, cf.id, [{"produit_id": P["PE"].id, "quantite": 10}], J(0, 8),
                              (today + timedelta(days=3)).date())
        c3 = ventes.creer_commande(clients[6].id, cf.id, [{"produit_id": P["PI"].id, "quantite": 4},
                                                         {"produit_id": P["AI"].id, "quantite": 3}], J(9),
                                   (today - timedelta(days=8)).date(), frais_livraison=5_000)
        try:
            ventes.confirmer_commande(c3)
            ventes.passer_en_preparation(c3, livreurs[0].id)
            ventes.livrer_commande(c3, user, {"montant": 0, "mode": "credit"}, J(8, 11))
        except Exception:
            pass

        # ── Un retour client (avoir) ──
        from .models import Vente
        v = Vente.query.filter(Vente.canal == "gros", Vente.statut == "valide", Vente.entrepot_id == cf.id).order_by(
            Vente.date.desc()).first()
        if v and v.lignes:
            ventes.creer_avoir(v, v.lignes[0], round(min(1, v.lignes[0].quantite / 2), 1), "Produit abîmé à la livraison",
                               remise_en_stock=True, etat="endommage")
        db.session.commit()
    set_param("demo_chargee", "1")
    db.session.commit()
