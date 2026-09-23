"""One-time, source-bound cleanup and BC entry authorized on 2026-09-13."""

from datetime import date
from decimal import Decimal

from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import transaction
from django.test import RequestFactory
from django.utils import timezone

from accounts.models import User
from audits.models import AuditLogEntry
from bons.ai_confidence import build_receipt_review_confidence_scores
from bons.forms import BonValidateForm
from bons.models import BonDeCommande, BonStatus, OcrStatus, ReceiptFile
from bons.views import (
    BonValidateView,
    OcrReviewView,
    _find_duplicate_bons_for_validation,
    _get_amount_consistency_warnings,
    _get_bon_amount_consistency_warning,
    _get_mismatch_warning,
)
from budget.models import Expense


EXPECTED_HASHES = {
    140: "de7f0649253abbc16cfeb7e30b88dc2600a5efabaf69cf78fe24909263463051",
    141: "fd0fa51f4e969b3961c940d60ff11ff2958759c78e9354199ddadaf463460154",
    142: "333b2df357fdfe812e18953135b1a30b081df491e26354e56705204a3dd10c42",
}


def request_for(user, method="get"):
    factory = RequestFactory()
    request = getattr(factory, method)("/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def receipt_manifest(bon):
    return list(
        bon.active_receipt_files.order_by("created_at", "pk").values(
            "id", "original_filename", "sha256_checksum", "ocr_status"
        )
    )


def void_stray_bon(bon, user, *, reason, now):
    expenses = list(
        Expense.objects.select_for_update()
        .filter(bon_de_commande=bon, is_cancellation=False)
        .order_by("pk")
    )
    expense_snapshots = [
        {
            "id": expense.pk,
            "bon_number": expense.bon_number,
            "amount": str(expense.amount),
            "sub_budget_id": expense.sub_budget_id,
            "description": expense.description,
            "already_cancelled": expense.is_cancelled,
        }
        for expense in expenses
    ]
    files = receipt_manifest(bon)

    for expense in expenses:
        if expense.is_cancelled:
            continue
        Expense.objects.create(
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

    for receipt in bon.active_receipt_files.order_by("created_at", "pk"):
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
        summary=f"Bon {bon.number} retiré comme donnée étrangère au lot demandé",
        payload={
            "reason": reason,
            "previous_status": bon.status,
            "previous_total": str(bon.total),
            "receipts": files,
            "expenses": expense_snapshots,
        },
    )


def set_final_fields(receipt, *, sub_budget_id, values, user, now):
    fields = receipt.extracted_fields
    for field_name, value in values.items():
        setattr(fields, field_name, value)
    fields.sub_budget_id = sub_budget_id
    fields.confirmed_by = user
    fields.confirmed_at = now
    confidence_values = {
        "document_type": fields.final_document_type,
        "bc_number": fields.final_bc_number,
        "associated_bc_number": fields.final_associated_bc_number,
        "supplier_name": fields.final_supplier_name,
        "supplier_address": fields.final_supplier_address,
        "reimburse_to": fields.final_reimburse_to,
        "expense_member_name": fields.final_expense_member_name,
        "expense_apartment": fields.final_expense_apartment,
        "validator_member_name": fields.final_validator_member_name,
        "validator_apartment": fields.final_validator_apartment,
        "signer_roles_ambiguous": fields.signer_roles_ambiguous_final,
        "member_name_raw": fields.member_name_candidate,
        "apartment_number": fields.final_apartment_number,
        "merchant_name": fields.final_merchant,
        "purchase_date": fields.final_purchase_date,
        "subtotal": fields.final_subtotal,
        "tps": fields.final_tps,
        "tvq": fields.final_tvq,
        "untaxed_extra_amount": fields.final_untaxed_extra_amount,
        "total": fields.final_total,
        "summary": fields.final_summary,
    }
    fields.final_confidence_scores = build_receipt_review_confidence_scores(
        receipt,
        confidence_values,
        document_type=fields.final_document_type,
    )
    fields.save()
    receipt.ocr_status = OcrStatus.CORRECTED
    receipt.save(update_fields=["ocr_status"])


@transaction.atomic
def run():
    now = timezone.now()
    user = User.objects.get(username="tresorierBB")
    scan = BonDeCommande.objects.select_for_update().get(pk=113)
    assert scan.status == BonStatus.READY_FOR_REVIEW
    assert scan.is_scan_session

    staged = {
        receipt.pk: receipt
        for receipt in ReceiptFile.objects.select_for_update()
        .filter(pk__in=EXPECTED_HASHES)
    }
    assert set(staged) == set(EXPECTED_HASHES)
    for receipt_id, expected_hash in EXPECTED_HASHES.items():
        receipt = staged[receipt_id]
        assert receipt.bon_de_commande_id == scan.pk
        assert receipt.sha256_checksum == expected_hash

    stray_reason = (
        "Retiré le 2026-09-13 à la demande du trésorier : données IGA étrangères "
        "au lot BC17181/BC17188/BC17190"
    )
    for bon_id in (102, 109):
        bon = BonDeCommande.objects.select_for_update().get(pk=bon_id)
        assert bon.number in {"BB260021", "BB260028"}
        void_stray_bon(bon, user, reason=stray_reason, now=now)

    set_final_fields(
        staged[140],
        sub_budget_id=4,
        user=user,
        now=now,
        values={
            "final_document_type": "paper_bc",
            "final_bc_number": "17181",
            "final_supplier_name": "Carl-David Fortin",
            "final_supplier_address": "1215 rue Kitchener, appartement 206",
            "final_reimburse_to": "member",
            "final_expense_member_name": "Carl-David Fortin",
            "final_expense_apartment": "206",
            "final_validator_member_name": "Alexis Camille Roman",
            "final_validator_apartment": "201",
            "signer_roles_ambiguous_final": False,
            "final_merchant": "Divers fournisseurs",
            "final_purchase_date": date(2026, 5, 16),
            "final_subtotal": Decimal("302.11"),
            "final_tps": Decimal("15.11"),
            "final_tvq": Decimal("30.14"),
            "final_untaxed_extra_amount": None,
            "final_total": Decimal("347.36"),
            "final_summary": (
                "Lampe fluo, ferme-porte, gypse coupe-feu, charnière ressort, "
                "ruban à gypse, plâtre, butoir et tirette, connecteur de tuyau "
                "et poignée"
            ),
        },
    )
    set_final_fields(
        staged[141],
        sub_budget_id=15,
        user=user,
        now=now,
        values={
            "final_document_type": "paper_bc",
            "final_bc_number": "17188",
            "final_supplier_name": "Alexis Camille Roman",
            "final_supplier_address": "1215 rue Kitchener, appartement 201",
            "final_reimburse_to": "member",
            "final_expense_member_name": "Alexis Camille Roman",
            "final_expense_apartment": "201",
            "final_validator_member_name": "Jessica Bergeron",
            "final_validator_apartment": "203",
            "signer_roles_ambiguous_final": False,
            "final_merchant": "Canva",
            "final_purchase_date": date(2026, 4, 23),
            "final_subtotal": Decimal("54.00"),
            "final_tps": Decimal("2.70"),
            "final_tvq": Decimal("5.40"),
            "final_untaxed_extra_amount": None,
            "final_total": Decimal("62.10"),
            "final_summary": "Trois abonnements Canva Pro",
        },
    )
    set_final_fields(
        staged[142],
        sub_budget_id=9,
        user=user,
        now=now,
        values={
            "final_document_type": "paper_bc",
            "final_bc_number": "17190",
            "final_supplier_name": "Alexis Camille Roman",
            "final_supplier_address": "1215 rue Kitchener, appartement 201",
            "final_reimburse_to": "member",
            "final_expense_member_name": "Alexis Camille Roman",
            "final_expense_apartment": "201",
            "final_validator_member_name": "Marylin Lamarche",
            "final_validator_apartment": "202",
            "signer_roles_ambiguous_final": False,
            "final_merchant": "CANAC",
            "final_purchase_date": date(2026, 6, 17),
            "final_subtotal": Decimal("19.99"),
            "final_tps": Decimal("1.00"),
            "final_tvq": Decimal("1.99"),
            "final_untaxed_extra_amount": None,
            "final_total": Decimal("22.98"),
            "final_summary": "Pistolet d'arrosage Stanley",
        },
    )

    existing_17190 = BonDeCommande.objects.select_for_update().get(pk=111)
    assert existing_17190.number == "17190"
    assert existing_17190.status == BonStatus.VALIDATED
    assert existing_17190.total == Decimal("22.98")
    previous_17190_files = receipt_manifest(existing_17190)
    for old_receipt_id in (138, 139):
        old_receipt = ReceiptFile.objects.select_for_update().get(pk=old_receipt_id)
        if old_receipt.archived_at is None:
            old_receipt.archive(
                reason="Remplacé par le PDF source autoritatif BC17190 fourni le 2026-09-13"
            )
    staged[142].bon_de_commande = existing_17190
    staged[142].save(update_fields=["bon_de_commande_id"])
    AuditLogEntry.objects.create(
        actor=user,
        action="bon.source_replaced",
        target_app_label="bons",
        target_model="bondecommande",
        target_object_id=str(existing_17190.pk),
        summary="Source de BC 17190 remplacée par le PDF autoritatif fourni",
        payload={
            "previous_receipts": previous_17190_files,
            "new_receipt_id": staged[142].pk,
            "new_sha256": staged[142].sha256_checksum,
        },
    )

    before_ids = set(BonDeCommande.objects.values_list("pk", flat=True))
    OcrReviewView()._finalize_bons(request_for(user), scan)
    created = list(
        BonDeCommande.objects.select_for_update()
        .filter(is_paper_bc=True, paper_bc_number__in=["17181", "17188"])
        .exclude(pk__in=before_ids)
        .order_by("paper_bc_number")
    )
    assert [bon.paper_bc_number for bon in created] == ["17181", "17188"]

    for bon in created:
        receipts = list(bon.active_receipt_files.order_by("created_at", "pk"))
        assert not _get_amount_consistency_warnings(receipts)
        assert _get_bon_amount_consistency_warning(bon) is None
        assert not any(_get_mismatch_warning(receipt) for receipt in receipts)
        validated_duplicates, unvalidated_duplicates = (
            _find_duplicate_bons_for_validation(bon, receipts)
        )
        assert not validated_duplicates
        assert not unvalidated_duplicates

        form = BonValidateForm({"confirm": True})
        assert form.is_valid(), form.errors
        view = BonValidateView()
        view.request = request_for(user, method="post")
        view.bon = bon
        response = view.form_valid(form)
        assert response.status_code == 302
        bon.refresh_from_db()
        assert bon.status == BonStatus.VALIDATED
        assert Expense.objects.filter(
            bon_de_commande=bon,
            is_cancellation=False,
        ).count() == 1
        AuditLogEntry.objects.create(
            actor=user,
            action="bon.entered.authoritative_pdf",
            target_app_label="bons",
            target_model="bondecommande",
            target_object_id=str(bon.pk),
            summary=f"BC papier {bon.paper_bc_number} validé depuis le PDF fourni",
            payload={
                "receipt_ids": [receipt.pk for receipt in receipts],
                "sha256": receipts[0].sha256_checksum,
                "total": str(bon.total),
                "sub_budget_id": bon.sub_budget_id,
            },
        )

    scan.refresh_from_db()
    existing_17190.refresh_from_db()
    assert scan.status == BonStatus.VOID
    assert existing_17190.status == BonStatus.VALIDATED
    assert existing_17190.active_receipt_files.filter(pk=142).exists()
    assert Expense.objects.filter(
        bon_de_commande=existing_17190,
        is_cancellation=False,
    ).count() == 1

    return {
        "voided_bons": [102, 109],
        "cancelled_expense": 95,
        "validated_bons": [
            {"id": bon.pk, "number": bon.number, "total": str(bon.total)}
            for bon in created
        ],
        "retained_17190": {
            "id": existing_17190.pk,
            "number": existing_17190.number,
            "total": str(existing_17190.total),
            "source_receipt": 142,
        },
    }


RESULT = run()
print(RESULT)
