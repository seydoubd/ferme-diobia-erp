"""Rapports filtrables par période, exportables en PDF et Excel."""
import io
from collections import defaultdict
from datetime import datetime, date
from sqlalchemy import func
from ..extensions import db
from ..models import (Vente, Achat, Transformation, MouvementStock, MouvementTresorerie, CompteTresorerie, Produit,
                      Paiement)
from ..utils import fcfa, nombre, date_fr, get_param
from . import analytics, stock, finance, compta

RAPPORTS = {
    "ventes_quotidien": {"titre": "Rapport quotidien des ventes", "icone": "cart"},
    "ventes_mensuel": {"titre": "Rapport mensuel des ventes", "icone": "chart"},
    "stock": {"titre": "Rapport de stock valorisé", "icone": "box"},
    "pertes": {"titre": "Rapport des pertes et mortalités", "icone": "alert", "finances": True},
    "transformation": {"titre": "Rapport de transformation", "icone": "knife"},
    "rentabilite_produit": {"titre": "Rentabilité par produit", "icone": "trend", "finances": True},
    "creances": {"titre": "Rapport des créances clients", "icone": "users"},
    "dettes": {"titre": "Rapport des dettes fournisseurs", "icone": "factory"},
    "caisse": {"titre": "Rapport de caisse et trésorerie", "icone": "wallet", "finances": True},
    "financier": {"titre": "Rapport financier", "icone": "book", "finances": True},
    "achats": {"titre": "Rapport des achats", "icone": "bag"},
}


def _dt(debut, fin):
    return datetime.combine(debut, datetime.min.time()), datetime.combine(fin, datetime.max.time())


