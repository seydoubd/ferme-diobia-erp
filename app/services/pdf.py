"""Facture PDF (A4) aux couleurs de La Ferme Diobia."""
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from ..utils import fcfa, nombre, date_fr, get_param
from .finance import MODES


def _f(v):
    return fcfa(v).replace(" ", " ")


def facture_pdf(v):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.4 * cm,
                            bottomMargin=1.4 * cm, title=f"Facture {v.numero}")
    ss = getSampleStyleSheet()
    vert, orange = colors.HexColor("#2E7D32"), colors.HexColor("#F7941D")
    n = ParagraphStyle("n", parent=ss["Normal"], fontSize=9, leading=12)
    b = ParagraphStyle("b", parent=n, fontName="Helvetica-Bold")
    titre = ParagraphStyle("t", parent=ss["Title"], textColor=vert, fontSize=18, alignment=0, spaceAfter=0)
    entete = Table([[Paragraph(get_param("nom_entreprise", "La Ferme Diobia"), titre),
                     Paragraph(f"<font color='#E07B00' size='16'><b>{'AVOIR / ' if v.statut == 'annulee' else ''}FACTURE</b></font><br/>"
                               f"<b>{v.numero}</b><br/>Date : {date_fr(v.date)}<br/>Échéance : {date_fr(v.echeance)}", n)],
                    [Paragraph(f"{get_param('slogan', '')}<br/>{get_param('adresse', '')}<br/>Tél. {get_param('telephone', '')}"
                               f"<br/>{get_param('email', '')}"
                               + (f"<br/>NINEA {get_param('ninea')}" if get_param("ninea") else ""), n),
                     Paragraph(f"<b>Facturé à</b><br/>{v.client.nom}<br/>{v.client.adresse or ''}<br/>{v.client.telephone or ''}", n)]],
                   colWidths=[9.5 * cm, 8.3 * cm])
    entete.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (1, 1), (1, 1), colors.HexColor("#FFF3E0")),
                                ("BOX", (1, 1), (1, 1), 0.5, orange), ("LEFTPADDING", (1, 1), (1, 1), 8)]))
    data = [["Désignation", "Quantité", "Prix unitaire", "Remise", "Montant HT"]]
    for l in v.lignes:
        data.append([Paragraph(l.produit.nom, n), f"{nombre(l.quantite)} {l.produit.unite}", _f(l.prix_unitaire),
                     f"{nombre(l.remise_pct)} %" if l.remise_pct else "", _f(l.montant_ht)])
    if v.frais_livraison:
        data.append(["Frais de livraison", "", "", "", _f(v.frais_livraison)])
    t = Table(data, colWidths=[6.8 * cm, 2.6 * cm, 3 * cm, 1.8 * cm, 3.6 * cm], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), vert), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9),
                           ("ALIGN", (1, 1), (-1, -1), "RIGHT"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D5DED5")),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3FAF3")])]))
    rows = []
    if v.remise_globale:
        rows.append(["Remise globale", "- " + _f(v.remise_globale)])
    rows += [["Total HT", _f(v.total_ht)]]
    if v.tva:
        rows.append([f"TVA ({nombre(float(get_param('tva_taux', '18')))} %)", _f(v.tva)])
    rows += [["TOTAL TTC", _f(v.total_ttc)], ["Déjà réglé", _f(v.montant_paye)]]
    if v.montant_avoirs:
        rows.append(["Avoirs", "- " + _f(v.montant_avoirs)])
    rows.append(["RESTE À PAYER", _f(v.solde)])
    tot = Table(rows, colWidths=[4.5 * cm, 3.6 * cm], hAlign="RIGHT")
    st = [("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9.5)]
    for i, r in enumerate(rows):
        if r[0] in ("TOTAL TTC", "RESTE À PAYER"):
            st += [("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"),
                   ("BACKGROUND", (0, i), (-1, i), vert if r[0] == "TOTAL TTC" else colors.HexColor("#FFF3E0")),
                   ("TEXTCOLOR", (0, i), (-1, i), colors.white if r[0] == "TOTAL TTC" else colors.HexColor("#E07B00"))]
    tot.setStyle(TableStyle(st))
    regl = ", ".join(f"{date_fr(a.paiement.date)} {MODES.get(a.paiement.mode, a.paiement.mode)} {_f(a.montant)}"
                     for a in v.allocations if a.paiement.statut == "valide")
    els = [entete, Spacer(1, 14), t, Spacer(1, 10), tot, Spacer(1, 12)]
    if regl:
        els.append(Paragraph(f"<b>Règlements :</b> {regl}", n))
    els += [Spacer(1, 18), Paragraph(get_param("mentions_facture", ""), ParagraphStyle("m", parent=n, textColor=colors.grey))]
    doc.build(els)
    buf.seek(0)
    return buf
