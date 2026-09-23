"""Tests for Grand Livre parser, reconciliation, and views."""
import json
import os
import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, RequestFactory
from django.core.files.uploadedfile import SimpleUploadedFile

import openpyxl

from accounts.models import User
from houses.models import House
from members.models import Apartment, Member, Residency
from budget.models import (
    BudgetYear, SubBudget, Expense, ExpenseSourceType,
    GrandLivreUpload, GrandLivreEntry, GrandLivreAdjustment, ReconciliationResult,
    GLUploadStatus, GLMatchConfidence,
)
from budget.gl_parser import (
    parse_grand_livre, GLAccountSection, _normalize_account_number,
    _parse_french_date,
)
from budget.gl_reconciliation import _extract_bc_number, _extract_apartment


def _create_test_gl_workbook(
    transactions, account="13-51200", total_debit=None, total_credit=None,
    period_end_text=None,
    first_transaction_in_header=False,
):
    """Create a minimal Grand Livre Excel file in memory for testing."""
    wb = openpyxl.Workbook()
    ws = wb.active

    # Header rows (mimicking the real file)
    ws.append(["", "", "", "", "", "Coopérative d'habitation", "", "", "", "", ""])
    ws.append(["", "", "", "", "", "des Cantons de l'Est", "", "", "", "", ""])
    ws.append(["", "", "", "", "", "Grand-Livre", "", "", "", "", ""])
    ws.append(["", "", "", "", "", "", "", "", "", "", ""])
    # Row 5: may contain period end date
    d5 = period_end_text or ""
    ws.append(["", "", "", d5, "", "", "", "", "", "", ""])
    ws.append(["", "", "", "", "", "", "", "", "", "", ""])
    ws.append(["", "", "", "", "", "", "", "", "", "", ""])
    # Row 8: column headers
    ws.append(["No compte", "Description", "Ann/Pér", "Date", "Source", "Description", "", "Solde début", "Débit", "Crédit", "Solde fin"])

    running_debit = Decimal("0")
    running_credit = Decimal("0")
    prefix = account.split("-")[0]
    start_idx = 0

    # Some real GL exports put the first transaction on the same row as the
    # account header.
    if first_transaction_in_header and transactions:
        tx = transactions[0]
        d = tx.get("debit", 0)
        c = tx.get("credit", 0)
        running_debit += Decimal(str(d))
        running_credit += Decimal(str(c))
        solde = running_debit - running_credit
        ws.append([
            account,
            "Entretien et ré",
            tx.get("period"),
            tx.get("date"),
            tx.get("source", ""),
            tx.get("description", ""),
            None,
            0,
            d if d else None,
            c if c else None,
            float(solde) if tx.get("show_solde", True) else None,
        ])
        start_idx = 1
    else:
        ws.append([account, "Entretien et ré", None, None, None, None, None, None, None, None, None])

    # Transaction rows
    for tx in transactions[start_idx:]:
        d = tx.get("debit", 0)
        c = tx.get("credit", 0)
        running_debit += Decimal(str(d))
        running_credit += Decimal(str(c))
        solde = running_debit - running_credit
        ws.append([
            None,  # A
            None,  # B
            tx.get("period"),  # C
            tx.get("date"),  # D
            tx.get("source", ""),  # E
            tx.get("description", ""),  # F
            None,  # G
            None,  # H (Solde début)
            d if d else None,  # I (Débit)
            c if c else None,  # J (Crédit)
            float(solde) if tx.get("show_solde", True) else None,  # K (Solde fin)
        ])

    # Total row
    td = total_debit if total_debit is not None else float(running_debit)
    tc = total_credit if total_credit is not None else float(running_credit)
    total_num = prefix + "51200"
    count = len(transactions)
    ws.append([
        None, None, None, None, None, None,
        f"Total No compte {total_num} : {count}",
        None, td, tc, float(Decimal(str(td)) - Decimal(str(tc))),
    ])

    # Grand Total
    ws.append([None] * 11)
    ws.append([None] * 11)
    ws.append(["No compte", "Description", "Ann/Pér", "Date", "Source", "Description", "",
               "Solde début", "Débit", "Crédit", "Solde fin"])
    ws.append([None, None, None, None, None, None, "Grand Total :", None, td, tc, None])

    return wb