def generer(code, debut, fin):
    d0, d1 = _dt(debut, fin)
    rep = {"titre": RAPPORTS[code]["titre"], "periode": f"Du {date_fr(debut)} au {date_fr(fin)}", "colonnes": [],
           "lignes": [], "total": None, "notes": [], "blocs": []}
    C = lambda *cols: [{"label": l, "type": t} for l, t in cols]

    if code == "ventes_quotidien":
        jours = defaultdict(lambda: {"nb": 0, "ca": 0, "tva": 0, "paye_imm": 0, "cout": 0, "gros": 0, "detail": 0})
        for v in Vente.query.filter(Vente.statut == "valide", Vente.date >= d0, Vente.date <= d1).all():
            j = jours[v.date.date()]
            j["nb"] += 1
            j["ca"] += v.total_ht
            j["tva"] += v.tva
            j["cout"] += v.cout_total
            j["gros" if v.canal == "gros" else "detail"] += v.total_ht
        enc = {str(k): v for k, v in db.session.query(func.date(Paiement.date), func.sum(Paiement.montant)).filter(
            Paiement.sens == "encaissement", Paiement.statut == "valide", Paiement.date >= d0, Paiement.date <= d1)
            .group_by(func.date(Paiement.date)).all()}
        rep["colonnes"] = C(("Date", "date"), ("Nb ventes", "num"), ("Ventes en gros", "fcfa"), ("Ventes au détail", "fcfa"),
                            ("CA HT", "fcfa"), ("Encaissements du jour", "fcfa"), ("Coût réel", "fcfa"), ("Marge brute", "fcfa"))
        for d in sorted(jours):
            j = jours[d]
            rep["lignes"].append([d, j["nb"], j["gros"], j["detail"], j["ca"], enc.get(str(d), 0) or 0, j["cout"], j["ca"] - j["cout"]])
    elif code == "ventes_mensuel":
        mois = defaultdict(lambda: [0, 0, 0, 0])
        for v in Vente.query.filter(Vente.statut == "valide", Vente.date >= d0, Vente.date <= d1).all():
            m = mois[(v.date.year, v.date.month)]
            m[0] += 1
            m[1] += v.total_ht
            m[2] += v.cout_total
            m[3] += v.solde
        rep["colonnes"] = C(("Mois", "txt"), ("Nb ventes", "num"), ("CA HT", "fcfa"), ("Coût réel", "fcfa"),
                            ("Marge brute", "fcfa"), ("Taux de marge", "pct"), ("Reste à encaisser", "fcfa"))
        for (a, mm), (n, ca, c, du) in sorted(mois.items()):
            rep["lignes"].append([f"{mm:02d}/{a}", n, ca, c, ca - c, (ca - c) / ca * 100 if ca else 0, du])
        rep["blocs"].append({"titre": "Par produit", "colonnes": C(("Produit", "txt"), ("Quantité", "num"), ("CA HT", "fcfa"), ("Marge", "fcfa")),
                             "lignes": [[r["cle"], f"{nombre(r['quantite'])} {r['unite']}", r["ca"], r["marge"]]
                                        for r in analytics.par_dimension("produit", debut, fin)]})
        rep["blocs"].append({"titre": "Par canal", "colonnes": C(("Canal", "txt"), ("CA HT", "fcfa"), ("Marge", "fcfa"), ("Taux", "pct")),
                             "lignes": [[r["cle"], r["ca"], r["marge"], r["taux"]] for r in analytics.par_dimension("canal", debut, fin)]})
    elif code == "stock":
        rep["periode"] = f"Situation au {date_fr(date.today())}"
        rep["colonnes"] = C(("Produit", "txt"), ("Entrepôt", "txt"), ("Frais", "num"), ("Congelé", "num"), ("Endommagé", "num"),
                            ("Réservé", "num"), ("Disponible", "num"), ("Unité", "txt"), ("Valeur (coût réel)", "fcfa"))
        for r in stock.etat_stock():
            rep["lignes"].append([r["produit"].nom, r["entrepot"].nom, r["frais"], r["congele"], r["endommage"], r["reserve"],
                                  r["disponible"], r["produit"].unite, r["valeur"]])
        rep["blocs"].append({"titre": "Rapprochement stock physique / comptable",
                             "colonnes": C(("Compte", "txt"), ("Libellé", "txt"), ("Valeur physique (lots)", "fcfa"),
                                           ("Solde comptable", "fcfa"), ("Écart", "fcfa")),
                             "lignes": [[r["compte"], r["libelle"], r["physique"], r["comptable"], r["ecart"]]
                                        for r in stock.rapprochement_stock()]})
    elif code == "pertes":
        lib = stock.TYPES_MVT
        rep["colonnes"] = C(("Date", "date"), ("Type", "txt"), ("Produit", "txt"), ("Entrepôt", "txt"), ("Quantité", "num"),
                            ("Valeur", "fcfa"), ("Motif", "txt"))
        for m in MouvementStock.query.filter(MouvementStock.type_mvt.in_(analytics.TYPES_PERTES), MouvementStock.date >= d0,
                                             MouvementStock.date <= d1).order_by(MouvementStock.date).all():
            rep["lignes"].append([m.date, lib.get(m.type_mvt, m.type_mvt), m.produit.nom, m.entrepot.nom, -m.quantite,
                                  -m.valeur, m.note or ""])
        trf = Transformation.query.filter(Transformation.statut == "terminee", Transformation.date_fin >= d0,
                                          Transformation.date_fin <= d1).all()
        rep["notes"].append(f"Pertes de poids en transformation (déchets, sang, plumes) : {nombre(sum(t.pertes_kg for t in trf))} kg, "
                            f"valeur matière correspondante {fcfa(sum(t.valeur_pertes for t in trf))} — déjà incluse dans le coût de revient des produits.")
    elif code == "transformation":
        rep["colonnes"] = C(("N°", "txt"), ("Date", "date"), ("Poulets", "num"), ("Poids vif (kg)", "num"), ("Poids obtenu (kg)", "num"),
                            ("Rendement", "pct"), ("Coût matière", "fcfa"), ("Frais", "fcfa"), ("Coût total", "fcfa"),
                            ("Coût / kg obtenu", "fcfa"), ("Méthode", "txt"))
        for t in Transformation.query.filter(Transformation.statut == "terminee", Transformation.date >= d0,
                                             Transformation.date <= d1).order_by(Transformation.date).all():
            rep["lignes"].append([t.numero, t.date, t.nb_poulets, t.poids_vif_total, t.poids_obtenu, t.rendement_pct,
                                  t.cout_matiere, t.frais_total, t.cout_total, t.cout_total / t.poids_obtenu if t.poids_obtenu else 0,
                                  t.methode_repartition])
        agg = defaultdict(lambda: [0, 0])
        for t in Transformation.query.filter(Transformation.statut == "terminee", Transformation.date >= d0, Transformation.date <= d1).all():
            for tp in t.produits:
                agg[tp.produit.nom][0] += tp.quantite
                agg[tp.produit.nom][1] += tp.cout_alloue
        rep["blocs"].append({"titre": "Produits obtenus et coût de revient moyen",
                             "colonnes": C(("Produit", "txt"), ("Quantité obtenue", "num"), ("Coût alloué", "fcfa"), ("Coût unitaire moyen", "fcfa")),
                             "lignes": [[k, q, c, c / q if q else 0] for k, (q, c) in sorted(agg.items())]})
    elif code == "rentabilite_produit":
        rep["colonnes"] = C(("Produit", "txt"), ("Quantité vendue", "txt"), ("CA HT", "fcfa"), ("Coût réel (FIFO)", "fcfa"),
                            ("Marge brute", "fcfa"), ("Taux de marge", "pct"))
        for r in analytics.par_dimension("produit", debut, fin):
            rep["lignes"].append([r["cle"], f"{nombre(r['quantite'])} {r['unite']}", r["ca"], r["cout"], r["marge"], r["taux"]])
        rep["blocs"].append({"titre": "Comparaison des deux circuits", "colonnes": C(("Circuit", "txt"), ("CA HT", "fcfa"),
                             ("Coût réel", "fcfa"), ("Pertes", "fcfa"), ("Marge nette de pertes", "fcfa")),
                             "lignes": [[r["cle"], r["ca"], r["cout"], r["pertes"], r["marge_nette"]] for r in analytics.circuits(debut, fin)]})
    elif code == "creances":
        rep["periode"] = f"Situation au {date_fr(date.today())}"
        rep["colonnes"] = C(("Client", "txt"), ("Type", "txt"), ("Téléphone", "txt"), ("Total facturé", "fcfa"), ("Encaissé", "fcfa"),
                            ("Reste dû", "fcfa"), ("Dont échu", "fcfa"), ("Plus ancienne échéance", "date"))
        for r in finance.creances_clients():
            if r["du"] > 0.5:
                c = r["client"]
                rep["lignes"].append([c.nom, c.type_client, c.telephone or "", r["total"], r["encaisse"], r["du"], r["retard"], r["echeance"]])
    elif code == "dettes":
        rep["periode"] = f"Situation au {date_fr(date.today())}"
        rep["colonnes"] = C(("Fournisseur", "txt"), ("Total acheté", "fcfa"), ("Payé", "fcfa"), ("Reste à payer", "fcfa"),
                            ("Dont échu", "fcfa"), ("Prochaine échéance", "date"))
        for r in finance.dettes_fournisseurs():
            rep["lignes"].append([r["fournisseur"].nom, r["total"], r["paye"], r["du"], r["retard"], r["echeance"]])
    elif code == "caisse":
        rep["colonnes"] = C(("Date", "date"), ("Compte", "txt"), ("Libellé", "txt"), ("Entrée", "fcfa"), ("Sortie", "fcfa"))
        for m in MouvementTresorerie.query.filter(MouvementTresorerie.date >= d0, MouvementTresorerie.date <= d1).order_by(
                MouvementTresorerie.date).all():
            rep["lignes"].append([m.date, m.compte.nom, m.libelle, m.montant if m.montant > 0 else 0, -m.montant if m.montant < 0 else 0])
        rep["blocs"].append({"titre": "Soldes par compte", "colonnes": C(("Compte", "txt"), ("Solde au début", "fcfa"), ("Solde à la fin", "fcfa")),
                             "lignes": [[c.nom, finance.solde(c, datetime.combine(debut, datetime.min.time())), finance.solde(c, d1)]
                                        for c in CompteTresorerie.query.all()]})
    elif code == "financier":
        r = analytics.resultat(debut, fin)
        rep["colonnes"] = C(("Indicateur", "txt"), ("Montant", "fcfa"))
        rep["lignes"] = [["Chiffre d'affaires HT (facturé)", r["ca"]], ["Coût réel des marchandises vendues", -r["cmv"]],
                         ["Pertes et mortalités valorisées", -r["pertes"]], ["MARGE BRUTE", r["marge_brute"]]]
        for k, v in sorted(r["charges"].items()):
            rep["lignes"].append([f"   {k}", -v])
        rep["lignes"] += [["Total charges opérationnelles", -r["total_charges"]], ["RÉSULTAT D'EXPLOITATION", r["resultat_exploitation"]],
                          ["Impôt sur les sociétés estimé", -r["impot_estime"]], ["RÉSULTAT NET ESTIMÉ", r["resultat_net"]],
                          ["—", None], ["Encaissements de la période (≠ CA)", r["encaissements"]],
                          ["Investissements (hors charges)", r["investissements"]],
                          ["Trésorerie disponible aujourd'hui", finance.tresorerie_totale()],
                          ["Créances clients", sum(x["du"] for x in finance.creances_clients())],
                          ["Dettes fournisseurs", sum(x["du"] for x in finance.dettes_fournisseurs())]]
        cr = compta.compte_resultat(debut, fin)
        rep["blocs"].append({"titre": "Compte de résultat comptable (SYSCOHADA)", "colonnes": C(("Réf.", "txt"), ("Poste", "txt"), ("Montant", "fcfa")),
                             "lignes": [[a, b, c] for a, b, c in cr["lignes"]]})
        rep["notes"].append("Le résultat de gestion et le résultat comptable coïncident lorsque toutes les opérations "
                            "de la période sont enregistrées ; les écarts proviennent des stocks initiaux saisis et des écritures manuelles.")
    elif code == "achats":
        rep["colonnes"] = C(("N°", "txt"), ("Date réception", "date"), ("Fournisseur", "txt"), ("Articles", "txt"),
                            ("Marchandises", "fcfa"), ("Frais d'approche", "fcfa"), ("Total dû", "fcfa"), ("Payé", "fcfa"), ("Reste", "fcfa"))
        for a in Achat.query.filter(Achat.statut == "recu", Achat.date_reception >= d0, Achat.date_reception <= d1).order_by(Achat.date_reception).all():
            art = ", ".join(f"{nombre(l.quantite)} {l.produit.unite} {l.produit.nom}" for l in a.lignes)
            rep["lignes"].append([a.numero, a.date_reception, a.fournisseur.nom, art, a.total_marchandises,
                                  (a.frais_transport or 0) + (a.autres_frais or 0), a.total_ttc, a.montant_paye, a.solde])
    # Totaux automatiques sur les colonnes monétaires / numériques
    if rep["lignes"] and code not in ("financier",):
        tot = []
        for i, c in enumerate(rep["colonnes"]):
            if c["type"] in ("fcfa",) or (c["type"] == "num" and code not in ("stock", "transformation")):
                tot.append(sum((l[i] or 0) for l in rep["lignes"] if isinstance(l[i], (int, float))))
            else:
                tot.append("")
        tot[0] = "TOTAL"
        rep["total"] = tot
    return rep


