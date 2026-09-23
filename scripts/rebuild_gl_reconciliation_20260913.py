"""Restore source-correct BC17186 and rebuild GL upload 13 without AI calls."""

import hashlib
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from audits.models import AuditLogEntry
from bons.models import BonDeCommande, BonStatus, ReceiptFile
from budget.gl_reconciliation import GrandLivreReconciliationService
from budget.models import Expense, GrandLivreUpload


BC17186_HASH = "53e30c214af569f6484bcc3288e8ef84ea09c152ff4a4a7263d3a6ce7fa2e62a"
GL_HASH = "76365e211d5a6c95213060c6b1697ac8bca149483f5af93ce59ff23ff477fe8f"


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@transaction.atomic
def run():
    user = User.objects.get(username="tresorierBB")
    now = timezone.now()

    bon = BonDeCommande.objects.select_for_update().get(pk=82)
    receipt = ReceiptFile.objects.select_for_update().get(pk=97)
    expense = Expense.objects.select_for_update().get(pk=88)
    reversal = Expense.objects.select_for_update().get(pk=101)

    assert bon.number == "17186"
    assert bon.status == BonStatus.VOID
    assert bon.total == Decimal("54.54")
    assert receipt.bon_de_commande_id == bon.pk
    assert receipt.sha256_checksum == BC17186_HASH
    assert receipt.archived_at is not None
    assert expense.bon_de_commande_id == bon.pk
    assert expense.amount == Decimal("54.54")
    assert reversal.reversal_of_id == expense.pk
    assert reversal.amount == Decimal("-54.54")

    previous = {
        "bon_date": bon.purchase_date.isoformat(),
        "bon_status": bon.status,
        "receipt_archived_at": receipt.archived_at.isoformat(),
        "expense_date": expense.entry_date.isoformat(),
        "reversal_id": reversal.pk,
        "reversal_amount": str(reversal.amount),
    }

    ReceiptFile.objects.filter(pk=receipt.pk).update(
        archived_at=None,
        archive_reason="",
        page_count=2,
    )
    fields = receipt.extracted_fields
    fields.purchase_date_candidate = date(2026, 2, 26)
    fields.final_purchase_date = date(2026, 2, 26)
    fields.save(update_fields=[
        "purchase_date_candidate",
        "final_purchase_date",
        "updated_at",
    ])
    BonDeCommande.objects.filter(pk=bon.pk).update(
        purchase_date=date(2026, 2, 26),
        status=BonStatus.VALIDATED,
        void_reason="",
        voided_at=None,
    )
    Expense.objects.filter(pk=expense.pk).update(
        entry_date=date(2026, 2, 26),
        validated_gl=False,
    )
    Expense.objects.filter(pk=reversal.pk).delete()

    AuditLogEntry.objects.create(
        actor=user,
        action="bon.restored.source_date_correction",
        target_app_label="bons",
        target_model="bondecommande",
        target_object_id=str(bon.pk),
        summary="BC 17186 restauré; date corrigée au 2026-02-26 depuis les documents source",
        payload={
            "previous": previous,
            "current": {
                "bon_date": "2026-02-26",
                "bon_status": BonStatus.VALIDATED,
                "receipt_id": receipt.pk,
                "receipt_sha256": receipt.sha256_checksum,
                "expense_id": expense.pk,
                "expense_amount": str(expense.amount),
            },
        },
    )

    upload = GrandLivreUpload.objects.select_for_update().get(pk=13)
    assert upload.account_number == "13-51200"
    assert file_sha256(upload.uploaded_file.path) == GL_HASH
    old_entry_ids = list(upload.entries.order_by("row_number").values_list("pk", flat=True))

    GrandLivreReconciliationService.parse_and_store(upload)
    GrandLivreReconciliationService.match_expenses(upload, use_ai=False)
    result = GrandLivreReconciliationService.build_reconciliation(upload)
    upload.refresh_from_db()

    assert upload.entry_count == 15
    assert upload.gl_total_debit == Decimal("4753.16")
    assert upload.gl_total_credit == Decimal("0.00")
    assert upload.gl_solde_fin == Decimal("4753.16")
    assert result.gl_total == Decimal("4753.16")
    assert result.adjustment_total == Decimal("0.00")
    assert result.adjusted_gl_total == Decimal("4753.16")
    assert result.grille_total == Decimal("1076.55")
    assert result.difference == Decimal("3676.61")
    assert result.matched_count == 7
    assert result.unmatched_gl_count == 8
    assert result.missing_from_gl_count == 0

    expected_matches = {
        62: 87,
        63: 89,
        64: 90,
        65: 88,
        69: 98,
        73: 96,
        74: 99,
    }
    actual_matches = {
        row_number: matched_expense_id
        for row_number, matched_expense_id in upload.entries.filter(
            matched_expense__isnull=False,
        ).values_list("row_number", "matched_expense_id")
    }
    assert actual_matches == expected_matches, actual_matches
    assert not any(
        "résiduel inexpliqué" in anomaly.get("message", "")
        or "Écart restant" in anomaly.get("message", "")
        for anomaly in result.anomalies
    )

    AuditLogEntry.objects.create(
        actor=user,
        action="grand_livre.reprocessed.parser_correction",
        target_app_label="budget",
        target_model="grandlivreupload",
        target_object_id=str(upload.pk),
        summary="Grand Livre BB retraité avec les colonnes Débit/Crédit/Solde corrigées",
        payload={
            "source_sha256": GL_HASH,
            "old_entry_ids": old_entry_ids,
            "new_entry_ids": list(upload.entries.order_by("row_number").values_list("pk", flat=True)),
            "entry_count": upload.entry_count,
            "gl_total_debit": str(upload.gl_total_debit),
            "gl_total_credit": str(upload.gl_total_credit),
            "gl_solde_fin": str(upload.gl_solde_fin),
            "matched_count": result.matched_count,
            "unmatched_gl_count": result.unmatched_gl_count,
            "missing_from_gl_count": result.missing_from_gl_count,
            "ai_calls": 0,
        },
    )

    return {
        "restored_bon": bon.pk,
        "restored_expense": expense.pk,
        "deleted_reversal": reversal.pk,
        "upload": upload.pk,
        "entries": upload.entry_count,
        "gl_total": str(result.gl_total),
        "grid_total": str(result.grille_total),
        "difference": str(result.difference),
        "matched": result.matched_count,
        "unmatched_gl": result.unmatched_gl_count,
        "missing_from_gl": result.missing_from_gl_count,
    }


RESULT = run()
print(RESULT)
