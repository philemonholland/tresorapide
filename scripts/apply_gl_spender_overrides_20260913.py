"""Apply two user-confirmed spender overrides to upload 13 GL imports."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction

from audits.services import create_audit_log_entry
from budget.gl_reconciliation import GrandLivreReconciliationService
from budget.models import ExpenseSourceType, GrandLivreUpload
from members.models import Apartment, Member


UPLOAD_ID = 13
ACCOUNT = "13-51200"
SOURCE_SHA256 = "76365e211d5a6c95213060c6b1697ac8bca149483f5af93ce59ff23ff477fe8f"
SOURCE_TOTAL = Decimal("4753.16")
TARGETS = {
    67: {
        "expense_id": 106,
        "description": "Scellant",
        "amount": Decimal("9.30"),
        "member": ("Marylin", "Lamarche"),
        "apartment": "202",
    },
    68: {
        "expense_id": 107,
        "description": "Batterie d'urgence pour l'éclairage",
        "amount": Decimal("124.12"),
        "member": ("Carl-David", "Fortin"),
        "apartment": "206",
    },
}
REASON = "Attribution confirmée par le trésorier le 2026-09-13."


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest():
    upload = GrandLivreUpload.objects.select_related(
        "budget_year",
        "budget_year__house",
        "uploaded_by",
    ).get(pk=UPLOAD_ID)
    assert upload.account_number == ACCOUNT
    assert upload.budget_year.house.account_number == ACCOUNT
    assert upload.entry_count == 15
    assert upload.gl_solde_fin == SOURCE_TOTAL
    assert file_sha256(upload.uploaded_file.path) == SOURCE_SHA256

    manifest = []
    for row_number, target in TARGETS.items():
        entry = upload.entries.select_related(
            "matched_expense",
            "matched_expense__sub_budget",
        ).get(row_number=row_number)
        expense = entry.matched_expense
        assert expense is not None
        assert expense.pk == target["expense_id"]
        assert expense.source_type == ExpenseSourceType.GL_IMPORT
        assert expense.description == target["description"]
        assert expense.amount == target["amount"]
        assert expense.sub_budget.trace_code == 1

        first_name, last_name = target["member"]
        member = Member.objects.get(first_name=first_name, last_name=last_name)
        apartment = Apartment.objects.get(
            house=upload.budget_year.house,
            code=target["apartment"],
        )
        residency = member.residency_on(entry.date)
        assert residency is not None
        assert residency.apartment_id == apartment.pk
        label = f"{apartment.code} / {member.display_name}"
        manifest.append({
            "row": row_number,
            "entry_id": entry.pk,
            "expense_id": expense.pk,
            "description": expense.description,
            "date": expense.entry_date.isoformat(),
            "amount": str(expense.amount),
            "before": expense.spent_by_label,
            "after": label,
            "override_before": expense.gl_spent_by_override,
            "trace": expense.sub_budget.trace_code,
        })
    return upload, manifest


def apply(upload, manifest):
    changed = 0
    with transaction.atomic():
        for row in manifest:
            entry = upload.entries.select_related("matched_expense").get(
                row_number=row["row"]
            )
            expense = entry.matched_expense
            before = {
                "spent_by_label": expense.spent_by_label,
                "gl_spent_by_override": expense.gl_spent_by_override,
                "gl_spent_by_override_reason": expense.gl_spent_by_override_reason,
            }
            if (
                expense.spent_by_label == row["after"]
                and expense.gl_spent_by_override
                and expense.gl_spent_by_override_reason == REASON
            ):
                continue
            expense.spent_by_label = row["after"]
            expense.gl_spent_by_override = True
            expense.gl_spent_by_override_reason = REASON
            expense.save(update_fields=[
                "spent_by_label",
                "gl_spent_by_override",
                "gl_spent_by_override_reason",
                "updated_at",
            ])
            create_audit_log_entry(
                action="grand_livre.spent_by_overridden",
                target=expense,
                summary=(
                    f"Attribution manuelle confirmée pour {expense.description}"
                ),
                actor=upload.uploaded_by,
                payload={
                    "upload_id": upload.pk,
                    "gl_entry_id": entry.pk,
                    "row_number": entry.row_number,
                    "source_sha256": SOURCE_SHA256,
                    "before": before,
                    "after": {
                        "spent_by_label": expense.spent_by_label,
                        "gl_spent_by_override": True,
                        "gl_spent_by_override_reason": REASON,
                    },
                },
            )
            changed += 1

        assert changed == 2
        assert GrandLivreReconciliationService.sync_materialized_import_context(upload) == 0
        result = GrandLivreReconciliationService.build_reconciliation(upload)
        assert result.matched_count == 15
        assert result.unmatched_gl_count == 0
        assert result.missing_from_gl_count == 0
        assert result.gl_total == SOURCE_TOTAL
        assert result.grille_total == SOURCE_TOTAL
        assert result.difference == Decimal("0.00")

        for row in manifest:
            expense = upload.entries.select_related("matched_expense").get(
                row_number=row["row"]
            ).matched_expense
            assert expense.spent_by_label == row["after"]
            assert expense.gl_spent_by_override
            assert expense.gl_spent_by_override_reason == REASON

        create_audit_log_entry(
            action="grand_livre.spender_overrides_applied",
            target=upload,
            summary="Attributions Scellant et batterie d'urgence confirmées",
            actor=upload.uploaded_by,
            payload={
                "source_sha256": SOURCE_SHA256,
                "rows": manifest,
                "final_controls": {
                    "matched_count": result.matched_count,
                    "gl_total": str(result.gl_total),
                    "grid_total": str(result.grille_total),
                    "difference": str(result.difference),
                },
            },
        )
    return {
        "changed": changed,
        "synchronization_changes_after_override": 0,
        "gl_total": str(result.gl_total),
        "grid_total": str(result.grille_total),
        "difference": str(result.difference),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    upload, manifest = load_manifest()
    output = {
        "mode": "apply" if args.apply else "dry-run",
        "upload_id": upload.pk,
        "account": upload.account_number,
        "source_sha256": SOURCE_SHA256,
        "targets": manifest,
    }
    if args.apply:
        output["result"] = apply(upload, manifest)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