def fmt(v, t):
    if v is None or v == "":
        return ""
    if t == "fcfa":
        return fcfa(v)
    if t == "num":
        return nombre(v)
    if t == "pct":
        return f"{nombre(v, 1)} %"
    if t == "date":
        return date_fr(v)
    return str(v)


def to_xlsx(rep):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    wb = Workbook()
    ws = wb.active
    ws.title = rep["titre"][:30]
    vert = PatternFill("solid", fgColor="2E7D32")
    pale = PatternFill("solid", fgColor="E8F5E9")
    ws.append([get_param("nom_entreprise", "La Ferme Diobia")])
    ws["A1"].font = Font(bold=True, size=14, color="2E7D32")
    ws.append([rep["titre"]])
    ws["A2"].font = Font(bold=True, size=12, color="E07B00")
    ws.append([rep["periode"]])
    ws.append([])

    def table(cols, lignes, total=None):
        ws.append([c["label"] for c in cols])
        r = ws.max_row
        for i in range(len(cols)):
            cell = ws.cell(row=r, column=i + 1)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = vert
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for l in lignes:
            row = []
            for v, c in zip(l, cols):
                if c["type"] == "date" and hasattr(v, "strftime"):
                    v = v.date() if isinstance(v, datetime) else v
                row.append(v)
            ws.append(row)
            rr = ws.max_row
            for i, c in enumerate(cols):
                cell = ws.cell(row=rr, column=i + 1)
                if c["type"] == "fcfa":
                    cell.number_format = '#,##0 "FCFA"'
                elif c["type"] == "num":
                    cell.number_format = "#,##0.###"
                elif c["type"] == "pct":
                    cell.number_format = '0.0" %"'
                elif c["type"] == "date":
                    cell.number_format = "DD/MM/YYYY"
        if total:
            ws.append(total)
            rr = ws.max_row
            for i in range(len(cols)):
                cell = ws.cell(row=rr, column=i + 1)
                cell.font = Font(bold=True)
                cell.fill = pale
                if cols[i]["type"] == "fcfa":
                    cell.number_format = '#,##0 "FCFA"'
        ws.append([])

    table(rep["colonnes"], rep["lignes"], rep["total"])
    for b in rep["blocs"]:
        ws.append([b["titre"]])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, color="2E7D32")
        table(b["colonnes"], b["lignes"])
    for n in rep["notes"]:
        ws.append([n])
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None and c.row > 4), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 12), 45)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def to_pdf(rep):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    buf = io.BytesIO()
    large = len(rep["colonnes"]) > 6
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4) if large else A4, leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.2 * cm, bottomMargin=1.2 * cm, title=rep["titre"])
    ss = getSampleStyleSheet()
    vert, orange = colors.HexColor("#2E7D32"), colors.HexColor("#E07B00")
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=vert, fontSize=15, alignment=0, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=orange, fontSize=11.5, spaceBefore=4, spaceAfter=2)
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=7.6, leading=9.5)
    note = ParagraphStyle("n", parent=ss["Normal"], fontSize=8, textColor=colors.HexColor("#555555"))
    els = [Paragraph(get_param("nom_entreprise", "La Ferme Diobia"), h1), Paragraph(rep["titre"], h2),
           Paragraph(rep["periode"], note), Spacer(1, 8)]

    def fp(v, t):
        return fmt(v, t).replace("\u202f", " ")

    def table(cols, lignes, total=None):
        data = [[Paragraph(f"<b>{c['label']}</b>", ParagraphStyle("th", parent=small, textColor=colors.white)) for c in cols]]
        for l in lignes:
            data.append([Paragraph(fp(v, c["type"]), small) if c["type"] == "txt" else fp(v, c["type"]) for v, c in zip(l, cols)])
        if total:
            data.append([fp(v, c["type"]) if v != "TOTAL" else "TOTAL" for v, c in zip(total, cols)])
        t = Table(data, repeatRows=1)
        st = [("BACKGROUND", (0, 0), (-1, 0), vert), ("FONTSIZE", (0, 0), (-1, -1), 7.6),
              ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3FAF3")]),
              ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D5DED5")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
        for i, c in enumerate(cols):
            if c["type"] in ("fcfa", "num", "pct"):
                st.append(("ALIGN", (i, 1), (i, -1), "RIGHT"))
        if total:
            st += [("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E8F5E9")), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]
        t.setStyle(TableStyle(st))
        els.append(t)
        els.append(Spacer(1, 10))

    if rep["lignes"]:
        table(rep["colonnes"], rep["lignes"], rep["total"])
    else:
        els.append(Paragraph("Aucune donnée sur la période.", note))
    for b in rep["blocs"]:
        els.append(Paragraph(b["titre"], h2))
        if b["lignes"]:
            table(b["colonnes"], b["lignes"])
    for n in rep["notes"]:
        els.append(Paragraph(n, note))
    els.append(Spacer(1, 6))
    els.append(Paragraph(f"Édité le {datetime.now().strftime('%d/%m/%Y à %H:%M')}", note))
    doc.build(els)
    buf.seek(0)
    return buf
