"""PDF and XLSX export for bons de commande.

Generates a PDF matching the paper bon de commande format,
with attached receipt images as subsequent pages.
"""
import io
import os
from decimal import Decimal

from django.conf import settings

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch, cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, Image as RLImage,
    PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT


COOP_NAME = "COOPÉRATIVE D'HABITATION\nDES CANTONS DE L'EST"
COOP_ADDRESS = "548, rue Dufferin, Sherbrooke (Québec) J1H 4N1"
COOP_PHONE = "Tél. : (819) 566-6303  Téléc. : (819) 829-1593"
COOP_EMAIL = "Courriel : chce@reseaucoop.com  www.chce.coop"


def _fmt_money(val):
    """Format a Decimal as '0.00' or '' if None."""
    if val is None:
        return ""
    return f"{val:.2f} $"


def generate_bon_pdf(bon) -> bytes:
    """Generate a PDF for a BonDeCommande matching the paper form layout."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
    )

    styles = getSampleStyleSheet()
    style_normal = styles["Normal"]
    style_title = ParagraphStyle(
        "BonTitle", parent=styles["Heading2"],
        alignment=TA_CENTER, fontSize=14, spaceAfter=6,
    )
    style_small = ParagraphStyle(
        "Small", parent=style_normal, fontSize=8, leading=10,
    )
    style_center = ParagraphStyle(
        "Center", parent=style_normal, alignment=TA_CENTER, fontSize=9,
    )
    style_right = ParagraphStyle(
        "Right", parent=style_normal, alignment=TA_RIGHT, fontSize=10,
    )
    style_bold = ParagraphStyle(
        "Bold", parent=style_normal, fontSize=10,
        fontName="Helvetica-Bold",
    )

    elements = []

    # ── Header section ───────────────────────────────────────────────────
    header_left = Paragraph(
        f"<b>{COOP_NAME}</b><br/>"
        f"<font size='7'>{COOP_ADDRESS}<br/>"
        f"{COOP_PHONE}<br/>"
        f"{COOP_EMAIL}</font>",
        style_normal,
    )
    header_right = Paragraph(
        f"<font size='8'>Notre numéro de commande</font><br/>"
        f"<b><font size='16'>No {bon.number}</font></b><br/><br/>"
        f"<font size='9'>Date : <b>{bon.purchase_date.strftime('%Y-%m-%d') if bon.purchase_date else '____'}</b></font><br/>"
        f"<font size='9'>Maison : <b>{bon.house.code if bon.house else '____'}</b></font>",
        style_normal,
    )
    header_table = Table(
        [[header_left, header_right]],
        colWidths=[10 * cm, 8 * cm],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 0.5 * cm))

    # ── Title ────────────────────────────────────────────────────────────
    elements.append(Paragraph("<b>BON DE COMMANDE</b>", style_title))
    elements.append(Spacer(1, 0.3 * cm))

    # ── Supplier / reimburse section ─────────────────────────────────────
    purchaser_name = bon.purchaser_name_snapshot or (
        bon.purchaser_member.display_name if bon.purchaser_member else ""
    )
    purchaser_apt = bon.purchaser_unit_snapshot or (
        bon.purchaser_apartment.code if bon.purchaser_apartment else ""
    )
    merchant = bon.merchant_name or ""

    info_data = [
        [
            Paragraph("<font size='8'>Fournisseur / personne à rembourser :</font>", style_normal),
            Paragraph("<font size='8'>Emplacement des travaux ou de livraison</font>", style_normal),
        ],
        [
            Paragraph(f"<font size='9'>Nom : <b>{merchant or purchaser_name}</b></font>", style_normal),
            Paragraph(f"<font size='9'>Maison : <b>{bon.house.code if bon.house else ''}</b></font>", style_normal),
        ],
        [
            Paragraph(f"<font size='9'>Adresse : </font>", style_normal),
            Paragraph(f"<font size='9'>Logement(s) : <b>{purchaser_apt}</b></font>", style_normal),
        ],
    ]
    info_table = Table(info_data, colWidths=[10 * cm, 8 * cm])
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.grey),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 0.3 * cm))

    # ── Warning text ─────────────────────────────────────────────────────
    elements.append(Paragraph(
        "<font size='7'><i>Si la personne à rembourser n'est pas un fournisseur, "
        "une signature d'autorisation d'un autre membre est demandée.</i></font>",
        style_center,
    ))
    elements.append(Paragraph(
        "<font size='9'><b>Montant maximum autorisé : 500 $</b></font>",
        style_center,
    ))
    elements.append(Spacer(1, 0.3 * cm))

    # ── Items table ──────────────────────────────────────────────────────
    # Build items from receipts
    items_header = [
        Paragraph("<b><font color='white'>Qté</font></b>", style_center),
        Paragraph("<b><font color='white'>Description</font></b>", style_center),
        Paragraph("<b><font color='white'>Sous-budget</font></b>", style_center),
        Paragraph("<b><font color='white'>Total</font></b>", style_center),
    ]
    items_data = [items_header]

    receipts = bon.receipt_files.order_by("created_at", "pk")
    for r in receipts:
        try:
            ef = r.extracted_fields
            desc = ef.final_merchant or ef.merchant_candidate or r.original_filename
            sb_name = ef.sub_budget.name if ef.sub_budget else ""
            total_val = _fmt_money(ef.final_total or ef.total_candidate)
        except Exception:
            desc = r.original_filename
            sb_name = ""
            total_val = ""
        items_data.append([
            Paragraph("1", style_center),
            Paragraph(f"<font size='9'>{desc}</font>", style_normal),
            Paragraph(f"<font size='9'>{sb_name}</font>", style_normal),
            Paragraph(f"<font size='9'>{total_val}</font>", style_right),
        ])

    # Pad to at least 5 rows
    while len(items_data) < 6:
        items_data.append(["", "", "", ""])

    items_table = Table(
        items_data,
        colWidths=[1.5 * cm, 8 * cm, 5 * cm, 3.5 * cm],
    )
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 0.3 * cm))

    # ── Totals section ───────────────────────────────────────────────────
    totals_data = []
    if bon.subtotal:
        totals_data.append(["Sous-total :", _fmt_money(bon.subtotal)])
    if bon.tps:
        totals_data.append(["TPS :", _fmt_money(bon.tps)])
    if bon.tvq:
        totals_data.append(["TVQ :", _fmt_money(bon.tvq)])
    totals_data.append([
        Paragraph("<b>TOTAL</b>", style_right),
        Paragraph(f"<b>{_fmt_money(bon.total)}</b>", style_right),
    ])

    totals_table = Table(
        totals_data,
        colWidths=[14.5 * cm, 3.5 * cm],
    )
    totals_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (0, -1), "RIGHT"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (1, -1), (1, -1), 1, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BOX", (1, -1), (1, -1), 1, colors.black),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 0.5 * cm))

    # ── Signature section ────────────────────────────────────────────────
    sig_data = [
        [
            Paragraph(f"<font size='9'><b>{purchaser_name}</b></font>", style_normal),
            "",
        ],
        [
            Paragraph("<font size='7'>NOM EN LETTRES MOULÉES</font>", style_normal),
            Paragraph("<font size='7'>SIGNATURE</font>", style_normal),
        ],
        ["", ""],
        [
            Paragraph("<font size='7'>NOM EN LETTRES MOULÉES (autorisation)</font>", style_normal),
            Paragraph("<font size='7'>SIGNATURE</font>", style_normal),
        ],
    ]
    sig_table = Table(sig_data, colWidths=[9 * cm, 9 * cm])
    sig_table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (0, 0), 0.5, colors.black),
        ("LINEBELOW", (1, 0), (1, 0), 0.5, colors.black),
        ("LINEBELOW", (0, 2), (0, 2), 0.5, colors.black),
        ("LINEBELOW", (1, 2), (1, 2), 0.5, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(sig_table)
    elements.append(Spacer(1, 0.3 * cm))

    # ── Footer ───────────────────────────────────────────────────────────
    footer_data = [[
        Paragraph("<font size='7'>Copie blanche : Fournisseur</font>", style_center),
        Paragraph("<font size='7'>Copie jaune : Coopérative</font>", style_center),
        Paragraph("<font size='7'>Copie rose : Maison</font>", style_center),
    ]]
    footer_table = Table(footer_data, colWidths=[6 * cm, 6 * cm, 6 * cm])
    footer_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0f0f0")),
    ]))
    elements.append(footer_table)

    # ── Attached receipt images ──────────────────────────────────────────
    for receipt in receipts:
        if not receipt.file or not receipt.content_type:
            continue
        if not receipt.content_type.startswith("image/"):
            continue
        try:
            file_path = receipt.file.path
            if not os.path.exists(file_path):
                continue
            elements.append(PageBreak())
            elements.append(Paragraph(
                f"<b>Reçu : {receipt.original_filename}</b>",
                style_bold,
            ))
            elements.append(Spacer(1, 0.3 * cm))

            # Fit image to page
            max_w = 17 * cm
            max_h = 22 * cm
            img = RLImage(file_path)
            iw, ih = img.imageWidth, img.imageHeight
            ratio = min(max_w / iw, max_h / ih)
            img.drawWidth = iw * ratio
            img.drawHeight = ih * ratio
            elements.append(img)
        except Exception:
            elements.append(Paragraph(
                f"<i>(Image non disponible : {receipt.original_filename})</i>",
                style_small,
            ))

    doc.build(elements)
    return buf.getvalue()


def generate_bon_xlsx(bon) -> bytes:
    """Generate an XLSX file for a BonDeCommande."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = f"BC {bon.number}"

    # Column widths
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 35
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 15

    bold = Font(bold=True)
    title_font = Font(bold=True, size=14)
    header_font = Font(bold=True, color="FFFFFF", size=10)
    header_fill = PatternFill(start_color="333333", end_color="333333", fill_type="solid")
    border_thin = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    money_fmt = '#,##0.00 "$"'

    # Header
    ws.merge_cells("A1:D1")
    ws["A1"] = COOP_NAME.replace("\n", " ")
    ws["A1"].font = bold

    ws.merge_cells("A2:D2")
    ws["A2"] = COOP_ADDRESS

    ws.merge_cells("A4:D4")
    ws["A4"] = "BON DE COMMANDE"
    ws["A4"].font = title_font
    ws["A4"].alignment = Alignment(horizontal="center")

    # Info fields
    row = 6
    info = [
        ("Numéro :", bon.number),
        ("Date :", bon.purchase_date.strftime("%Y-%m-%d") if bon.purchase_date else ""),
        ("Maison :", bon.house.code if bon.house else ""),
        ("Marchand :", bon.merchant_name or ""),
        ("Personne à rembourser :", bon.purchaser_name_snapshot or (
            bon.purchaser_member.display_name if bon.purchaser_member else ""
        )),
        ("Appartement :", bon.purchaser_unit_snapshot or (
            bon.purchaser_apartment.code if bon.purchaser_apartment else ""
        )),
        ("Sous-budget :", bon.sub_budget.name if bon.sub_budget else ""),
    ]
    for label, value in info:
        ws.cell(row=row, column=1, value=label).font = bold
        ws.cell(row=row, column=2, value=value)
        row += 1

    row += 1

    # Items header
    headers = ["Qté", "Description", "Sous-budget", "Total"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border_thin
        cell.alignment = Alignment(horizontal="center")
    row += 1

    # Items from receipts
    receipts = bon.receipt_files.order_by("created_at", "pk")
    for r in receipts:
        try:
            ef = r.extracted_fields
            desc = ef.final_merchant or ef.merchant_candidate or r.original_filename
            sb_name = ef.sub_budget.name if ef.sub_budget else ""
            total_val = float(ef.final_total or ef.total_candidate or 0)
        except Exception:
            desc = r.original_filename
            sb_name = ""
            total_val = 0

        ws.cell(row=row, column=1, value=1).border = border_thin
        ws.cell(row=row, column=1).alignment = Alignment(horizontal="center")
        ws.cell(row=row, column=2, value=desc).border = border_thin
        ws.cell(row=row, column=3, value=sb_name).border = border_thin
        cell = ws.cell(row=row, column=4, value=total_val)
        cell.border = border_thin
        cell.number_format = money_fmt
        cell.alignment = Alignment(horizontal="right")
        row += 1

    row += 1

    # Totals
    total_labels = []
    if bon.subtotal:
        total_labels.append(("Sous-total :", float(bon.subtotal)))
    if bon.tps:
        total_labels.append(("TPS :", float(bon.tps)))
    if bon.tvq:
        total_labels.append(("TVQ :", float(bon.tvq)))
    total_labels.append(("TOTAL :", float(bon.total or 0)))

    for label, val in total_labels:
        ws.cell(row=row, column=3, value=label).font = bold
        ws.cell(row=row, column=3).alignment = Alignment(horizontal="right")
        cell = ws.cell(row=row, column=4, value=val)
        cell.font = bold
        cell.number_format = money_fmt
        cell.alignment = Alignment(horizontal="right")
        if label == "TOTAL :":
            cell.border = Border(
                top=Side(style="double"), bottom=Side(style="double"),
                left=Side(style="thin"), right=Side(style="thin"),
            )
        row += 1

    row += 2

    # Signatures
    ws.cell(row=row, column=1, value="NOM EN LETTRES MOULÉES :").font = bold
    ws.cell(row=row, column=2, value=bon.purchaser_name_snapshot or (
        bon.purchaser_member.display_name if bon.purchaser_member else ""
    ))
    row += 1
    ws.cell(row=row, column=1, value="SIGNATURE :").font = bold
    row += 2
    ws.cell(row=row, column=1, value="AUTORISATION :").font = bold

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
