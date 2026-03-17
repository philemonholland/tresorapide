"""PDF package generation for archival reimbursement bundles."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from django.template.loader import render_to_string

from reimbursements.models import Reimbursement

try:
    from xhtml2pdf import pisa
except ImportError as exc:  # pragma: no cover - exercised in runtime setup, not logic tests.
    raise RuntimeError(
        "xhtml2pdf is required for reimbursement PDF package generation."
    ) from exc


def build_reimbursement_package_context(
    reimbursement: Reimbursement,
) -> dict[str, Any]:
    """Build a PDF-safe context using snapshot fields for archival stability."""
    receipt_files = list(
        reimbursement.receipt_files.order_by("created_at", "id").values(
            "id",
            "original_filename",
            "content_type",
            "file_size_bytes",
            "sha256_checksum",
            "created_at",
            "archived_at",
            "archive_reason",
        )
    )
    active_receipts = [receipt for receipt in receipt_files if receipt["archived_at"] is None]
    archived_receipts = [
        receipt for receipt in receipt_files if receipt["archived_at"] is not None
    ]
    return {
        "reimbursement": reimbursement,
        "member_name": reimbursement.member_name_snapshot,
        "member_email": reimbursement.member_email_snapshot,
        "apartment_display": reimbursement.apartment_display_snapshot,
        "budget_label": reimbursement.budget_year_label_snapshot,
        "budget_category_label": (
            f"{reimbursement.budget_category_code_snapshot} - "
            f"{reimbursement.budget_category_name_snapshot}"
        ),
        "residency_period": (
            f"{reimbursement.residency_start_snapshot} to "
            f"{reimbursement.residency_end_snapshot or 'present'}"
            if reimbursement.residency_start_snapshot
            else "No residency snapshot available"
        ),
        "receipt_signer_name": reimbursement.receipt_signer_name_snapshot,
        "active_receipts": active_receipts,
        "archived_receipts": archived_receipts,
    }


def render_reimbursement_package_pdf(reimbursement: Reimbursement) -> bytes:
    """Render a deterministic accountant-ready PDF package."""
    html = render_to_string(
        "reimbursements/pdf_package.html",
        build_reimbursement_package_context(reimbursement),
    )
    output = BytesIO()
    result = pisa.CreatePDF(html, dest=output, encoding="utf-8")
    if result.err:
        raise RuntimeError(
            f"Unable to render PDF package for reimbursement {reimbursement.reference_code}."
        )
    return output.getvalue()
