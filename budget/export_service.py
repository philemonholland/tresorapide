"""PDF and XLSX export for the Grille de dépenses (expense ledger).

Generates landscape-oriented exports with narrow margins,
matching the on-screen layout.
"""
import io
from html import escape
from decimal import Decimal

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import inch, cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill, numbers
from openpyxl.utils import get_column_letter

from .services import BudgetCalculationService


COOP_NAME = "Coopérative d'habitation des Cantons de l'Est"


def _fmt(val):
    """Format Decimal as string with 2 decimals, or empty string."""
    if val is None:
        return ""
    return f"{val:,.2f} $".replace(",", "\u00a0")


def _fmt_plain(val):
    """Format a PDF amount in compact French-Canadian notation."""
    if val is None:
        return ""
    whole, decimals = f"{Decimal(val):,.2f}".split(".")
    whole = whole.replace(",", "\u00a0")
    return f"{whole},{decimals} $"


# ──────────────────────────────────────────────────────────────────
#  PDF export
# ──────────────────────────────────────────────────────────────────

def generate_expense_ledger_pdf(budget_year, *, include_cancelled=False):
    """Generate a landscape PDF of the expense ledger. Returns bytes."""
    svc = BudgetCalculationService
    base = svc.base_values(budget_year)
    rows = svc.running_balances(budget_year, include_cancelled=include_cancelled)
    categories = svc.category_summary(budget_year)
    repair = svc.repair_totals(budget_year)
    imprevues = svc.imprevues_totals(budget_year)
    available = svc.available_money(budget_year)

    buf = io.BytesIO()
    page = landscape(letter)
    doc = SimpleDocTemplate(
        buf, pagesize=page,
        leftMargin=0.38 * inch, rightMargin=0.38 * inch,
        topMargin=0.38 * inch, bottomMargin=0.48 * inch,
    )

    styles = getSampleStyleSheet()
    dark_blue = colors.HexColor("#17324D")
    medium_blue = colors.HexColor("#315C7D")
    pale_blue = colors.HexColor("#EAF2F8")
    pale_green = colors.HexColor("#EAF7EF")
    pale_gray = colors.HexColor("#F5F7F9")
    border_gray = colors.HexColor("#B7C3CC")
    text_gray = colors.HexColor("#44515C")

    style_title = ParagraphStyle(
        "LedgerTitle", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=17, leading=20, textColor=dark_blue, alignment=TA_CENTER,
        spaceAfter=3,
    )
    style_subtitle = ParagraphStyle(
        "LedgerSubtitle", parent=styles["Normal"], fontSize=8.5, leading=11,
        textColor=text_gray, alignment=TA_CENTER, spaceAfter=7,
    )
    style_intro = ParagraphStyle(
        "LedgerIntro", parent=styles["Normal"], fontSize=8, leading=10.5,
        textColor=text_gray, alignment=TA_LEFT, spaceAfter=6,
    )
    style_page_title = ParagraphStyle(
        "PageTitle", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=15, leading=18, textColor=dark_blue, alignment=TA_LEFT,
        spaceAfter=3,
    )
    style_panel_title = ParagraphStyle(
        "PanelTitle", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=10.5, leading=13, textColor=dark_blue, spaceAfter=5,
    )
    style_help = ParagraphStyle(
        "Help", parent=styles["Normal"], fontSize=8, leading=10.5,
        textColor=text_gray,
    )
    style_cell = ParagraphStyle(
        "Cell", parent=styles["Normal"], fontSize=7.2, leading=9.2,
    )
    style_cell_r = ParagraphStyle("CellR", parent=style_cell, alignment=TA_RIGHT)
    style_cell_c = ParagraphStyle("CellC", parent=style_cell, alignment=TA_CENTER)
    style_cell_yes = ParagraphStyle(
        "CellYes", parent=style_cell_c, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1E6A43"),
    )
    style_cell_negative = ParagraphStyle(
        "CellNegative", parent=style_cell_r, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#A12A2A"),
    )
    style_header = ParagraphStyle(
        "Header", parent=style_cell, fontName="Helvetica-Bold", fontSize=7.2,
        leading=8.7, textColor=colors.white,
    )
    style_header_c = ParagraphStyle("HeaderC", parent=style_header, alignment=TA_CENTER)
    style_header_r = ParagraphStyle("HeaderR", parent=style_header, alignment=TA_RIGHT)
    style_metric_label = ParagraphStyle(
        "MetricLabel", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=8, leading=10, textColor=medium_blue, alignment=TA_CENTER,
    )
    style_metric_value = ParagraphStyle(
        "MetricValue", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=12, leading=14, textColor=dark_blue, alignment=TA_CENTER,
    )

    elements = []

    house = budget_year.house
    export_date = timezone.localdate()
    elements.append(Paragraph(
        f"Grille de dépenses - {escape(house.code)} - {budget_year.year}", style_title
    ))
    elements.append(Paragraph(COOP_NAME, style_subtitle))
    elements.append(Paragraph(
        "Les dates ci-dessous sont les dates d'achat conservées dans "
        "Tresorapide. Cette page présente toutes les dépenses actives de "
        "l'année, dans l'ordre chronologique.",
        style_intro,
    ))

    metric_labels = [
        "Dépenses enregistrées",
        "Dépenses à ce jour",
        "Argent disponible",
        "Disponible après réserve",
    ]
    metric_values = [
        str(len(rows)),
        _fmt_plain(base["expenses_to_date"]),
        _fmt_plain(available["available"]),
        _fmt_plain(available["available_minus_imprevues"]),
    ]
    metric_table = Table(
        [
            [Paragraph(label, style_metric_label) for label in metric_labels],
            [Paragraph(value, style_metric_value) for value in metric_values],
        ],
        colWidths=[(page[0] - doc.leftMargin - doc.rightMargin) / 4] * 4,
    )
    metric_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), pale_blue),
        ("BACKGROUND", (0, 1), (-1, 1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.6, border_gray),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, border_gray),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 1), (-1, 1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 4),
    ]))
    elements.append(metric_table)
    elements.append(Spacer(1, 8))

    # ── Expense ledger table ─────────────────────────────
    header = [
        Paragraph("Date d'achat", style_header),
        Paragraph("Description", style_header),
        Paragraph("No BC", style_header),
        Paragraph("Au GL", style_header_c),
        Paragraph("Fournisseur", style_header),
        Paragraph("Remboursé à", style_header),
        Paragraph("Dépensé par", style_header),
        Paragraph("Validé par", style_header),
        Paragraph("Montant", style_header_r),
        Paragraph("Trace", style_header_c),
        Paragraph("Solde", style_header_r),
        Paragraph("Solde après réserve", style_header_r),
    ]

    data = [header]
    for r in rows:
        exp = r["expense"]
        desc = exp.description
        if exp.is_cancellation:
            desc += " [ANNULATION]"
        data.append([
            Paragraph(exp.entry_date.strftime("%Y-%m-%d"), style_cell),
            Paragraph(escape(desc), style_cell),
            Paragraph(escape(exp.bon_number or ""), style_cell),
            Paragraph("Oui" if exp.validated_gl else "", style_cell_yes),
            Paragraph(escape(exp.supplier_name or ""), style_cell),
            Paragraph(escape(exp.display_reimburse_label), style_cell),
            Paragraph(escape(exp.display_spent_by_label), style_cell),
            Paragraph(escape(exp.display_approved_by_label), style_cell),
            Paragraph(_fmt_plain(exp.amount), style_cell_r),
            Paragraph(str(exp.sub_budget.trace_code), style_cell_c),
            Paragraph(_fmt_plain(r["balance"]), style_cell_r),
            Paragraph(_fmt_plain(r["balance_minus_imprevues"]), style_cell_r),
        ])

    avail_width = page[0] - doc.leftMargin - doc.rightMargin
    col_widths = [
        0.07 * avail_width,  # Date
        0.165 * avail_width,  # Description
        0.055 * avail_width,  # BC
        0.04 * avail_width,  # GL
        0.105 * avail_width,  # Fournisseur
        0.07 * avail_width,  # Remboursé à
        0.105 * avail_width,  # Dépensé par
        0.105 * avail_width,  # Validé par
        0.075 * avail_width,  # Montant
        0.04 * avail_width,  # Trace
        0.08 * avail_width,  # Balance
        0.09 * avail_width,  # Solde après réserve
    ]

    table = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), dark_blue),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, border_gray),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    # Alternate row colours
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), pale_gray))
        if rows[i - 1]["expense"].validated_gl:
            style_cmds.append(("BACKGROUND", (3, i), (3, i), pale_green))
        # Red text for cancellation rows
        exp = rows[i - 1]["expense"]
        if exp.is_cancellation:
            style_cmds.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor("#c0392b")))
        elif rows[i - 1]["is_cancelled"]:
            style_cmds.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor("#999999")))

    table.setStyle(TableStyle(style_cmds))
    elements.append(table)
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        "<b>Comment lire le tableau :</b> « Au GL : Oui » signifie que la dépense "
        "a été retrouvée dans le Grand Livre. Le « Solde après réserve » montre ce "
        "qui reste disponible après avoir conservé la réserve de 15 % pour les imprévus.",
        style_help,
    ))

    # ── Dedicated summary page ───────────────────────────
    elements.append(PageBreak())
    elements.append(Paragraph("Comprendre le budget", style_page_title))
    elements.append(Paragraph(
        "Prévu = budget attribué. Utilisé = dépenses actives enregistrées. "
        "Restant = montant encore disponible dans la catégorie.",
        style_intro,
    ))

    summary_data = [
        ["Budget d'entretien de la maison", base["budget_total"]],
        ["Budget de déneigement", base["snow_budget"]],
        ["Imprévus (15 %)", base["imprevues"]],
        ["Budget après réserve de 15 %", base["budget_minus_imprevues"]],
        ["Dépenses effectuées à ce jour", base["expenses_to_date"]],
        ["Budget réparations - prévu", repair["planned"]],
        ["Budget réparations - utilisé", repair["used"]],
        ["Budget réparations - restant", repair["remaining"]],
        ["Imprévus utilisés", imprevues["used"]],
        ["Imprévus restants", imprevues["remaining"]],
        ["Argent total disponible", available["available"]],
        ["Disponible après réserve de 15 %", available["available_minus_imprevues"]],
    ]
    summary_rows = [
        [Paragraph(escape(label), style_help), Paragraph(_fmt_plain(value), style_cell_r)]
        for label, value in summary_data
    ]
    left_width = avail_width * 0.36
    right_width = avail_width * 0.64
    summary_tbl = Table(
        summary_rows,
        colWidths=[left_width * 0.68, left_width * 0.27],
    )
    summary_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, border_gray),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BACKGROUND", (0, -2), (-1, -1), pale_blue),
        ("FONTNAME", (0, -2), (-1, -1), "Helvetica-Bold"),
    ]))

    cat_header = [
        Paragraph("Description", style_header),
        Paragraph("Trace", style_header_c),
        Paragraph("Prévu", style_header_r),
        Paragraph("Utilisé", style_header_r),
        Paragraph("Restant", style_header_r),
    ]
    cat_data = [cat_header]
    for category in categories:
        remaining_style = (
            style_cell_negative if category["remaining"] < 0 else style_cell_r
        )
        cat_data.append([
            Paragraph(escape(category["name"]), style_help),
            Paragraph(str(category["trace_code"]), style_cell_c),
            Paragraph(_fmt_plain(category["planned"]), style_cell_r),
            Paragraph(_fmt_plain(category["used"]), style_cell_r),
            Paragraph(_fmt_plain(category["remaining"]), remaining_style),
        ])

    non_cont = [c for c in categories if not c["sub_budget"].is_contingency]
    cat_data.append([
        Paragraph("<b>Total (sans les imprévus)</b>", style_help),
        "",
        Paragraph(_fmt_plain(sum((c["planned"] for c in non_cont), Decimal("0"))), style_cell_r),
        Paragraph(_fmt_plain(sum((c["used"] for c in non_cont), Decimal("0"))), style_cell_r),
        Paragraph(_fmt_plain(sum((c["remaining"] for c in non_cont), Decimal("0"))), style_cell_r),
    ])

    cat_widths = [
        right_width * 0.43,
        right_width * 0.09,
        right_width * 0.16,
        right_width * 0.16,
        right_width * 0.16,
    ]
    cat_tbl = Table(cat_data, colWidths=cat_widths, repeatRows=1)
    cat_style = [
        ("BACKGROUND", (0, 0), (-1, 0), dark_blue),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, border_gray),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
        ("BACKGROUND", (0, -1), (-1, -1), pale_blue),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]
    for i, category in enumerate(categories, 1):
        if i % 2 == 0:
            cat_style.append(("BACKGROUND", (0, i), (-1, i), pale_gray))
        if category["remaining"] < 0:
            cat_style.extend([
                ("BACKGROUND", (4, i), (4, i), colors.HexColor("#FDECEC")),
            ])
    cat_tbl.setStyle(TableStyle(cat_style))

    left_panel = [
        Paragraph("Résumé budgétaire", style_panel_title),
        summary_tbl,
        Spacer(1, 7),
        Paragraph(
            "La réserve de 15 % est mise de côté pour les imprévus. Le montant "
            "« disponible après réserve » est donc le repère le plus prudent.",
            style_help,
        ),
    ]
    right_panel = [
        Paragraph("Sous-budgets", style_panel_title),
        cat_tbl,
        Spacer(1, 6),
        Paragraph(
            "Un montant négatif dans « Restant » signifie que des dépenses ont été "
            "inscrites dans une catégorie sans budget prévu suffisant.",
            style_help,
        ),
    ]
    panels = Table(
        [[left_panel, right_panel]],
        colWidths=[left_width, right_width],
        hAlign="LEFT",
    )
    panels.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 9),
        ("LEFTPADDING", (1, 0), (1, 0), 9),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LINEBEFORE", (1, 0), (1, 0), 0.5, border_gray),
    ]))
    elements.append(panels)

    def draw_footer(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(border_gray)
        canvas.setLineWidth(0.4)
        canvas.line(doc.leftMargin, 0.34 * inch, page[0] - doc.rightMargin, 0.34 * inch)
        canvas.setFont("Helvetica", 6.8)
        canvas.setFillColor(text_gray)
        canvas.drawString(
            doc.leftMargin,
            0.19 * inch,
            f"Généré le {export_date.isoformat()} - dates d'achat Tresorapide",
        )
        canvas.drawRightString(
            page[0] - doc.rightMargin,
            0.19 * inch,
            f"Page {canvas.getPageNumber()}",
        )
        canvas.restoreState()

    doc.build(elements, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────
#  XLSX export
# ──────────────────────────────────────────────────────────────────

_THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)
_HEADER_FILL = PatternFill(start_color="2C3E50", end_color="2C3E50", fill_type="solid")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=9)
_ALT_FILL = PatternFill(start_color="F7F9FB", end_color="F7F9FB", fill_type="solid")
_MONEY_FMT = '#,##0.00 "$"'
_CANCEL_FONT = Font(color="C0392B", size=9)
_CANCELLED_FONT = Font(color="999999", strikethrough=True, size=9)
_TOTAL_FILL = PatternFill(start_color="ECF0F1", end_color="ECF0F1", fill_type="solid")
_BOLD_FONT = Font(bold=True, size=9)


