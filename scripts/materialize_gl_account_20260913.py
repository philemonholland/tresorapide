"""Validate and materialize the authoritative BB account 13-51200 rows.

Run without arguments for a read-only manifest. Pass ``--apply`` only after a
fresh protected backup and the matching source-hash check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

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
    _clean_gl_import_description,
    _gl_entry_fingerprint,
    _resolve_gl_import_sub_budget,
    active_expenses_queryset,
)
from budget.models import GrandLivreUpload, SubBudget


UPLOAD_ID = 13
ACCOUNT = "13-51200"
SOURCE_SHA256 = "76365e211d5a6c95213060c6b1697ac8bca149483f5af93ce59ff23ff477fe8f"
SOURCE_TOTAL = Decimal("4753.16")
EXPECTED_PENDING = {
    66: ("2026-03-18", "QU PARENT", "360856", Decimal("184.15"), 1),
    67: ("2026-03-20", "QU PARENT", "360856", Decimal("9.30"), 1),
    68: ("2026-03-31", "BATTILL", "360867", Decimal("124.12"), 99),
    70: ("2026-06-05", "QU PARENT", "077909", Decimal("124.17"), 99),
    71: ("2026-06-17", "PEINTURE J", "17182", Decimal("2742.72"), 11),
    72: ("2026-07-02", "MULTICLES", "", Decimal("132.22"), 99),
    75: ("2026-07-23", "QU PARENT", "077939", Decimal("18.19"), 99),
    76: ("2026-09-01", "TERMINIX", "", Decimal("341.74"), 5),
}
EXPECTED_DESCRIPTIONS = {
    66: "Robinet et cartouche - appartement 103",
    67: "Scellant",
    68: "Batterie d'urgence pour l'éclairage",
    70: "1 ferme-porte",
    71: "Peinture des piliers",
    72: "Poignée de porte",
    75: "Clapet et levier de toilette",
    76: "Contrat annuel du 1er septembre au 31 août 2027",
}


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_and_validate_source():
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
            "upload",
        ).order_by("row_number")
    )
    assert [entry.row_number for entry in entries] == list(range(62, 77))
    pending = [entry for entry in entries if entry.matched_expense_id is None]
    assert [entry.row_number for entry in pending] == list(EXPECTED_PENDING)
    assert sum((entry.net_amount for entry in pending), Decimal("0")) == Decimal("3676.61")

    for entry in pending:
        expected_date, expected_source, expected_bc, expected_amount, _trace = (
            EXPECTED_PENDING[entry.row_number]
        )
        assert (entry.date.isoformat() if entry.date else "") == expected_date
        assert entry.source == expected_source
        assert entry.extracted_bc_number == expected_bc
        assert entry.net_amount == expected_amount
        assert _clean_gl_import_description(entry) == EXPECTED_DESCRIPTIONS[entry.row_number]
    return upload, entries, pending


def build_manifest(upload, entries, pending):
    existing_by_bc = {}
    for expense in active_expenses_queryset(upload.budget_year).select_related("sub_budget"):
        digits = "".join(character for character in (expense.bon_number or "") if character.isdigit())
        if digits:
            existing_by_bc[digits] = expense
    fallback = SubBudget.objects.get(budget_year=upload.budget_year, trace_code=99)

    rows = []
    for entry in pending:
        category = _resolve_gl_import_sub_budget(
            upload.budget_year,
            entry,
            existing_by_bc=existing_by_bc,
            fallback_sub_budget=fallback,
        )
        expected_trace = EXPECTED_PENDING[entry.row_number][4]
        assert category.trace_code == expected_trace
        rows.append({
            "row": entry.row_number,
            "date": entry.date.isoformat() if entry.date else None,
            "source": entry.source,
            "bc": entry.extracted_bc_number,
            "description_source": entry.description_raw,
            "description_tresorapide": _clean_gl_import_description(entry),
            "amount": str(entry.net_amount),
            "trace": category.trace_code,
            "category": category.name,
            "fingerprint": _gl_entry_fingerprint(entry),
        })
        if entry.extracted_bc_number:
            existing_by_bc[entry.extracted_bc_number] = SimpleNamespace(
                sub_budget=category
            )

    local_snapshot = {
        entry.row_number: {
            "expense_id": entry.matched_expense_id,
            "date": entry.matched_expense.entry_date.isoformat(),
            "description": entry.matched_expense.description,
            "supplier": entry.matched_expense.supplier_name,
            "spent_by": entry.matched_expense.spent_by_label,
            "trace": entry.matched_expense.sub_budget.trace_code,
            "amount": str(entry.matched_expense.amount),
        }
        for entry in entries
        if entry.matched_expense_id
    }
    return rows, local_snapshot


def apply_materialization(upload, rows, local_snapshot):
    with transaction.atomic():
        created, skipped = GrandLivreReconciliationService.materialize_unmatched_entries(upload)
        assert len(created) == 8
        assert skipped == 0
        result = GrandLivreReconciliationService.build_reconciliation(upload)

        upload.refresh_from_db()
        assert upload.entries.filter(matched_expense__isnull=True).count() == 0
        assert result.matched_count == 15
        assert result.unmatched_gl_count == 0
        assert result.missing_from_gl_count == 0
        assert result.gl_total == SOURCE_TOTAL
        assert result.grille_total == SOURCE_TOTAL
        assert result.difference == Decimal("0.00")
        assert result.is_balanced

        for row_number, before in local_snapshot.items():
            entry = upload.entries.select_related(
                "matched_expense",
                "matched_expense__sub_budget",
            ).get(row_number=row_number)
            after = {
                "expense_id": entry.matched_expense_id,
                "date": entry.matched_expense.entry_date.isoformat(),
                "description": entry.matched_expense.description,
                "supplier": entry.matched_expense.supplier_name,
                "spent_by": entry.matched_expense.spent_by_label,
                "trace": entry.matched_expense.sub_budget.trace_code,
                "amount": str(entry.matched_expense.amount),
            }
            assert after == before

        created_again, skipped_again = (
            GrandLivreReconciliationService.materialize_unmatched_entries(upload)
        )
        assert created_again == []
        assert skipped_again == 0

        create_audit_log_entry(
            action="grand_livre.account_materialized",
            target=upload,
            summary=(
                "Compte 13-51200 matérialisé automatiquement dans la grille: "
                "15 écritures sur 15"
            ),
            actor=upload.uploaded_by,
            payload={
                "source_sha256": SOURCE_SHA256,
                "source_total": str(SOURCE_TOTAL),
                "created_expense_ids": [expense.pk for expense in created],
                "rows": rows,
                "final_controls": {
                    "entry_count": upload.entry_count,
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
        "created_expense_ids": [expense.pk for expense in created],
        "matched_count": result.matched_count,
        "unmatched_gl_count": result.unmatched_gl_count,
        "missing_from_gl_count": result.missing_from_gl_count,
        "gl_total": str(result.gl_total),
        "grid_total": str(result.grille_total),
        "difference": str(result.difference),
        "is_balanced": result.is_balanced,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    upload, entries, pending = load_and_validate_source()
    rows, local_snapshot = build_manifest(upload, entries, pending)
    output = {
        "mode": "apply" if args.apply else "dry-run",
        "upload_id": upload.pk,
        "account": upload.account_number,
        "source_sha256": SOURCE_SHA256,
        "source_entry_count": upload.entry_count,
        "source_total": str(upload.gl_solde_fin),
        "existing_local_matches": local_snapshot,
        "entries_to_materialize": rows,
    }
    if args.apply:
        output["result"] = apply_materialization(upload, rows, local_snapshot)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
