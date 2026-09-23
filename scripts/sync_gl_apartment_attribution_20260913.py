"""Correct BB/apt attribution for upload 13 GL-imported expenses.

Run without arguments for a read-only manifest. Pass ``--apply`` only after a
fresh protected backup and matching source-hash verification.
"""
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
from budget.gl_reconciliation import (
    GrandLivreReconciliationService,
    _extract_apartment,
    _gl_import_spent_by_label,
)
from budget.models import ExpenseSourceType, GrandLivreUpload


UPLOAD_ID = 13
ACCOUNT = "13-51200"
SOURCE_SHA256 = "76365e211d5a6c95213060c6b1697ac8bca149483f5af93ce59ff23ff477fe8f"
SOURCE_TOTAL = Decimal("4753.16")
EXPECTED = {
    63: (89, "BB", 0),
    64: (90, "102 / Melyse Mupfasoni", 1),
    66: (105, "103 / Carole Lacourse", 1),
    67: (106, "103 / Carole Lacourse", 1),
    68: (107, "103 / Carole Lacourse", 1),
    70: (108, "BB", 99),
    71: (109, "BB", 11),
    72: (110, "BB", 99),
    75: (111, "BB", 99),
    76: (112, "BB", 5),
}


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_source():
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
    entries = list(
        upload.entries.select_related(
            "matched_expense",
            "matched_expense__sub_budget",
        ).order_by("row_number")
    )
    assert len(entries) == 15
    assert all(entry.matched_expense_id for entry in entries)
    return upload, entries


def preview(upload, entries):
    local_snapshot = {
        entry.row_number: {
            "expense_id": entry.matched_expense_id,
            "spent_by": entry.matched_expense.spent_by_label,
            "description": entry.matched_expense.description,
            "trace": entry.matched_expense.sub_budget.trace_code,
        }
        for entry in entries
        if entry.matched_expense.source_type != ExpenseSourceType.GL_IMPORT
    }
    current_period = None
    current_apartment = ""
    rows = []
    for entry in entries:
        period = entry.period or (
            entry.date.strftime("%Y-%m") if entry.date else ""
        )
        if period != current_period:
            current_period = period
            current_apartment = ""
        explicit = _extract_apartment(entry.description_raw)
        if explicit:
            current_apartment = explicit

        if entry.matched_expense.source_type != ExpenseSourceType.GL_IMPORT:
            continue
        entry.extracted_apartment = current_apartment
        label = _gl_import_spent_by_label(upload.budget_year, entry)
        trace = (
            1 if current_apartment
            else entry.matched_expense.sub_budget.trace_code
        )
        expected_expense, expected_label, expected_trace = EXPECTED[entry.row_number]
        assert entry.matched_expense_id == expected_expense
        assert label == expected_label
        assert trace == expected_trace
        rows.append({
            "row": entry.row_number,
            "expense_id": entry.matched_expense_id,
            "period": period,
            "explicit_apartment": explicit,
            "effective_apartment": current_apartment,
            "before_spent_by": entry.matched_expense.spent_by_label,
            "after_spent_by": label,
            "before_trace": entry.matched_expense.sub_budget.trace_code,
            "after_trace": trace,
        })
    assert set(EXPECTED) == {row["row"] for row in rows}
    return rows, local_snapshot


def apply(upload, rows, local_snapshot):
    with transaction.atomic():
        GrandLivreReconciliationService.propagate_apartment_context(upload)
        changed = GrandLivreReconciliationService.sync_materialized_import_context(upload)
        assert changed == 10
        result = GrandLivreReconciliationService.build_reconciliation(upload)
        assert result.matched_count == 15
        assert result.unmatched_gl_count == 0
        assert result.missing_from_gl_count == 0
        assert result.gl_total == SOURCE_TOTAL
        assert result.grille_total == SOURCE_TOTAL
        assert result.difference == Decimal("0.00")

        for row_number, (expense_id, label, trace) in EXPECTED.items():
            entry = upload.entries.select_related(
                "matched_expense",
                "matched_expense__sub_budget",
            ).get(row_number=row_number)
            assert entry.matched_expense_id == expense_id
            assert entry.matched_expense.spent_by_label == label
            assert entry.matched_expense.sub_budget.trace_code == trace

        for row_number, before in local_snapshot.items():
            entry = upload.entries.select_related(
                "matched_expense",
                "matched_expense__sub_budget",
            ).get(row_number=row_number)
            after = {
                "expense_id": entry.matched_expense_id,
                "spent_by": entry.matched_expense.spent_by_label,
                "description": entry.matched_expense.description,
                "trace": entry.matched_expense.sub_budget.trace_code,
            }
            assert after == before

        assert GrandLivreReconciliationService.sync_materialized_import_context(upload) == 0
        create_audit_log_entry(
            action="grand_livre.apartment_attribution_corrected",
            target=upload,
            summary=(
                "Attribution appartement/BB corrigée pour les imports du "
                "compte 13-51200"
            ),
            actor=upload.uploaded_by,
            payload={
                "source_sha256": SOURCE_SHA256,
                "rows": rows,
                "final_controls": {
                    "matched_count": result.matched_count,
                    "unmatched_gl_count": result.unmatched_gl_count,
                    "missing_from_gl_count": result.missing_from_gl_count,
                    "gl_total": str(result.gl_total),
                    "grid_total": str(result.grille_total),
                    "difference": str(result.difference),
                },
            },
        )
    return {
        "changed": changed,
        "gl_total": str(result.gl_total),
        "grid_total": str(result.grille_total),
        "difference": str(result.difference),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    upload, entries = load_source()
    rows, local_snapshot = preview(upload, entries)
    output = {
        "mode": "apply" if args.apply else "dry-run",
        "upload_id": upload.pk,
        "account": upload.account_number,
        "source_sha256": SOURCE_SHA256,
        "rows": rows,
        "preserved_local_rows": local_snapshot,
    }
    if args.apply:
        output["result"] = apply(upload, rows, local_snapshot)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
