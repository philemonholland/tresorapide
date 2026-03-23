"""Tests for Grand Livre parser, reconciliation, and views."""
import os
import tempfile
from datetime import date
from decimal import Decimal

from django.test import TestCase, RequestFactory
from django.core.files.uploadedfile import SimpleUploadedFile

import openpyxl

from accounts.models import User
from houses.models import House
from members.models import Member
from budget.models import (
    BudgetYear, SubBudget, Expense, ExpenseSourceType,
    GrandLivreUpload, GrandLivreEntry, ReconciliationResult,
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
        self.assertEqual(entries[0].match_confidence, GLMatchConfidence.EXACT)
        self.assertIsNone(entries[1].matched_expense)

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
        self.assertEqual(expense.spent_by_label, "CHCE")
        self.assertEqual(expense.display_approved_by_label, "CHCE")


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