def _save_wb_temp(wb):
    """Save workbook to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    wb.save(path)
    return path


class GLParserNormalizationTests(TestCase):
    def test_normalize_with_dash(self):
        self.assertEqual(_normalize_account_number("13-51200"), "13-51200")

    def test_normalize_without_dash(self):
        self.assertEqual(_normalize_account_number("1351200"), "13-51200")

    def test_normalize_whitespace(self):
        self.assertEqual(_normalize_account_number(" 13-51200 "), "13-51200")


class GLParserExtractionTests(TestCase):
    def test_extract_bc_number(self):
        self.assertEqual(_extract_bc_number("496578-BC 16482-scellant"), "16482")
        self.assertEqual(_extract_bc_number("492428-BC168377-tuyaux"), "168377")
        self.assertEqual(_extract_bc_number("no bc here"), "")

    def test_extract_apartment(self):
        self.assertEqual(_extract_apartment("tuyauxéchangeur#104"), "104")
        self.assertEqual(_extract_apartment("511888-BC137940-Colleplomberie#102"), "102")
        self.assertEqual(_extract_apartment("no apartment"), "")


class GLParserTests(TestCase):
    """Test parsing of Grand Livre Excel files."""

    def test_parse_basic_section(self):
        transactions = [
            {"date": date(2025, 4, 15), "source": "CANACMARQ", "description": "9008563050-BC 16481-bac roulant", "debit": 126.44},
            {"date": date(2025, 4, 24), "source": "QU PARENT", "description": "496578-BC 16482-scellant, mortier", "debit": 41.86},
            {"date": date(2025, 5, 1), "source": "QU PARENT", "description": "497066-BC 16483-clé, mortier", "debit": 10.81},
        ]
        wb = _create_test_gl_workbook(transactions, account="13-51200")
        path = _save_wb_temp(wb)
        try:
            section = parse_grand_livre(path, "13-51200")
            self.assertEqual(section.account_number, "13-51200")
            self.assertEqual(len(section.transactions), 3)
            self.assertEqual(section.transactions[0].source, "CANACMARQ")
            self.assertEqual(section.transactions[0].debit, Decimal("126.44"))
            self.assertEqual(section.transactions[1].description, "496578-BC 16482-scellant, mortier")
            self.assertEqual(section.total_debit, Decimal("179.11"))
            self.assertEqual(section.total_credit, Decimal("0.00"))
        finally:
            os.unlink(path)

    def test_parse_with_credits(self):
        transactions = [
            {"date": date(2025, 6, 1), "source": "SUPPLIER", "description": "Purchase", "debit": 100},
            {"date": date(2025, 7, 1), "source": "SUPPLIER", "description": "Refund", "debit": 0, "credit": 25},
        ]
        wb = _create_test_gl_workbook(transactions, account="13-51200")
        path = _save_wb_temp(wb)
        try:
            section = parse_grand_livre(path, "13-51200")
            self.assertEqual(len(section.transactions), 2)
            self.assertEqual(section.transactions[1].credit, Decimal("25.00"))
            self.assertEqual(section.solde_fin, Decimal("75.00"))
        finally:
            os.unlink(path)

    def test_parse_compact_nine_column_accountant_layout(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append([None] * 9)
        ws.append([None] * 9)
        ws.append([None] * 9)
        ws.append([None] * 9)
        ws.append([None] * 8 + [date(2026, 9, 9)])
        ws.append([None] * 9)
        ws.append([None] * 9)
        ws.append([
            "No compte", "Description", "Ann/Pér", "Date", "Source",
            "Description", "Débit", "Crédit", "Solde fin",
        ])
        ws.append([
            "13-51200", "Entretien et ré", date(2026, 1, 1),
            date(2026, 1, 7), "SANY", "4999081-BC 16739-Nettoyants",
            19.49, None, None,
        ])
        ws.append([
            None, None, None, date(2026, 1, 30), "POULIOT", "Réparation",
            100.00, None, 119.49,
        ])
        # Compact exports have no textual total label. The final row contains
        # only Debit, Credit and Ending Balance.
        ws.append([None, None, None, None, None, None, 119.49, 25.00, 94.49])
        ws.append([
            "14-51200", "Next account", date(2026, 1, 1),
            date(2026, 1, 8), "OTHER", "Other", 999.00, None, 999.00,
        ])
        path = _save_wb_temp(wb)
        try:
            section = parse_grand_livre(path, "13-51200")
        finally:
            os.unlink(path)

        self.assertEqual(len(section.transactions), 2)
        self.assertEqual(section.transactions[0].debit, Decimal("19.49"))
        self.assertEqual(section.transactions[1].debit, Decimal("100.00"))
        self.assertEqual(section.total_debit, Decimal("119.49"))
        self.assertEqual(section.total_credit, Decimal("25.00"))
        self.assertEqual(section.solde_fin, Decimal("94.49"))
        self.assertEqual(section.entry_count, 2)
        self.assertEqual(section.period_end_date, date(2026, 9, 9))

    def test_parse_missing_account(self):
        wb = _create_test_gl_workbook(
            [{"date": date(2025, 1, 1), "source": "X", "description": "Y", "debit": 10}],
            account="99-51200",
        )
        path = _save_wb_temp(wb)
        try:
            section = parse_grand_livre(path, "13-51200")
            self.assertEqual(len(section.transactions), 0)
        finally:
            os.unlink(path)

    def test_parse_real_grand_livre(self):
        """Test against the actual reference file if available."""
        gl_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "budget_example_spreadsheet",
            "622-grands-livres-maison-au-31-decembre-2025-final-1.xlsx",
        )
        if not os.path.exists(gl_path):
            self.skipTest("Reference GL file not available")

        section = parse_grand_livre(gl_path, "13-51200")
        self.assertEqual(section.account_number, "13-51200")
        self.assertGreater(len(section.transactions), 30)
        self.assertEqual(section.total_debit, Decimal("8932.05"))
        self.assertEqual(section.solde_fin, Decimal("8932.05"))


class GLReconciliationTests(TestCase):
    """Test the reconciliation service."""

    def setUp(self):
        self.house = House.objects.create(
            code="BB", name="Maison BB",
            account_number="13-51200", accounting_code="13",
        )
        self.member = Member.objects.create(
            first_name="Test", last_name="User",
        )
        self.user = User.objects.create_user(
            username="tester", password="pass123",
            house=self.house, role="TREASURER",
        )
        self.by = BudgetYear.objects.create(
            house=self.house, year=2025,
            annual_budget_total=Decimal("11951"),
            snow_budget=Decimal("1858"),
        )
        # Create sub-budgets
        self.sub_repair = SubBudget.objects.create(
            budget_year=self.by, trace_code=1,
            name="Réparations", planned_amount=2080,
        )
        self.sub_misc = SubBudget.objects.create(
            budget_year=self.by, trace_code=8,
            name="Produits ménager", planned_amount=300,
        )

    def _add_expense(self, desc, amount, bon_number="", entry_date=None, sub=None):
        return Expense.objects.create(
            budget_year=self.by,
            sub_budget=sub or self.sub_repair,
            entry_date=entry_date or date(2025, 5, 1),
            description=desc,
            bon_number=bon_number,
            amount=Decimal(str(amount)),
            spent_by_label="101 / Test",
        )

    def _create_upload_with_entries(self, transactions, **workbook_kwargs):
        wb = _create_test_gl_workbook(
            transactions,
            account="13-51200",
            **workbook_kwargs,
        )
        path = _save_wb_temp(wb)
        try:
            with open(path, "rb") as f:
                content = f.read()
        finally:
            os.unlink(path)

        upload = GrandLivreUpload.objects.create(
            budget_year=self.by,
            uploaded_file=SimpleUploadedFile("gl_test.xlsx", content,
                                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            uploaded_by=self.user,
        )
        return upload

    def test_parse_and_store(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        transactions = [
            {"date": date(2025, 4, 15), "source": "CANAC", "description": "BC 16481-bac", "debit": 126.44},
            {"date": date(2025, 5, 1), "source": "PARENT", "description": "BC 16482-mortier", "debit": 41.86},
        ]
        upload = self._create_upload_with_entries(transactions)
        section = Svc.parse_and_store(upload)
        upload.refresh_from_db()

        self.assertEqual(upload.status, GLUploadStatus.PARSED)
        self.assertEqual(upload.entry_count, 2)
        self.assertEqual(upload.entries.count(), 2)

    def test_reprocessing_preserves_source_row_identity_and_adjustment_link(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        upload = self._create_upload_with_entries([{
            "date": date(2025, 4, 15),
            "source": "CANAC",
            "description": "BC 16481-bac",
            "debit": 126.44,
        }])
        Svc.parse_and_store(upload)
        entry = upload.entries.get()
        adjustment = GrandLivreAdjustment.objects.create(
            entry=entry,
            amount_to_subtract=Decimal("10.00"),
            reason="Test stable link",
            created_by=self.user,
        )

        Svc.parse_and_store(upload)

        refreshed_entry = upload.entries.get()
        adjustment.refresh_from_db()
        self.assertEqual(refreshed_entry.pk, entry.pk)
        self.assertEqual(adjustment.entry_id, entry.pk)

    def test_match_by_amount(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        # Create an expense that should match
        self._add_expense("Bac roulant", 126.44)

        transactions = [
            {"date": date(2025, 4, 15), "source": "CANAC", "description": "BC 16481-bac", "debit": 126.44},
            {"date": date(2025, 5, 1), "source": "PARENT", "description": "BC 16482-mortier", "debit": 41.86},
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload)

        entries = list(upload.entries.order_by("row_number"))
        self.assertIsNotNone(entries[0].matched_expense)
        self.assertEqual(entries[0].match_confidence, GLMatchConfidence.PROBABLE)
        self.assertIsNone(entries[1].matched_expense)

    def test_match_by_unique_amount_and_same_date_is_exact(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        expense = self._add_expense(
            "Exact amount and date",
            126.44,
            entry_date=date(2025, 4, 15),
        )
        upload = self._create_upload_with_entries([{
            "date": date(2025, 4, 15),
            "source": "CANAC",
            "description": "Exact amount and date",
            "debit": 126.44,
        }])
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload, use_ai=False)

        entry = upload.entries.first()
        expense.refresh_from_db()
        self.assertEqual(entry.matched_expense_id, expense.pk)
        self.assertEqual(entry.match_confidence, GLMatchConfidence.EXACT)
        self.assertTrue(expense.validated_gl)

    def test_match_by_bc_number(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        self._add_expense("Some purchase", 41.86, bon_number="16482")

        transactions = [
            {"date": date(2025, 5, 1), "source": "PARENT", "description": "496578-BC 16482-scellant", "debit": 41.86},
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload)

        entry = upload.entries.first()
        self.assertIsNotNone(entry.matched_expense)
        self.assertEqual(entry.extracted_bc_number, "16482")

    def test_bc_number_does_not_match_when_amount_differs(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        self._add_expense("Some purchase", 41.86, bon_number="16482")
        transactions = [{
            "date": date(2025, 5, 1),
            "source": "PARENT",
            "description": "496578-BC 16482-scellant",
            "debit": 50.00,
        }]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload, use_ai=False)

        entry = upload.entries.first()
        self.assertIsNone(entry.matched_expense)
        self.assertEqual(entry.match_confidence, GLMatchConfidence.UNMATCHED)
        self.assertTrue(entry.needs_import)

    @patch("openai.OpenAI")
    def test_enrich_with_ai_accepts_string_row_identifiers(self, mock_openai):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        transactions = [
            {
                "date": date(2025, 5, 1),
                "source": "PARENT",
                "description": "496578-BC 16482-scellant",
                "debit": 41.86,
            },
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        entry = upload.entries.first()

        mock_openai.return_value.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps([
                            {
                                "row": str(entry.row_number),
                                "bc_number": "16482",
                                "apartment": "104",
                                "description_clean": "Scellant et mortier",
                                "field_confidence_scores": {
                                    "bc_number": "8",
                                    "apartment": "7",
                                    "description_clean": 9,
                                },
                            }
                        ])
                    )
                )
            ]
        )

        with self.settings(OPENAI_API_KEY="test-key"):
            Svc.enrich_with_ai(upload)

        entry.refresh_from_db()
        self.assertEqual(entry.extracted_bc_number, "16482")
        self.assertEqual(entry.extracted_apartment, "104")
        self.assertEqual(entry.description_clean, "Scellant et mortier")
        self.assertEqual(entry.ai_metadata["parse_field_confidence_scores"]["bc_number"], 8)
        self.assertEqual(entry.ai_metadata["parse_field_confidence_scores"]["description_clean"], 9)

    @patch("openai.OpenAI")
    def test_enrich_with_ai_preserves_existing_parse_metadata_on_gpt_failure(self, mock_openai):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        transactions = [
            {
                "period": "2025-14",
                "date": date(2025, 1, 7),
                "source": "SANY",
                "description": "4999081-BC 16482-Nettoyants",
                "debit": 19.49,
            },
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        entry = upload.entries.first()
        entry.ai_metadata = {
            "parse_field_confidence_scores": {
                "bc_number": 8,
                "apartment": "NA",
                "description_clean": 7,
            }
        }
        entry.save(update_fields=["ai_metadata"])

        mock_openai.return_value.chat.completions.create.side_effect = RuntimeError("boom")

        with self.settings(OPENAI_API_KEY="test-key"):
            Svc.enrich_with_ai(upload)

        entry.refresh_from_db()
        self.assertEqual(entry.ai_metadata["parse_field_confidence_scores"]["bc_number"], 8)
        self.assertEqual(entry.ai_metadata["parse_field_confidence_scores"]["description_clean"], 7)

    def test_match_by_bc_number_when_first_transaction_shares_account_row(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        expense = self._add_expense(
            "Bon de commande pour 1 NUBIOCAL 900ML",
            19.49,
            bon_number="16739",
            entry_date=date(2025, 1, 7),
            sub=self.sub_misc,
        )

        transactions = [
            {
                "period": "2025-14",
                "date": date(2025, 1, 7),
                "source": "SANY",
                "description": "4999081-BC 16739-Nettoyants",
                "debit": 19.49,
            },
        ]
        upload = self._create_upload_with_entries(
            transactions,
            first_transaction_in_header=True,
        )
        Svc.parse_and_store(upload)
        self.assertEqual(upload.entries.count(), 1)

        Svc.match_expenses(upload)

        entry = upload.entries.first()
        self.assertEqual(entry.extracted_bc_number, "16739")
        self.assertEqual(entry.matched_expense_id, expense.id)
        self.assertEqual(entry.match_confidence, GLMatchConfidence.EXACT)

    def test_match_prefers_bon_expense_over_prior_gl_import_duplicate(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        original = self._add_expense(
            "Bon de commande pour 1 NUBIOCAL 900ML",
            19.49,
            bon_number="16739",
            entry_date=date(2025, 1, 7),
            sub=self.sub_misc,
        )
        duplicate = Expense.objects.create(
            budget_year=self.by,
            sub_budget=self.sub_misc,
            entry_date=date(2025, 1, 7),
            description="4999081-BC 16739-Nettoyants",
            bon_number="16739",
            amount=Decimal("19.49"),
            spent_by_label="202 / Marylin Lamarche",
            validated_gl=True,
            source_type=ExpenseSourceType.GL_IMPORT,
        )

        transactions = [
            {
                "date": date(2025, 1, 7),
                "source": "SANY",
                "description": "4999081-BC 16739-Nettoyants",
                "debit": 19.49,
            },
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload)

        entry = upload.entries.first()
        self.assertEqual(entry.matched_expense_id, original.id)
        self.assertNotEqual(entry.matched_expense_id, duplicate.id)
        self.assertEqual(entry.match_confidence, GLMatchConfidence.EXACT)

    def test_match_expenses_clears_stale_semantic_match_metadata_on_exact_match(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        expense = self._add_expense(
            "Bon de commande pour mortier",
            41.86,
            bon_number="16482",
            entry_date=date(2025, 5, 1),
            sub=self.sub_misc,
        )
        transactions = [
            {"date": date(2025, 5, 1), "source": "PARENT", "description": "496578-BC 16482-mortier", "debit": 41.86},
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        entry = upload.entries.first()
        entry.ai_metadata = {
            "semantic_match": {
                "expense_id": 999999,
                "confidence": 0.91,
                "confidence_score": 8,
            }
        }
        entry.save(update_fields=["ai_metadata"])

        Svc.match_expenses(upload)

        entry.refresh_from_db()
        self.assertEqual(entry.matched_expense_id, expense.id)
        self.assertEqual(entry.match_confidence, GLMatchConfidence.EXACT)
        self.assertNotIn("semantic_match", entry.ai_metadata)

    @patch("openai.OpenAI")
    def test_gpt_semantic_match_accepts_string_identifiers(self, mock_openai):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        target_expense = self._add_expense(
            "Bon de commande pour nettoyant",
            19.49,
            sub=self.sub_misc,
        )
        self._add_expense(
            "Autre depense nettoyant",
            19.49,
            sub=self.sub_misc,
        )

        transactions = [
            {
                "date": None,
                "source": "SANY",
                "description": "NUBIOCAL 900ML",
                "debit": 19.49,
            },
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        entry = upload.entries.first()

        mock_openai.return_value.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps([
                            {
                                "gl_id": str(entry.pk),
                                "expense_id": str(target_expense.pk),
                                "confidence": "0.91",
                                "confidence_score": "8",
                            }
                        ])
                    )
                )
            ]
        )

        with self.settings(OPENAI_API_KEY="test-key"):
            Svc.match_expenses(upload)

        entry.refresh_from_db()
        self.assertEqual(entry.matched_expense_id, target_expense.id)
        self.assertEqual(entry.match_confidence, GLMatchConfidence.PROBABLE)
        self.assertIn("score IA 8/9", entry.match_notes)
        self.assertEqual(entry.ai_metadata["semantic_match"]["expense_id"], target_expense.id)
        self.assertEqual(entry.ai_metadata["semantic_match"]["confidence_score"], 8)

    def test_build_reconciliation_balanced(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        self._add_expense("Expense A", 100)
        self._add_expense("Expense B", 50)

        transactions = [
            {"date": date(2025, 4, 15), "source": "X", "description": "A", "debit": 100},
            {"date": date(2025, 5, 1), "source": "Y", "description": "B", "debit": 50},
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload)
        result = Svc.build_reconciliation(upload)

        self.assertTrue(result.is_balanced)
        self.assertEqual(result.matched_count, 2)
        self.assertEqual(result.unmatched_gl_count, 0)
        self.assertEqual(result.status_light, "green")

    def test_build_reconciliation_unbalanced(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        # Only one expense, but GL has two
        self._add_expense("Expense A", 100)

        transactions = [
            {"date": date(2025, 4, 15), "source": "X", "description": "A", "debit": 100},
            {"date": date(2025, 5, 1), "source": "Y", "description": "B", "debit": 50},
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload)
        result = Svc.build_reconciliation(upload)

        self.assertFalse(result.is_balanced)
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.unmatched_gl_count, 1)

    def test_import_validated_entries(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc
        from audits.models import AuditLogEntry

        transactions = [
            {"date": date(2025, 5, 1), "source": "PARENT", "description": "497066-BC 16483-clé#104", "debit": 10.81},
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload)

        # Mark entry as validated
        entry = upload.entries.first()
        entry.is_validated = True
        entry.save()

        created, skipped = Svc.import_validated_entries(upload, [entry.pk])
        self.assertEqual(len(created), 1)

        expense = created[0]
        self.assertEqual(expense.amount, Decimal("10.81"))
        self.assertEqual(expense.source_type, ExpenseSourceType.GL_IMPORT)
        self.assertTrue(expense.validated_gl)
        self.assertEqual(expense.spent_by_label, "104")
        self.assertEqual(expense.display_approved_by_label, "CHCE")
        self.assertEqual(expense.sub_budget.trace_code, 1)
        self.assertEqual(expense.description, "Clé - appartement 104")
        self.assertEqual(len(expense.gl_source_fingerprint), 64)
        self.assertEqual(
            AuditLogEntry.objects.filter(
                action="grand_livre.entry_materialized",
                target_object_id=str(expense.pk),
            ).count(),
            1,
        )

    def test_materialize_all_house_account_rows_preserves_local_fields(self):
        from audits.models import AuditLogEntry
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        paint = SubBudget.objects.create(
            budget_year=self.by,
            trace_code=11,
            name="Peinture",
            planned_amount=250,
        )
        extermination = SubBudget.objects.create(
            budget_year=self.by,
            trace_code=5,
            name="Exterminateur",
            planned_amount=350,
        )
        apartment_103 = Apartment.objects.create(
            house=self.house,
            code="103",
        )
        carole = Member.objects.create(
            first_name="Carole",
            last_name="Lacourse",
        )
        Residency.objects.create(
            member=carole,
            apartment=apartment_103,
            start_date=date(2025, 1, 1),
            is_primary_contact=True,
        )
        local = self._add_expense(
            "Notre description claire",
            19.49,
            bon_number="16739",
            entry_date=date(2025, 1, 7),
        )
        transactions = [
            {
                "date": date(2025, 1, 7),
                "source": "SANY",
                "description": "4999081-BC 16739-Nettoyants",
                "debit": 19.49,
            },
            {
                "date": date(2025, 3, 18),
                "source": "QU PARENT",
                "description": "514268-BC360856-Robinet+cartouche#103",
                "debit": 184.15,
            },
            {
                "date": date(2025, 3, 20),
                "source": "QU PARENT",
                "description": "514391-BC 360856-scellant",
                "debit": 9.30,
            },
            {
                "date": date(2025, 3, 31),
                "source": "BATTILL",
                "description": "76641-BC 360867-batterie d'urgence éclairage",
                "debit": 124.12,
            },
            {
                "date": date(2025, 6, 17),
                "source": "PEINTURE J",
                "description": "5480-BC 17182-peinture pilliers",
                "debit": 2742.72,
            },
            {
                "date": date(2025, 9, 1),
                "source": "TERMINIX",
                "description": "4540484-Contratannueldu1septau31août2027",
                "debit": 341.74,
            },
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload, use_ai=False)

        created, skipped = Svc.materialize_unmatched_entries(upload)
        result = Svc.build_reconciliation(upload)

        self.assertEqual(len(created), 5)
        self.assertEqual(skipped, 0)
        self.assertEqual(Expense.objects.count(), 6)
        local.refresh_from_db()
        self.assertEqual(local.description, "Notre description claire")
        self.assertEqual(local.entry_date, date(2025, 1, 7))

        bc_group = list(Expense.objects.filter(bon_number="360856"))
        self.assertEqual(len(bc_group), 2)
        self.assertEqual({expense.sub_budget.trace_code for expense in bc_group}, {1})
        self.assertEqual(
            {expense.spent_by_label for expense in bc_group},
            {"103 / Carole Lacourse"},
        )
        self.assertEqual(
            Expense.objects.get(bon_number="17182").sub_budget,
            paint,
        )
        self.assertEqual(
            Expense.objects.get(supplier_name="TERMINIX").sub_budget,
            extermination,
        )
        self.assertEqual(
            Expense.objects.get(bon_number="360867").sub_budget.trace_code,
            1,
        )
        self.assertEqual(
            Expense.objects.get(bon_number="360867").spent_by_label,
            "103 / Carole Lacourse",
        )
        self.assertEqual(
            Expense.objects.get(supplier_name="TERMINIX").spent_by_label,
            "BB",
        )
        self.assertEqual(
            AuditLogEntry.objects.filter(
                action="grand_livre.entry_materialized"
            ).count(),
            5,
        )
        self.assertEqual(result.matched_count, 6)
        self.assertEqual(result.unmatched_gl_count, 0)
        self.assertEqual(result.missing_from_gl_count, 0)
        self.assertEqual(result.grille_total, result.gl_total)
        self.assertEqual(result.difference, Decimal("0.00"))
        self.assertTrue(result.is_balanced)

        created_again, skipped_again = Svc.materialize_unmatched_entries(upload)
        self.assertEqual(created_again, [])
        self.assertEqual(skipped_again, 0)
        self.assertEqual(Expense.objects.count(), 6)

    def test_full_reconciliation_materializes_unmatched_rows_automatically(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        upload = self._create_upload_with_entries([{
            "date": date(2025, 4, 15),
            "source": "CANAC",
            "description": "123456-BC 16481-bac",
            "debit": 126.44,
        }])
        with (
            patch.object(Svc, "enrich_with_ai"),
            patch.object(Svc, "analyze_with_ai"),
        ):
            Svc.full_reconciliation(upload)

        upload.refresh_from_db()
        entry = upload.entries.get()
        self.assertIsNotNone(entry.matched_expense_id)
        self.assertFalse(entry.needs_import)
        self.assertTrue(entry.is_validated)
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(upload.reconciliation.matched_count, 1)
        self.assertEqual(upload.reconciliation.unmatched_gl_count, 0)
        expense = Expense.objects.get()
        self.assertEqual(expense.spent_by_label, "BB")
        self.assertEqual(expense.display_approved_by_label, "CHCE")

        with (
            patch.object(Svc, "enrich_with_ai"),
            patch.object(Svc, "analyze_with_ai"),
        ):
            Svc.full_reconciliation(upload)
        self.assertEqual(Expense.objects.count(), 1)

    def test_materialization_rejects_another_account(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        upload = self._create_upload_with_entries([{
            "date": date(2025, 4, 15),
            "source": "CANAC",
            "description": "123456-BC 16481-bac",
            "debit": 126.44,
        }])
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload, use_ai=False)
        upload.account_number = "99-99999"
        upload.save(update_fields=["account_number"])

        with self.assertRaises(ValidationError):
            Svc.materialize_unmatched_entries(upload)
        self.assertEqual(Expense.objects.count(), 0)

    def test_credit_materializes_as_an_active_negative_gl_expense(self):
        from budget.gl_reconciliation import (
            GrandLivreReconciliationService as Svc,
            active_expenses_queryset,
        )

        upload = self._create_upload_with_entries([{
            "date": date(2025, 4, 15),
            "source": "FOURNISSEUR",
            "description": "123456-Crédit fournisseur",
            "debit": 0,
            "credit": 25.50,
        }])
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload, use_ai=False)
        created, skipped = Svc.materialize_unmatched_entries(upload)
        result = Svc.build_reconciliation(upload)

        self.assertEqual(skipped, 0)
        self.assertEqual(len(created), 1)
        expense = created[0]
        self.assertEqual(expense.amount, Decimal("-25.50"))
        self.assertFalse(expense.is_cancellation)
        self.assertTrue(
            active_expenses_queryset(self.by).filter(pk=expense.pk).exists()
        )
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.unmatched_gl_count, 0)
        self.assertEqual(result.missing_from_gl_count, 0)
        self.assertEqual(result.grille_total, Decimal("-25.50"))
        self.assertEqual(result.difference, Decimal("0.00"))
        self.assertEqual(expense.spent_by_label, "BB")
        self.assertEqual(expense.display_approved_by_label, "CHCE")

    def test_apartment_context_propagates_within_period_and_resets(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        transactions = [
            {
                "period": "2025-02",
                "date": date(2025, 2, 2),
                "description": "111-BC100-Colle#102",
                "debit": 1,
            },
            {
                "date": date(2025, 2, 26),
                "description": "112-BC101-Robinet",
                "debit": 2,
            },
            {
                "period": "2025-03",
                "date": date(2025, 3, 18),
                "description": "113-BC102-Cartouche#103",
                "debit": 3,
            },
            {
                "date": date(2025, 3, 20),
                "description": "114-BC103-Scellant",
                "debit": 4,
            },
            {
                "date": date(2025, 3, 31),
                "description": "115-BC104-Batterie",
                "debit": 5,
            },
            {
                "period": "2025-05",
                "date": date(2025, 5, 1),
                "description": "116-BC105-Poignée",
                "debit": 6,
            },
        ]
        upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(upload)

        entries = list(upload.entries.order_by("row_number"))
        self.assertEqual(
            [entry.extracted_apartment for entry in entries],
            ["102", "102", "103", "103", "103", ""],
        )
        self.assertEqual(
            [entry.ai_metadata["apartment_context"]["source"] for entry in entries],
            [
                "explicit",
                "period_inherited",
                "explicit",
                "period_inherited",
                "period_inherited",
                "none",
            ],
        )

    def test_sync_updates_only_gl_imports_and_keeps_existing_category_without_apartment(self):
        from audits.models import AuditLogEntry
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        apartment_103 = Apartment.objects.create(house=self.house, code="103")
        carole = Member.objects.create(first_name="Carole", last_name="Lacourse")
        Residency.objects.create(
            member=carole,
            apartment=apartment_103,
            start_date=date(2025, 1, 1),
            is_primary_contact=True,
        )
        gl_first = self._add_expense(
            "Premier import GL",
            10,
            entry_date=date(2025, 3, 18),
            sub=self.by.sub_budgets.get(trace_code=0),
        )
        gl_first.source_type = ExpenseSourceType.GL_IMPORT
        gl_first.spent_by_label = "CHCE"
        gl_first.save()
        gl_second = self._add_expense(
            "Deuxième import GL",
            20,
            entry_date=date(2025, 3, 20),
            sub=self.by.sub_budgets.get(trace_code=0),
        )
        gl_second.source_type = ExpenseSourceType.GL_IMPORT
        gl_second.spent_by_label = "CHCE"
        gl_second.save()
        gl_house = self._add_expense(
            "Import maison",
            30,
            entry_date=date(2025, 4, 1),
            sub=self.by.sub_budgets.get(trace_code=0),
        )
        gl_house.source_type = ExpenseSourceType.GL_IMPORT
        gl_house.spent_by_label = "CHCE"
        gl_house.save()
        local = self._add_expense(
            "Achat membre",
            40,
            entry_date=date(2025, 4, 2),
        )
        local.spent_by_label = "201 / Alexis Camille Roman"
        local.save()

        upload = self._create_upload_with_entries([
            {
                "period": "2025-03",
                "date": date(2025, 3, 18),
                "description": "1-BC101-Robinet#103",
                "debit": 10,
            },
            {
                "date": date(2025, 3, 20),
                "description": "2-BC102-Scellant",
                "debit": 20,
            },
            {
                "period": "2025-04",
                "date": date(2025, 4, 1),
                "description": "3-BC103-Poignée",
                "debit": 30,
            },
            {
                "date": date(2025, 4, 2),
                "description": "4-BC104-Achat membre",
                "debit": 40,
            },
        ])
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload, use_ai=False)
        changed = Svc.sync_materialized_import_context(upload)

        self.assertEqual(changed, 3)
        gl_first.refresh_from_db()
        gl_second.refresh_from_db()
        gl_house.refresh_from_db()
        local.refresh_from_db()
        self.assertEqual(gl_first.spent_by_label, "103 / Carole Lacourse")
        self.assertEqual(gl_second.spent_by_label, "103 / Carole Lacourse")
        self.assertEqual(gl_first.sub_budget.trace_code, 1)
        self.assertEqual(gl_second.sub_budget.trace_code, 1)
        self.assertEqual(gl_house.spent_by_label, "BB")
        self.assertEqual(gl_house.sub_budget.trace_code, 0)
        self.assertEqual(local.spent_by_label, "201 / Alexis Camille Roman")
        self.assertEqual(
            AuditLogEntry.objects.filter(
                action="grand_livre.import_context_synchronized"
            ).count(),
            3,
        )

    def test_sync_preserves_manual_gl_spender_override(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        apartment_103 = Apartment.objects.create(house=self.house, code="103")
        carole = Member.objects.create(first_name="Carole", last_name="Lacourse")
        Residency.objects.create(
            member=carole,
            apartment=apartment_103,
            start_date=date(2025, 1, 1),
            is_primary_contact=True,
        )
        expense = self._add_expense(
            "Scellant",
            9.30,
            entry_date=date(2025, 3, 20),
            sub=self.sub_repair,
        )
        expense.source_type = ExpenseSourceType.GL_IMPORT
        expense.spent_by_label = "202 / Marylin Lamarche"
        expense.gl_spent_by_override = True
        expense.gl_spent_by_override_reason = "Confirmation du trésorier"
        expense.save()
        upload = self._create_upload_with_entries([{
            "period": "2025-03",
            "date": date(2025, 3, 20),
            "description": "514391-BC 360856-scellant#103",
            "debit": 9.30,
        }])
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload, use_ai=False)

        changed = Svc.sync_materialized_import_context(upload)

        expense.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(expense.spent_by_label, "202 / Marylin Lamarche")
        self.assertTrue(expense.gl_spent_by_override)
        self.assertEqual(expense.sub_budget.trace_code, 1)

    def test_reuploaded_source_matches_fingerprint_without_duplicate(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        transactions = [{
            "date": date(2025, 4, 15),
            "source": "CANAC",
            "description": "123456-BC 16481-bac",
            "debit": 126.44,
        }]
        first_upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(first_upload)
        Svc.match_expenses(first_upload, use_ai=False)
        created, _ = Svc.materialize_unmatched_entries(first_upload)
        self.assertEqual(len(created), 1)
        expense = created[0]

        second_upload = self._create_upload_with_entries(transactions)
        Svc.parse_and_store(second_upload)
        Svc.match_expenses(second_upload, use_ai=False)
        second_entry = second_upload.entries.get()

        self.assertEqual(second_entry.matched_expense_id, expense.pk)
        self.assertEqual(second_entry.match_confidence, GLMatchConfidence.EXACT)
        self.assertIn("Même écriture source", second_entry.match_notes)
        created_again, skipped_again = Svc.materialize_unmatched_entries(
            second_upload
        )
        self.assertEqual(created_again, [])
        self.assertEqual(skipped_again, 0)
        self.assertEqual(Expense.objects.count(), 1)


    def test_reconciliation_excludes_reversed_and_void_bon_expenses(self):
        from bons.models import BonDeCommande, BonStatus
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        active = self._add_expense("Active", 30)
        reversed_expense = self._add_expense("Reversed", 40)
        Expense.objects.create(
            budget_year=self.by,
            sub_budget=self.sub_repair,
            entry_date=date(2025, 5, 2),
            description="[ANNULATION] Reversed",
            amount=Decimal("-40.00"),
            spent_by_label="101 / Test",
            is_cancellation=True,
            reversal_of=reversed_expense,
        )
        void_bon = BonDeCommande.objects.create(
            house=self.house,
            budget_year=self.by,
            number="VOID-TEST",
            purchase_date=date(2025, 5, 3),
            short_description="Void",
            total=Decimal("50.00"),
            sub_budget=self.sub_repair,
            purchaser_member=self.member,
            status=BonStatus.VOID,
        )
        Expense.objects.create(
            budget_year=self.by,
            sub_budget=self.sub_repair,
            bon_de_commande=void_bon,
            entry_date=date(2025, 5, 3),
            description="Void bon expense",
            amount=Decimal("50.00"),
            spent_by_label="101 / Test",
        )
        upload = GrandLivreUpload.objects.create(
            budget_year=self.by,
            uploaded_by=self.user,
            account_number="13-51200",
            status=GLUploadStatus.RECONCILED,
            gl_solde_fin=Decimal("0.00"),
            period_end_date=date(2025, 12, 31),
        )

        result = Svc.build_reconciliation(upload)

        self.assertEqual(result.grille_total, Decimal("30.00"))
        self.assertEqual(result.missing_from_gl_count, 1)
        self.assertEqual(
            result.anomalies[-1].get("expense_ids", []),
            [active.pk],
        )


class GLViewTests(TestCase):
    """Test GL views."""

    def setUp(self):
        self.house = House.objects.create(
            code="BB", name="Maison BB",
            account_number="13-51200", accounting_code="13",
        )
        self.member = Member.objects.create(
            first_name="Trésorier", last_name="Test",
        )
        self.user = User.objects.create_user(
            username="treasurer", password="pass123",
            house=self.house, role="TREASURER",
        )
        self.by = BudgetYear.objects.create(
            house=self.house, year=2025,
            annual_budget_total=Decimal("11951"),
        )

    def test_gl_list_requires_login(self):
        resp = self.client.get("/budget/grand-livre/")
        self.assertIn(resp.status_code, (302, 403))

    def test_gl_list_accessible_for_treasurer(self):
        self.client.login(username="treasurer", password="pass123")
        resp = self.client.get("/budget/grand-livre/")
        self.assertEqual(resp.status_code, 200)

    def test_gl_upload_creates_record(self):
        self.client.login(username="treasurer", password="pass123")

        transactions = [
            {"date": date(2025, 4, 15), "source": "X", "description": "Test", "debit": 100},
        ]
        wb = _create_test_gl_workbook(transactions, account="13-51200")
        path = _save_wb_temp(wb)
        try:
            with open(path, "rb") as f:
                resp = self.client.post(
                    "/budget/grand-livre/upload/",
                    {"budget_year": self.by.pk, "file": f},
                    follow=True,
                )
        finally:
            os.unlink(path)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(GrandLivreUpload.objects.count(), 1)
        upload = GrandLivreUpload.objects.first()
        self.assertEqual(upload.entry_count, 1)

    def test_gl_detail_shows_entries(self):
        self.client.login(username="treasurer", password="pass123")

        # Create upload via service
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        transactions = [
            {"date": date(2025, 4, 15), "source": "X", "description": "Test purchase", "debit": 100},
        ]
        wb = _create_test_gl_workbook(transactions, account="13-51200")
        path = _save_wb_temp(wb)
        try:
            with open(path, "rb") as f:
                content = f.read()
        finally:
            os.unlink(path)

        upload = GrandLivreUpload.objects.create(
            budget_year=self.by,
            uploaded_file=SimpleUploadedFile("gl.xlsx", content),
            uploaded_by=self.user,
        )
        Svc.parse_and_store(upload)
        Svc.match_expenses(upload)
        Svc.build_reconciliation(upload)

        resp = self.client.get(f"/budget/grand-livre/{upload.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Test purchase")


class GrandLivreAdjustmentTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(
            code="BB",
            name="Maison BB",
            account_number="13-51200",
            accounting_code="13",
        )
        self.user = User.objects.create_user(
            username="adjuster",
            password="pass123",
            house=self.house,
            role="TREASURER",
        )
        self.by = BudgetYear.objects.create(
            house=self.house,
            year=2026,
            annual_budget_total=Decimal("5000.00"),
        )
        self.upload = GrandLivreUpload.objects.create(
            budget_year=self.by,
            uploaded_by=self.user,
            account_number="13-51200",
            status=GLUploadStatus.RECONCILED,
            gl_total_debit=Decimal("100.00"),
            gl_total_credit=Decimal("0.00"),
            gl_solde_fin=Decimal("100.00"),
            period_end_date=date(2026, 9, 9),
            entry_count=1,
        )
        self.entry = GrandLivreEntry.objects.create(
            upload=self.upload,
            row_number=62,
            date=date(2026, 1, 7),
            source="SOURCE",
            description_raw="Source accounting row",
            debit=Decimal("100.00"),
            credit=Decimal("0.00"),
            needs_import=True,
        )
        self.client.login(username="adjuster", password="pass123")

    def test_adjustment_changes_derived_total_not_source_entry(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        GrandLivreAdjustment.objects.create(
            entry=self.entry,
            amount_to_subtract=Decimal("30.00"),
            reason="Montant contesté",
            created_by=self.user,
        )

        result = Svc.build_reconciliation(self.upload)

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.debit, Decimal("100.00"))
        self.assertEqual(result.gl_total, Decimal("100.00"))
        self.assertEqual(result.adjustment_total, Decimal("30.00"))
        self.assertEqual(result.adjusted_gl_total, Decimal("70.00"))
        self.assertEqual(result.difference, Decimal("70.00"))

    def test_zero_adjustment_preserves_source_total(self):
        from budget.gl_reconciliation import GrandLivreReconciliationService as Svc

        GrandLivreAdjustment.objects.create(
            entry=self.entry,
            amount_to_subtract=Decimal("0.00"),
            reason="Aucun écart retenu",
            created_by=self.user,
        )

        result = Svc.build_reconciliation(self.upload)

        self.assertEqual(result.adjustment_total, Decimal("0.00"))
        self.assertEqual(result.adjusted_gl_total, Decimal("100.00"))

    def test_create_renders_red_icon_and_child_row_directly_after_source(self):
        response = self.client.post(
            f"/budget/grand-livre/{self.upload.pk}/entries/{self.entry.pk}/adjustment/",
            {
                "amount_to_subtract": "25.00",
                "reason": "À confirmer avec la coopérative centrale",
            },
        )
        self.assertEqual(response.status_code, 302)

        detail = self.client.get(f"/budget/grand-livre/{self.upload.pk}/")
        html = detail.content.decode("utf-8")
        self.assertContains(detail, 'class="gl-override-icon"')
        self.assertContains(
            detail,
            'class="gl-adjustment-row"',
        )
        self.assertLess(
            html.index(f'id="gl-entry-{self.entry.pk}"'),
            html.index(f'data-parent-entry="{self.entry.pk}"'),
        )
        self.assertContains(detail, "-25.00 $")
        self.assertContains(detail, "Grand Livre source")
        self.assertContains(detail, "Grand Livre après ajustements")

    def test_full_adjustment_disables_import_and_archive_restores_source(self):
        from audits.models import AuditLogEntry

        create_response = self.client.post(
            f"/budget/grand-livre/{self.upload.pk}/entries/{self.entry.pk}/adjustment/",
            {
                "amount_to_subtract": "100.00",
                "reason": "Écriture contestée en totalité",
            },
        )
        self.assertEqual(create_response.status_code, 302)
        adjustment = GrandLivreAdjustment.objects.get(entry=self.entry)
        self.entry.refresh_from_db()
        self.assertFalse(self.entry.needs_import)

        detail = self.client.get(f"/budget/grand-livre/{self.upload.pk}/")
        self.assertContains(detail, "Écriture entièrement ajustée")

        archive_response = self.client.post(
            f"/budget/grand-livre/{self.upload.pk}/entries/{self.entry.pk}/adjustment/{adjustment.pk}/archive/",
            {"archive_reason": "Confirmation reçue"},
        )
        self.assertEqual(archive_response.status_code, 302)
        adjustment.refresh_from_db()
        self.entry.refresh_from_db()
        result = self.upload.reconciliation
        result.refresh_from_db()

        self.assertTrue(adjustment.is_archived)
        self.assertEqual(result.adjustment_total, Decimal("0.00"))
        self.assertEqual(result.adjusted_gl_total, Decimal("100.00"))
        self.assertTrue(self.entry.needs_import)
        detail = self.client.get(f"/budget/grand-livre/{self.upload.pk}/")
        self.assertNotContains(detail, 'class="gl-adjustment-row"')
        self.assertNotContains(detail, 'class="gl-override-icon"')
        self.assertTrue(AuditLogEntry.objects.filter(
            action="grand_livre.adjustment_created",
        ).exists())
        self.assertTrue(AuditLogEntry.objects.filter(
            action="grand_livre.adjustment_archived",
        ).exists())

    def test_adjustment_cannot_exceed_source_amount(self):
        response = self.client.post(
            f"/budget/grand-livre/{self.upload.pk}/entries/{self.entry.pk}/adjustment/",
            {
                "amount_to_subtract": "100.01",
                "reason": "Invalid",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ne peut pas dépasser")
        self.assertFalse(GrandLivreAdjustment.objects.exists())


class GLRealFileReconciliationTest(TestCase):
    """Integration test using the real reference files."""

    def test_real_file_reconciliation(self):
        """Test parsing the real Grand Livre file for house BB (13-51200)."""
        gl_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "budget_example_spreadsheet",
            "622-grands-livres-maison-au-31-decembre-2025-final-1.xlsx",
        )
        if not os.path.exists(gl_path):
            self.skipTest("Reference GL file not available")

        from budget.gl_parser import parse_grand_livre

        section = parse_grand_livre(gl_path, "13-51200")

        # Verify totals match the known values from the real file
        self.assertEqual(section.account_number, "13-51200")
        self.assertEqual(section.total_debit, Decimal("8932.05"))
        self.assertEqual(section.total_credit, Decimal("0.00"))
        self.assertEqual(section.solde_fin, Decimal("8932.05"))

        # The BB grille has 44 entries; the GL should have a comparable count
        self.assertGreater(len(section.transactions), 30)

        # Verify some known entries are present
        descriptions = [tx.description for tx in section.transactions]
        # Check for a known entry from the reference data
        has_bc_entry = any("BC" in d for d in descriptions)
        self.assertTrue(has_bc_entry, "Expected BC references in GL descriptions")


class FrenchDateParserTests(TestCase):
    """Tests for the French date parser used for GL period end date."""

    def test_parse_standard_date(self):
        self.assertEqual(
            _parse_french_date("Total des dépenses au 03 mars 2026"),
            date(2026, 3, 3),
        )

    def test_parse_single_digit_day(self):
        self.assertEqual(
            _parse_french_date("Total des dépenses au 5 janvier 2025"),
            date(2025, 1, 5),
        )

    def test_parse_december(self):
        self.assertEqual(
            _parse_french_date("Total des dépenses au 31 décembre 2025"),
            date(2025, 12, 31),
        )

    def test_parse_no_match(self):
        self.assertIsNone(_parse_french_date("no date here"))

    def test_parse_empty(self):
        self.assertIsNone(_parse_french_date(""))


class GLPeriodEndDateExtractionTests(TestCase):
    """Tests that period_end_date is extracted during parsing."""

    def test_period_end_from_header(self):
        wb = _create_test_gl_workbook(
            transactions=[{
                "period": "2026/01", "date": date(2026, 1, 30),
                "description": "test", "debit": 100,
            }],
            account="13-51200",
            period_end_text="Total des dépenses au 03 mars 2026",
        )
        path = _save_wb_temp(wb)
        section = parse_grand_livre(path, "13-51200")
        self.assertEqual(section.period_end_date, date(2026, 3, 3))
        os.unlink(path)

    def test_no_period_end_in_header(self):
        wb = _create_test_gl_workbook(
            transactions=[{
                "period": "2026/01", "date": date(2026, 1, 30),
                "description": "test", "debit": 100,
            }],
            account="13-51200",
        )
        path = _save_wb_temp(wb)
        section = parse_grand_livre(path, "13-51200")
        self.assertIsNone(section.period_end_date)
        os.unlink(path)


class GLReconciliationBalanceTests(TestCase):
    """Tests for balance explanation logic in build_reconciliation."""

    def setUp(self):
        self.house = House.objects.create(
            name="Test BB", code="BB",
            account_number="13-51200", accounting_code="13",
        )
        self.user = User.objects.create_user(
            username="treasurer_bal", password="x",
            house=self.house, role="TREASURER",
        )
        self.by = BudgetYear.objects.create(
            house=self.house, year=2026,
            annual_budget_total=Decimal("5000"),
            snow_budget=Decimal("0"),
        )
        self.sub = SubBudget.objects.create(
            budget_year=self.by, name="Test",
            trace_code=1, planned_amount=Decimal("5000"),
        )

    def test_balance_ok_when_post_gl_expenses_explain_gap(self):
        """If grille_total - post-GL expenses == gl_total, books balance."""
        from budget.gl_reconciliation import GrandLivreReconciliationService

        # Two expenses in the grille
        exp1 = Expense.objects.create(
            budget_year=self.by, sub_budget=self.sub,
            description="Matched expense", amount=Decimal("100"),
            entry_date=date(2026, 1, 15),
            source_type=ExpenseSourceType.BON_DE_COMMANDE,
            bon_number="BC111",
            spent_by_label="101 / Test",
        )
        # This one was added AFTER the GL period
        exp2 = Expense.objects.create(
            budget_year=self.by, sub_budget=self.sub,
            description="Post-GL expense", amount=Decimal("50"),
            entry_date=date(2026, 3, 10),
            source_type=ExpenseSourceType.BON_DE_COMMANDE,
            bon_number="BC222",
            spent_by_label="101 / Test",
        )

        # Create GL upload with solde_fin = 100 (only exp1 is in GL)
        upload = GrandLivreUpload.objects.create(
            budget_year=self.by,
            uploaded_by=self.user,
            account_number="13-51200",
            status=GLUploadStatus.RECONCILED,
            gl_solde_fin=Decimal("100"),
            period_end_date=date(2026, 3, 3),
            entry_count=1,
        )
        GrandLivreEntry.objects.create(
            upload=upload, row_number=1, date=date(2026, 1, 15),
            description_raw="BC111 test", debit=Decimal("100"),
            credit=Decimal("0"), solde_fin=Decimal("100"),
            matched_expense=exp1,
            match_confidence=GLMatchConfidence.EXACT,
            extracted_bc_number="111",
        )

        result = GrandLivreReconciliationService.build_reconciliation(upload)
        # Should find a "balance_ok_with_pending" anomaly
        types = [a["type"] for a in result.anomalies]
        self.assertIn("balance_ok_with_pending", types)
        # No "balance_mismatch" should appear
        self.assertNotIn("balance_mismatch", types)
        # The explanation should mention post-GL
        explanations = [
            a for a in result.anomalies if a["type"] == "balance_explanation"
        ]
        self.assertTrue(any("après la fin du GL" in e["message"] for e in explanations))

    def test_balance_ok_when_missing_pre_gl_expense_explains_gap(self):
        """A pre-GL expense missing from the accountant file explains the gap."""
        from budget.gl_reconciliation import GrandLivreReconciliationService

        exp1 = Expense.objects.create(
            budget_year=self.by, sub_budget=self.sub,
            description="Bon de commande pour 1 NUBIOCAL 900ML",
            amount=Decimal("19.49"),
            entry_date=date(2026, 1, 7),
            source_type=ExpenseSourceType.BON_DE_COMMANDE,
            bon_number="16739",
            spent_by_label="202 / Marylin Lamarche",
        )
        Expense.objects.create(
            budget_year=self.by, sub_budget=self.sub,
            description="Robinet",
            amount=Decimal("54.54"),
            entry_date=date(2026, 1, 30),
            source_type=ExpenseSourceType.BON_DE_COMMANDE,
            bon_number="17111",
            spent_by_label="202 / Marylin Lamarche",
        )

        upload = GrandLivreUpload.objects.create(
            budget_year=self.by,
            uploaded_by=self.user,
            account_number="13-51200",
            status=GLUploadStatus.RECONCILED,
            gl_solde_fin=Decimal("19.49"),
            period_end_date=date(2026, 3, 3),
            entry_count=1,
        )
        GrandLivreEntry.objects.create(
            upload=upload, row_number=37, date=date(2026, 1, 7),
            source="SANY",
            description_raw="4999081-BC 16739-Nettoyants",
            debit=Decimal("19.49"),
            credit=Decimal("0"),
            matched_expense=exp1,
            match_confidence=GLMatchConfidence.EXACT,
            extracted_bc_number="16739",
        )

        result = GrandLivreReconciliationService.build_reconciliation(upload)
        self.assertTrue(result.is_balanced)
        self.assertEqual(result.difference, Decimal("-54.54"))
        self.assertEqual(result.status_light, "yellow")

        explanations = [
            a["message"] for a in result.anomalies
            if a["type"] == "balance_explanation"
        ]
        self.assertTrue(any("54.54$" in msg for msg in explanations))
        self.assertTrue(any("non encore reflétée" in msg for msg in explanations))
        self.assertFalse(any("Écart résiduel inexpliqué" in msg for msg in explanations))
        self.assertIn(
            "missing_old_expenses",
            [a["type"] for a in result.anomalies],
        )

    def test_balance_mismatch_with_residual(self):
        """Genuine mismatch produces a warning with residual."""
        from budget.gl_reconciliation import GrandLivreReconciliationService

        Expense.objects.create(
            budget_year=self.by, sub_budget=self.sub,
            description="Only expense", amount=Decimal("200"),
            entry_date=date(2026, 1, 15),
            source_type=ExpenseSourceType.BON_DE_COMMANDE,
            spent_by_label="101 / Test",
        )

        upload = GrandLivreUpload.objects.create(
            budget_year=self.by,
            uploaded_by=self.user,
            account_number="13-51200",
            status=GLUploadStatus.RECONCILED,
            gl_solde_fin=Decimal("150"),
            period_end_date=date(2026, 3, 3),
            entry_count=0,
        )

        result = GrandLivreReconciliationService.build_reconciliation(upload)
        types = [a["type"] for a in result.anomalies]
        self.assertIn("balance_mismatch", types)
