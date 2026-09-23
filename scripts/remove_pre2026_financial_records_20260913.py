"""Remove active pre-2026 test financial records authorized on 2026-09-13."""

from datetime import date

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from audits.models import AuditLogEntry
from bons.models import BonDeCommande, BonStatus
from budget.models import Expense


CUTOFF = date(2026, 1, 1)
EXPECTED = {
    82: {
        "number": "17186",
        "date": date(2021, 11, 26),
        "receipt_id": 97,
        "receipt_hash": "53e30c214af569f6484bcc3288e8ef84ea09c152ff4a4a7263d3a6ce7fa2e62a",
        "expense_id": 88,
        "amount": "54.54",
    },
    85: {
        "number": "BB260004",
        "date": date(2024, 10, 24),
        "receipt_id": 99,
        "receipt_hash": "d006f5cf8caac779b6a344c97eb608da88999e1a82e27a072d93802bde4e51d8",
        "expense_id": 92,
        "amount": "14.65",
    },
    86: {
        "number": "BB260005",
        "date": date(2024, 10, 26),
        "receipt_id": 100,
        "receipt_hash": "79e264d26f34a8c1b22e71650e76206cfaa7bb2ddf35b726e84a7e7bb22fa330",
        "expense_id": 93,
        "amount": "79.30",
    },
    84: {
        "number": "BB260003",
        "date": date(2024, 10, 30),
        "receipt_id": 98,
        "receipt_hash": "0e5c76ffe07df8e23f6988fa547609f42f32d64e2ff2344c8a4645cb9d54f69a",
        "expense_id": 91,
        "amount": "36.78",
    },
}


@transaction.atomic
def run():
    now = timezone.now()
    user = User.objects.get(username="tresorierBB")
    reason = (
        "Retiré le 2026-09-13 à la demande du trésorier : "
        "donnée de test antérieure à 2026"
    )
    results = []

    active_pre2026_ids = set(
        BonDeCommande.objects.filter(purchase_date__lt=CUTOFF)
        .exclude(status=BonStatus.VOID)
        .values_list("pk", flat=True)
    )
    assert active_pre2026_ids == set(EXPECTED), active_pre2026_ids

    for bon_id, expected in EXPECTED.items():
        bon = BonDeCommande.objects.select_for_update().get(pk=bon_id)
        assert bon.number == expected["number"]
        assert bon.purchase_date == expected["date"]
        assert bon.status == BonStatus.VALIDATED

        receipts = list(
            bon.active_receipt_files.select_for_update().order_by("pk")
        )
        assert len(receipts) == 1
        assert receipts[0].pk == expected["receipt_id"]
        assert receipts[0].sha256_checksum == expected["receipt_hash"]

        expenses = list(
            Expense.objects.select_for_update()
            .filter(bon_de_commande=bon, is_cancellation=False)
            .order_by("pk")
        )
        assert len(expenses) == 1
        expense = expenses[0]
        assert expense.pk == expected["expense_id"]
        assert str(expense.amount) == expected["amount"]
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
            notes=reason,
        )

        receipt_manifest = [
            {
                "id": receipt.pk,
                "filename": receipt.original_filename,
                "sha256": receipt.sha256_checksum,
            }
            for receipt in receipts
        ]
        for receipt in receipts:
            receipt.archive(reason=reason)
        BonDeCommande.objects.filter(pk=bon.pk).update(
            status=BonStatus.VOID,
            void_reason=reason,
            voided_at=now,
        )
        AuditLogEntry.objects.create(
            actor=user,
            action="bon.voided.pre2026_test_cleanup",
            target_app_label="bons",
            target_model="bondecommande",
            target_object_id=str(bon.pk),
            summary=f"Bon {bon.number} retiré : donnée de test pré-2026",
            payload={
                "purchase_date": bon.purchase_date.isoformat(),
                "previous_status": bon.status,
                "previous_total": str(bon.total),
                "receipts": receipt_manifest,
                "expense": {
                    "original_id": expense.pk,
                    "original_amount": str(expense.amount),
                    "reversal_id": reversal.pk,
                    "reversal_amount": str(reversal.amount),
                },
            },
        )
        results.append({
            "bon_id": bon.pk,
            "number": bon.number,
            "receipt_id": receipts[0].pk,
            "expense_id": expense.pk,
            "reversal_id": reversal.pk,
        })

    assert not BonDeCommande.objects.filter(
        purchase_date__lt=CUTOFF,
    ).exclude(status=BonStatus.VOID).exists()

    return results


RESULT = run()
print(RESULT)
