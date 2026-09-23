"""Remove the two Carl-David Clarke duplicates authorized on 2026-09-13."""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from audits.models import AuditLogEntry
from bons.models import BonDeCommande, BonStatus
from budget.models import Expense


EXPECTED_RECEIPT_HASH = (
    "0e5c76ffe07df8e23f6988fa547609f42f32d64e2ff2344c8a4645cb9d54f69a"
)


@transaction.atomic
def run():
    now = timezone.now()
    user = User.objects.get(username="tresorierBB")
    results = []

    for bon_id, expected_number in ((88, "BB260007"), (90, "BB260009")):
        bon = BonDeCommande.objects.select_for_update().get(pk=bon_id)
        assert bon.number == expected_number
        assert bon.status in {
            BonStatus.READY_FOR_VALIDATION,
            BonStatus.VALIDATED,
        }
        receipts = list(
            bon.active_receipt_files.select_for_update().order_by("pk")
        )
        assert len(receipts) == 1
        assert receipts[0].sha256_checksum == EXPECTED_RECEIPT_HASH
        assert receipts[0].extracted_fields.final_member_name == "Carl-David Fortin"

        expenses = list(
            Expense.objects.select_for_update()
            .filter(bon_de_commande=bon, is_cancellation=False)
            .order_by("pk")
        )
        if bon_id == 88:
            assert expenses == []
        else:
            assert len(expenses) == 1
            assert expenses[0].pk == 94
            assert expenses[0].amount == Decimal("36.78")

        expense_manifest = []
        for expense in expenses:
            assert not expense.is_cancelled
            reversal = Expense.objects.create(
                budget_year=expense.budget_year,
                sub_budget=expense.sub_budget,
                bon_de_commande=expense.bon_de_commande,
                entry_date=now.date(),
                description=f"[ANNULATION] {expense.description}",
                bon_number=expense.bon_number,
                supplier_name=expense.supplier_name,
                reimburse_to=expense.reimburse_to,
                spent_by_label=expense.spent_by_label,
                amount=-expense.amount,
                source_type=expense.source_type,
                entered_by=user,
                is_cancellation=True,
                reversal_of=expense,
                notes=(
                    "Retiré le 2026-09-13 à la demande du trésorier : "
                    "attribution Carl-David incorrecte"
                ),
            )
            expense_manifest.append(
                {
                    "original_id": expense.pk,
                    "original_amount": str(expense.amount),
                    "reversal_id": reversal.pk,
                    "reversal_amount": str(reversal.amount),
                }
            )

        receipt_manifest = [
            {
                "id": receipt.pk,
                "filename": receipt.original_filename,
                "sha256": receipt.sha256_checksum,
            }
            for receipt in receipts
        ]
        reason = (
            "Retiré le 2026-09-13 à la demande du trésorier : "
            "attribution Carl-David incorrecte"
        )
        for receipt in receipts:
            receipt.archive(reason=reason)
        BonDeCommande.objects.filter(pk=bon.pk).update(
            status=BonStatus.VOID,
            void_reason=reason,
            voided_at=now,
        )
        AuditLogEntry.objects.create(
            actor=user,
            action="bon.voided.user_cleanup",
            target_app_label="bons",
            target_model="bondecommande",
            target_object_id=str(bon.pk),
            summary=f"Bon {bon.number} retiré : attribution Carl-David incorrecte",
            payload={
                "previous_status": bon.status,
                "previous_total": str(bon.total),
                "receipts": receipt_manifest,
                "expenses": expense_manifest,
            },
        )
        results.append(
            {
                "bon_id": bon.pk,
                "number": bon.number,
                "receipts_archived": [receipt.pk for receipt in receipts],
                "expenses": expense_manifest,
            }
        )

    return results


RESULT = run()
print(RESULT)