def generate_expense_ledger_xlsx(budget_year, *, include_cancelled=False):
    """Generate landscape Excel workbook of the expense ledger. Returns bytes."""
    svc = BudgetCalculationService
    base = svc.base_values(budget_year)
    rows = svc.running_balances(budget_year, include_cancelled=include_cancelled)
    categories = svc.category_summary(budget_year)
    repair = svc.repair_totals(budget_year)
    imprevues = svc.imprevues_totals(budget_year)
    available = svc.available_money(budget_year)

    wb = openpyxl.Workbook()

    # ── Sheet 1: Grille de dépenses ──────────────────────
    ws = wb.active
    ws.title = "Grille de dépenses"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_LETTER
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins.left = 0.4
    ws.page_margins.right = 0.4
    ws.page_margins.top = 0.4
    ws.page_margins.bottom = 0.4

    house = budget_year.house
    ws.merge_cells("A1:L1")
    title_cell = ws["A1"]
    title_cell.value = f"Grille de dépenses — {house.code} — {budget_year.year}"
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = Alignment(horizontal="center")

    ws.merge_cells("A2:L2")
    ws["A2"].value = COOP_NAME
    ws["A2"].font = Font(size=9, italic=True)
    ws["A2"].alignment = Alignment(horizontal="center")

    # Headers
    headers = [
        "Date", "Description", "# BC", "GL", "Fournisseur", "Remb.",
        "Dépensé par", "Validé par", "Montant", "Trace",
        "Balance", "Balance−15%",
    ]
    header_row = 4
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.border = _THIN_BORDER
        cell.alignment = Alignment(horizontal="center" if col_idx in (4, 10) else
                                   "right" if col_idx >= 9 else "left",
                                   wrap_text=True)

    # Data rows
    for i, r in enumerate(rows):
        row_num = header_row + 1 + i
        exp = r["expense"]
        desc = exp.description
        if exp.is_cancellation:
            desc += " [ANNULATION]"

        values = [
            exp.entry_date,
            desc,
            exp.bon_number or "",
            "✓" if exp.validated_gl else "",
            exp.supplier_name or "",
            exp.display_reimburse_label,
            exp.display_spent_by_label,
            exp.display_approved_by_label,
            float(exp.amount),
            exp.sub_budget.trace_code,
            float(r["balance"]),
            float(r["balance_minus_imprevues"]),
        ]
        for col_idx, v in enumerate(values, 1):
            cell = ws.cell(row=row_num, column=col_idx, value=v)
            cell.border = _THIN_BORDER
            cell.font = Font(size=9)
            if col_idx == 1:
                cell.number_format = "YYYY-MM-DD"
                cell.alignment = Alignment(horizontal="left")
            elif col_idx in (9, 11, 12):
                cell.number_format = _MONEY_FMT
                cell.alignment = Alignment(horizontal="right")
            elif col_idx in (4, 10):
                cell.alignment = Alignment(horizontal="center")

            # Row styling
            if exp.is_cancellation:
                cell.font = _CANCEL_FONT
            elif r["is_cancelled"]:
                cell.font = _CANCELLED_FONT

            if i % 2 == 1:
                cell.fill = _ALT_FILL

    # Column widths
    col_widths = [12, 28, 10, 5, 16, 10, 14, 14, 12, 6, 12, 12]
    for idx, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = w

    # Freeze header row
    ws.freeze_panes = f"A{header_row + 1}"

    # Print area
    last_data_row = header_row + len(rows)
    ws.print_title_rows = f"{header_row}:{header_row}"

    # ── Sheet 2: Résumé budgétaire ───────────────────────
    ws2 = wb.create_sheet("Résumé budgétaire")
    ws2.page_setup.orientation = "landscape"
    ws2.page_setup.paperSize = ws2.PAPERSIZE_LETTER
    ws2.page_margins.left = 0.5
    ws2.page_margins.right = 0.5

    ws2.merge_cells("A1:B1")
    ws2["A1"].value = "Résumé budgétaire"
    ws2["A1"].font = Font(bold=True, size=12)

    summary_items = [
        ("Budget d'entretien de la maison", base["budget_total"]),
        ("Budget de déneigement", base["snow_budget"]),
        ("Imprévus (15 %)", base["imprevues"]),
        ("Budget − 15 %", base["budget_minus_imprevues"]),
        ("Dépenses effectuées à ce jour", base["expenses_to_date"]),
        ("Budget réparations — prévu", repair["planned"]),
        ("Budget réparations — utilisé", repair["used"]),
        ("Budget réparations — restant", repair["remaining"]),
        ("Imprévus utilisés", imprevues["used"]),
        ("Imprévus restants", imprevues["remaining"]),
        ("Argent total disponible", available["available"]),
        ("Argent total disponible − 15 %", available["available_minus_imprevues"]),
    ]
    for i, (label, val) in enumerate(summary_items):
        r = 3 + i
        ws2.cell(row=r, column=1, value=label).font = Font(size=9)
        ws2.cell(row=r, column=1).border = _THIN_BORDER
        c = ws2.cell(row=r, column=2, value=float(val))
        c.number_format = _MONEY_FMT
        c.alignment = Alignment(horizontal="right")
        c.border = _THIN_BORDER
        c.font = Font(size=9)
        # Highlight last two rows
        if i >= len(summary_items) - 2:
            ws2.cell(row=r, column=1).fill = _TOTAL_FILL
            c.fill = _TOTAL_FILL
            c.font = _BOLD_FONT

    ws2.column_dimensions["A"].width = 44
    ws2.column_dimensions["B"].width = 16

    # ── Sub-budgets section on the same sheet ────────────
    sub_start = 3 + len(summary_items) + 2
    ws2.cell(row=sub_start, column=1, value="Sous-budgets").font = Font(bold=True, size=12)

    sub_headers = ["Description", "Trace", "Prévu", "Utilisé", "Restant"]
    for ci, h in enumerate(sub_headers, 1):
        cell = ws2.cell(row=sub_start + 1, column=ci, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.border = _THIN_BORDER
        cell.alignment = Alignment(horizontal="right" if ci >= 3 else
                                   "center" if ci == 2 else "left")

    for i, c in enumerate(categories):
        r = sub_start + 2 + i
        ws2.cell(row=r, column=1, value=c["name"]).border = _THIN_BORDER
        ws2.cell(row=r, column=2, value=c["trace_code"]).border = _THIN_BORDER
        ws2.cell(row=r, column=2).alignment = Alignment(horizontal="center")
        for ci, key in enumerate(["planned", "used", "remaining"], 3):
            cell = ws2.cell(row=r, column=ci, value=float(c[key]))
            cell.number_format = _MONEY_FMT
            cell.alignment = Alignment(horizontal="right")
            cell.border = _THIN_BORDER
        if i % 2 == 1:
            for ci in range(1, 6):
                ws2.cell(row=r, column=ci).fill = _ALT_FILL

    # Totals row
    non_cont = [c for c in categories if not c["sub_budget"].is_contingency]
    total_row = sub_start + 2 + len(categories)
    ws2.cell(row=total_row, column=1, value="Total (excl. imprévus)").font = _BOLD_FONT
    ws2.cell(row=total_row, column=1).fill = _TOTAL_FILL
    ws2.cell(row=total_row, column=1).border = _THIN_BORDER
    ws2.cell(row=total_row, column=2).fill = _TOTAL_FILL
    ws2.cell(row=total_row, column=2).border = _THIN_BORDER
    for ci, vals in [(3, "planned"), (4, "used"), (5, "remaining")]:
        cell = ws2.cell(row=total_row, column=ci,
                        value=float(sum((c[vals] for c in non_cont), Decimal("0"))))
        cell.number_format = _MONEY_FMT
        cell.alignment = Alignment(horizontal="right")
        cell.font = _BOLD_FONT
        cell.fill = _TOTAL_FILL
        cell.border = _THIN_BORDER

    for ci in range(3, 6):
        ws2.column_dimensions[get_column_letter(ci)].width = 14

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
