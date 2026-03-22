import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.core.exceptions import ValidationError

from houses.models import House
from accounts.models import User
from members.models import Member, Apartment, Residency
from budget.models import BudgetYear, SubBudget, Expense
from bons.models import BonDeCommande, BonStatus, DuplicateFlag, DuplicateFlagStatus
from bons.services import generate_bon_number
from bons.ocr_service import ReceiptOcrService, DuplicateDetectionService
from bons.views import _names_match, _normalize_name, _bon_is_export_ready


class BonNumberGenerationTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(
            code="BB", name="Maison BB", account_number="13-51200"
        )
        self.budget_year = BudgetYear.objects.create(
            house=self.house, year=2026,
            annual_budget_total=Decimal("12237.00")
        )
        self.sub_budget = SubBudget.objects.create(
            budget_year=self.budget_year, trace_code=7,
            name="Produits ménager", planned_amount=Decimal("300.00")
        )
        self.member = Member.objects.create(first_name="Marylin", last_name="Lamarche")
        self.apartment = Apartment.objects.create(house=self.house, code="202")
        Residency.objects.create(
            member=self.member, apartment=self.apartment,
            start_date=date(2020, 1, 1)
        )

    def test_first_bon_number_is_0001(self):
        number = generate_bon_number(self.house, 2026)
        self.assertEqual(number, "BB260001")

    def test_sequential_bon_numbers(self):
        BonDeCommande.objects.create(
            house=self.house, budget_year=self.budget_year,
            number="BB260001", purchase_date=date(2026, 1, 7),
            short_description="Test", total=Decimal("19.49"),
            sub_budget=self.sub_budget, purchaser_member=self.member,
        )
        number = generate_bon_number(self.house, 2026)
        self.assertEqual(number, "BB260002")

    def test_different_years_restart_sequence(self):
        BonDeCommande.objects.create(
            house=self.house, budget_year=self.budget_year,
            number="BB260005", purchase_date=date(2026, 1, 7),
            short_description="Test", total=Decimal("10.00"),
            sub_budget=self.sub_budget, purchaser_member=self.member,
        )
        number = generate_bon_number(self.house, 2027)
        self.assertEqual(number, "BB270001")

    def test_different_houses_independent_sequences(self):
        house2 = House.objects.create(code="DB", name="Maison DB", account_number="16-51200")
        number = generate_bon_number(house2, 2026)
        self.assertEqual(number, "DB260001")


class BonDeCommandeModelTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(code="BB", name="Maison BB", account_number="13-51200")
        self.budget_year = BudgetYear.objects.create(
            house=self.house, year=2026, annual_budget_total=Decimal("12237.00")
        )
        self.sub_budget = SubBudget.objects.create(
            budget_year=self.budget_year, trace_code=7,
            name="Produits ménager", planned_amount=Decimal("300.00")
        )
        self.purchaser = Member.objects.create(first_name="Marylin", last_name="Lamarche")
        self.approver = Member.objects.create(first_name="Carl-David", last_name="Fortin")
        self.apt = Apartment.objects.create(house=self.house, code="202")

    def test_approver_cannot_equal_purchaser(self):
        bon = BonDeCommande(
            house=self.house, budget_year=self.budget_year, number="BB260001",
            purchase_date=date(2026, 1, 7), short_description="Test",
            total=Decimal("19.49"), sub_budget=self.sub_budget,
            purchaser_member=self.purchaser, approver_member=self.purchaser,
        )
        with self.assertRaises(ValidationError):
            bon.full_clean()

    def test_valid_bon_saves_successfully(self):
        bon = BonDeCommande.objects.create(
            house=self.house, budget_year=self.budget_year, number="BB260001",
            purchase_date=date(2026, 1, 7), short_description="Nettoyants",
            total=Decimal("19.49"), sub_budget=self.sub_budget,
            purchaser_member=self.purchaser, approver_member=self.approver,
        )
        self.assertEqual(bon.status, BonStatus.DRAFT)
        self.assertIsNotNone(bon.pk)

    def test_snapshot_fields_captured(self):
        bon = BonDeCommande(
            house=self.house, budget_year=self.budget_year, number="BB260001",
            purchase_date=date(2026, 1, 7), short_description="Test",
            total=Decimal("19.49"), sub_budget=self.sub_budget,
            purchaser_member=self.purchaser, purchaser_apartment=self.apt,
            approver_member=self.approver,
        )
        bon.refresh_snapshot_fields()
        self.assertEqual(bon.purchaser_name_snapshot, "Marylin Lamarche")
        self.assertEqual(bon.purchaser_unit_snapshot, "202")
        self.assertEqual(bon.approver_name_snapshot, "Carl-David Fortin")

    def test_sub_budget_must_match_budget_year(self):
        other_year = BudgetYear.objects.create(
            house=self.house, year=2027, annual_budget_total=Decimal("10000.00")
        )
        other_sb = SubBudget.objects.create(
            budget_year=other_year, trace_code=7,
            name="Produits ménager", planned_amount=Decimal("300.00")
        )
        bon = BonDeCommande(
            house=self.house, budget_year=self.budget_year, number="BB260001",
            purchase_date=date(2026, 1, 7), short_description="Test",
            total=Decimal("10.00"), sub_budget=other_sb,
            purchaser_member=self.purchaser,
        )
        with self.assertRaises(ValidationError):
            bon.full_clean()


class ReceiptOcrParseTests(TestCase):
    """Test GPT receipt analysis response parsing (batch format)."""

    def test_parse_batch_typical(self):
        raw = '[{"filename": "recu1.png", "merchant": "DOLLARAMA", "purchase_date": "2025-01-15", "subtotal": 3.75, "tps": 0.19, "tvq": 0.37, "total": 4.31, "member_name": "Abla", "apartment_number": "207"}]'
        results = ReceiptOcrService._parse_batch_response(raw, ["recu1.png"])
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["filename"], "recu1.png")
        self.assertEqual(r["merchant"], "DOLLARAMA")
        self.assertEqual(r["purchase_date"], date(2025, 1, 15))
        self.assertEqual(r["subtotal"], Decimal("3.75"))
        self.assertEqual(r["tps"], Decimal("0.19"))
        self.assertEqual(r["tvq"], Decimal("0.37"))
        self.assertEqual(r["total"], Decimal("4.31"))
        self.assertEqual(r["member_name"], "Abla")
        self.assertEqual(r["apartment_number"], "207")

    def test_parse_batch_empty(self):
        results = ReceiptOcrService._parse_batch_response("", ["a.png"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["merchant"], "")
        self.assertEqual(results[0]["member_name"], "")
        self.assertIsNone(results[0]["purchase_date"])
        self.assertIsNone(results[0]["total"])

    def test_parse_batch_with_nulls(self):
        raw = '[{"filename": "r.png", "merchant": "METRO", "purchase_date": null, "subtotal": null, "tps": null, "tvq": null, "total": 12.34, "member_name": "ILLISIBLE", "apartment_number": "206"}]'
        results = ReceiptOcrService._parse_batch_response(raw, ["r.png"])
        r = results[0]
        self.assertEqual(r["merchant"], "METRO")
        self.assertIsNone(r["purchase_date"])
        self.assertIsNone(r["subtotal"])
        self.assertEqual(r["total"], Decimal("12.34"))
        self.assertEqual(r["member_name"], "ILLISIBLE")
        self.assertEqual(r["apartment_number"], "206")

    def test_parse_batch_with_code_fences(self):
        raw = '```json\n[{"filename": "r.png", "merchant": "RONA", "purchase_date": "2025-03-01", "subtotal": 45.00, "tps": 2.25, "tvq": 4.49, "total": 51.74, "member_name": "Carl", "apartment_number": "101"}]\n```'
        results = ReceiptOcrService._parse_batch_response(raw, ["r.png"])
        self.assertEqual(results[0]["merchant"], "RONA")
        self.assertEqual(results[0]["tps"], Decimal("2.25"))
        self.assertEqual(results[0]["total"], Decimal("51.74"))

    def test_parse_batch_single_object_fallback(self):
        """GPT might return a single object instead of array for 1 receipt."""
        raw = '{"filename": "x.png", "merchant": "IGA", "total": 9.99, "purchase_date": null, "subtotal": null, "tps": null, "tvq": null, "member_name": "", "apartment_number": ""}'
        results = ReceiptOcrService._parse_batch_response(raw, ["x.png"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["merchant"], "IGA")
        self.assertEqual(results[0]["total"], Decimal("9.99"))

    def test_parse_batch_multiple_receipts(self):
        raw = json.dumps([
            {"filename": "a.png", "merchant": "Metro", "total": 10.00, "purchase_date": None, "subtotal": None, "tps": None, "tvq": None, "member_name": "A", "apartment_number": "101"},
            {"filename": "b.png", "merchant": "IGA", "total": 20.00, "purchase_date": None, "subtotal": None, "tps": None, "tvq": None, "member_name": "B", "apartment_number": "202"},
        ])
        results = ReceiptOcrService._parse_batch_response(raw, ["a.png", "b.png"])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["merchant"], "Metro")
        self.assertEqual(results[1]["merchant"], "IGA")

    def test_is_available_without_key(self):
        with self.settings(OPENAI_API_KEY=""):
            self.assertFalse(ReceiptOcrService.is_available())

    def test_is_available_with_key(self):
        with self.settings(OPENAI_API_KEY="sk-test-key"):
            self.assertTrue(ReceiptOcrService.is_available())

    def test_safe_decimal(self):
        self.assertEqual(ReceiptOcrService._safe_decimal(12.50), Decimal("12.50"))
        self.assertEqual(ReceiptOcrService._safe_decimal("3.75"), Decimal("3.75"))
        self.assertIsNone(ReceiptOcrService._safe_decimal(None))
        self.assertIsNone(ReceiptOcrService._safe_decimal("abc"))

    def test_safe_date(self):
        self.assertEqual(ReceiptOcrService._safe_date("2025-01-15"), date(2025, 1, 15))
        self.assertIsNone(ReceiptOcrService._safe_date(""))
        self.assertIsNone(ReceiptOcrService._safe_date("not-a-date"))
        self.assertIsNone(ReceiptOcrService._safe_date(None))

    def test_parse_paper_bc(self):
        """Paper BC detection: GPT returns document_type=paper_bc with bc_number."""
        raw = json.dumps([
            {
                "filename": "BC16011.pdf - Page 1",
                "document_type": "paper_bc",
                "bc_number": "16011",
                "associated_bc_number": "",
                "supplier_name": "Gicleurs de l'Estrie",
                "supplier_address": "1110 Bélanger, Sherbrooke",
                "member_name": "",
                "apartment_number": "",
                "merchant": "",
                "purchase_date": "2024-11-29",
                "subtotal": 476.56,
                "tps": 23.83,
                "tvq": 47.54,
                "total": 547.93,
                "summary": "Plomberie - installation manomètre",
            },
            {
                "filename": "BC16011.pdf - Page 2",
                "document_type": "invoice",
                "bc_number": "",
                "associated_bc_number": "16011",
                "supplier_name": "Gicleurs de l'Estrie inc.",
                "supplier_address": "",
                "member_name": "",
                "apartment_number": "",
                "merchant": "",
                "purchase_date": "2024-11-29",
                "subtotal": 476.56,
                "tps": 23.83,
                "tvq": 47.54,
                "total": 547.93,
                "summary": "Facture matériaux et main d'oeuvre",
            },
        ])
        results = ReceiptOcrService._parse_batch_response(
            raw, ["BC16011.pdf - Page 1", "BC16011.pdf - Page 2"]
        )
        self.assertEqual(len(results), 2)
        # Paper BC
        self.assertEqual(results[0]["document_type"], "paper_bc")
        self.assertEqual(results[0]["bc_number"], "16011")
        self.assertEqual(results[0]["supplier_name"], "Gicleurs de l'Estrie")
        self.assertEqual(results[0]["total"], Decimal("547.93"))
        # Invoice
        self.assertEqual(results[1]["document_type"], "invoice")
        self.assertEqual(results[1]["associated_bc_number"], "16011")
        self.assertEqual(results[1]["supplier_name"], "Gicleurs de l'Estrie inc.")

    def test_parse_mixed_upload(self):
        """Mixed upload: receipt + paper BC in same batch."""
        raw = json.dumps([
            {
                "filename": "receipt.png",
                "document_type": "receipt",
                "bc_number": "",
                "associated_bc_number": "",
                "supplier_name": "",
                "supplier_address": "",
                "member_name": "Jean Dupont",
                "apartment_number": "305",
                "merchant": "RONA",
                "purchase_date": "2025-03-01",
                "subtotal": 45.00,
                "tps": 2.25,
                "tvq": 4.49,
                "total": 51.74,
                "summary": "Vis et peinture",
            },
            {
                "filename": "BC16739.pdf - Page 1",
                "document_type": "paper_bc",
                "bc_number": "16739",
                "associated_bc_number": "",
                "supplier_name": "Produits Sany",
                "supplier_address": "",
                "member_name": "",
                "apartment_number": "",
                "merchant": "",
                "purchase_date": "2026-01-07",
                "subtotal": 19.49,
                "tps": None,
                "tvq": None,
                "total": 19.49,
                "summary": "NUBIOCAL 900ML",
            },
        ])
        results = ReceiptOcrService._parse_batch_response(
            raw, ["receipt.png", "BC16739.pdf - Page 1"]
        )
        self.assertEqual(len(results), 2)
        # Receipt
        self.assertEqual(results[0]["document_type"], "receipt")
        self.assertEqual(results[0]["member_name"], "Jean Dupont")
        self.assertEqual(results[0]["apartment_number"], "305")
        self.assertEqual(results[0]["merchant"], "RONA")
        # Paper BC
        self.assertEqual(results[1]["document_type"], "paper_bc")
        self.assertEqual(results[1]["bc_number"], "16739")
        self.assertEqual(results[1]["supplier_name"], "Produits Sany")

    def test_parse_defaults_to_receipt(self):
        """Old-format response without document_type defaults to receipt."""
        raw = '[{"filename": "old.png", "merchant": "IGA", "total": 5.00}]'
        results = ReceiptOcrService._parse_batch_response(raw, ["old.png"])
        self.assertEqual(results[0]["document_type"], "receipt")
        self.assertEqual(results[0]["bc_number"], "")
        self.assertEqual(results[0]["associated_bc_number"], "")


class PaperBcFinalizationTests(TestCase):
    """Test that _finalize_bons correctly handles paper BC + invoice + receipt mixes."""

    def setUp(self):
        from bons.models import ReceiptFile, ReceiptExtractedFields
        self.house = House.objects.create(code="BB", name="Maison BB", account_number="13-51200")
        self.budget_year = BudgetYear.objects.create(
            house=self.house, year=2026, annual_budget_total=Decimal("12237.00")
        )
        self.sub_budget = SubBudget.objects.create(
            budget_year=self.budget_year, trace_code=7,
            name="Produits ménager", planned_amount=Decimal("300.00")
        )
        self.member = Member.objects.create(first_name="Marylin", last_name="Lamarche")
        self.apt = Apartment.objects.create(house=self.house, code="202")
        Residency.objects.create(
            member=self.member, apartment=self.apt, start_date=date(2020, 1, 1)
        )
        self.user = User.objects.create_user(
            username="tresorier", password="test123", role=20,
            member=self.member,
        )

        # Create a scan session bon
        self.scan_session = BonDeCommande()
        self.scan_session.house = self.house
        self.scan_session.budget_year = self.budget_year
        self.scan_session.number = "BB260099"
        self.scan_session.purchase_date = date(2026, 1, 7)
        self.scan_session.short_description = "(scan session)"
        self.scan_session.total = 0
        self.scan_session.sub_budget = self.sub_budget
        self.scan_session.purchaser_member = self.member
        self.scan_session.created_by = self.user
        self.scan_session.status = BonStatus.READY_FOR_REVIEW
        self.scan_session.is_scan_session = True
        super(BonDeCommande, self.scan_session).save()

        # Create receipt files attached to scan session
        self.receipt_bc = ReceiptFile.objects.create(
            bon_de_commande=self.scan_session,
            original_filename="BC16011.pdf",
            content_type="application/pdf",
        )
        self.receipt_inv = ReceiptFile.objects.create(
            bon_de_commande=self.scan_session,
            original_filename="facture.png",
            content_type="image/png",
        )
        self.receipt_regular = ReceiptFile.objects.create(
            bon_de_commande=self.scan_session,
            original_filename="recu_rona.jpg",
            content_type="image/jpeg",
        )

        # Extracted fields for paper BC
        ReceiptExtractedFields.objects.create(
            receipt_file=self.receipt_bc,
            document_type_candidate="paper_bc",
            final_document_type="paper_bc",
            bc_number_candidate="16011",
            final_bc_number="16011",
            supplier_name_candidate="Gicleurs de l'Estrie",
            final_supplier_name="Gicleurs de l'Estrie",
            total_candidate=Decimal("547.93"),
            final_total=Decimal("547.93"),
            purchase_date_candidate=date(2024, 11, 29),
            final_purchase_date=date(2024, 11, 29),
            sub_budget=self.sub_budget,
            summary_candidate="Installation purgeur",
            final_summary="Installation purgeur",
        )
        # Extracted fields for invoice linked to paper BC
        ReceiptExtractedFields.objects.create(
            receipt_file=self.receipt_inv,
            document_type_candidate="invoice",
            final_document_type="invoice",
            associated_bc_number_candidate="16011",
            final_associated_bc_number="16011",
            supplier_name_candidate="Gicleurs de l'Estrie inc.",
            final_supplier_name="Gicleurs de l'Estrie inc.",
            total_candidate=Decimal("547.93"),
            final_total=Decimal("547.93"),
            summary_candidate="Travaux plomberie",
            final_summary="Travaux plomberie",
            sub_budget=self.sub_budget,
        )
        # Extracted fields for regular receipt
        ReceiptExtractedFields.objects.create(
            receipt_file=self.receipt_regular,
            document_type_candidate="receipt",
            final_document_type="receipt",
            member_name_candidate="Marylin Lamarche",
            final_member_name="Marylin Lamarche",
            apartment_number_candidate="202",
            final_apartment_number="202",
            merchant_candidate="RONA",
            final_merchant="RONA",
            total_candidate=Decimal("25.00"),
            final_total=Decimal("25.00"),
            summary_candidate="Vis et peinture",
            final_summary="Vis et peinture",
            sub_budget=self.sub_budget,
        )

    def test_finalize_creates_paper_bc_and_regular_bons(self):
        """Mixed upload: paper BC with invoice + regular receipt → 2 bons."""
        from django.test import RequestFactory
        from bons.views import OcrReviewView

        factory = RequestFactory()
        request = factory.get("/")
        request.user = self.user
        # Attach message storage
        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(request, "session", "session")
        setattr(request, "_messages", FallbackStorage(request))

        view = OcrReviewView()
        response = view._finalize_bons(request, self.scan_session)

        # Should have created 2 bons: 1 paper BC + 1 regular
        created_bons = BonDeCommande.objects.filter(
            is_scan_session=False
        ).exclude(status=BonStatus.VOID)
        self.assertEqual(created_bons.count(), 2)

        # Paper BC bon
        paper_bon = created_bons.filter(is_paper_bc=True).first()
        self.assertIsNotNone(paper_bon)
        self.assertEqual(paper_bon.number, "16011")
        self.assertEqual(paper_bon.paper_bc_number, "16011")
        self.assertEqual(paper_bon.status, BonStatus.READY_FOR_VALIDATION)
        # Paper BC bon should have 2 receipt files (BC + invoice)
        self.assertEqual(paper_bon.receipt_files.count(), 2)

        # Regular bon
        regular_bon = created_bons.filter(is_paper_bc=False).first()
        self.assertIsNotNone(regular_bon)
        self.assertTrue(regular_bon.number.startswith("BB26"))
        self.assertEqual(regular_bon.receipt_files.count(), 1)

        # Scan session should be voided
        self.scan_session.refresh_from_db()
        self.assertEqual(self.scan_session.status, BonStatus.VOID)

    def test_finalize_handles_duplicate_paper_bc_number(self):
        """If a paper BC number already exists, append suffix."""
        # Create an existing bon with number 16011
        BonDeCommande.objects.create(
            house=self.house, budget_year=self.budget_year,
            number="16011", purchase_date=date(2026, 1, 1),
            short_description="Existing", total=Decimal("100"),
            sub_budget=self.sub_budget, purchaser_member=self.member,
        )

        from django.test import RequestFactory
        from bons.views import OcrReviewView
        from django.contrib.messages.storage.fallback import FallbackStorage

        factory = RequestFactory()
        request = factory.get("/")
        request.user = self.user
        setattr(request, "session", "session")
        setattr(request, "_messages", FallbackStorage(request))

        view = OcrReviewView()
        view._finalize_bons(request, self.scan_session)

        # Should use suffix since 16011 is taken
        paper_bon = BonDeCommande.objects.filter(
            is_paper_bc=True, is_scan_session=False
        ).exclude(status=BonStatus.VOID).first()
        self.assertIsNotNone(paper_bon)
        self.assertEqual(paper_bon.number, "16011-2")
        self.assertEqual(paper_bon.paper_bc_number, "16011")

    def test_orphan_invoices_grouped_as_regular(self):
        """Invoices with no matching paper BC are treated as regular receipts."""
        from bons.models import ReceiptExtractedFields
        # Change the invoice to point to a non-existent BC
        ef = self.receipt_inv.extracted_fields
        ef.final_associated_bc_number = "99999"
        ef.associated_bc_number_candidate = "99999"
        ef.save()

        from django.test import RequestFactory
        from bons.views import OcrReviewView
        from django.contrib.messages.storage.fallback import FallbackStorage

        factory = RequestFactory()
        request = factory.get("/")
        request.user = self.user
        setattr(request, "session", "session")
        setattr(request, "_messages", FallbackStorage(request))

        view = OcrReviewView()
        view._finalize_bons(request, self.scan_session)

        # Paper BC bon (BC alone, no matched invoices)
        paper_bon = BonDeCommande.objects.filter(
            is_paper_bc=True, is_scan_session=False
        ).exclude(status=BonStatus.VOID).first()
        self.assertIsNotNone(paper_bon)
        self.assertEqual(paper_bon.receipt_files.count(), 1)  # Just the BC

        # Regular bons should have the orphan invoice + regular receipt
        regular_bons = BonDeCommande.objects.filter(
            is_paper_bc=False, is_scan_session=False
        ).exclude(status=BonStatus.VOID)
        total_receipts = sum(b.receipt_files.count() for b in regular_bons)
        self.assertEqual(total_receipts, 2)  # invoice + receipt


class MismatchWarningTests(TestCase):
    """Test the _get_mismatch_warning helper."""

    def test_matching_totals_no_warning(self):
        from bons.views import _get_mismatch_warning
        from unittest.mock import MagicMock

        receipt = MagicMock()
        receipt.ocr_raw_text = json.dumps([
            {"document_type": "paper_bc", "bc_number": "16011", "total": 547.93},
            {"document_type": "invoice", "associated_bc_number": "16011", "total": 547.93},
        ])
        self.assertIsNone(_get_mismatch_warning(receipt))

    def test_mismatched_totals_returns_warning(self):
        from bons.views import _get_mismatch_warning
        from unittest.mock import MagicMock

        receipt = MagicMock()
        receipt.ocr_raw_text = json.dumps([
            {"document_type": "paper_bc", "bc_number": "16739", "total": 19.49},
            {"document_type": "invoice", "associated_bc_number": "16739", "total": 16.95},
        ])
        warning = _get_mismatch_warning(receipt)
        self.assertIsNotNone(warning)
        self.assertEqual(warning["bc_number"], "16739")
        self.assertAlmostEqual(float(warning["bc_total"]), 19.49)
        self.assertAlmostEqual(float(warning["invoice_total"]), 16.95)

    def test_no_paper_bc_no_warning(self):
        from bons.views import _get_mismatch_warning
        from unittest.mock import MagicMock

        receipt = MagicMock()
        receipt.ocr_raw_text = json.dumps([
            {"document_type": "receipt", "total": 25.00},
        ])
        self.assertIsNone(_get_mismatch_warning(receipt))


class FuzzyNameMatchTests(TestCase):
    """Test _names_match and _normalize_name for case/accent-insensitive matching."""

    def test_exact_match(self):
        self.assertTrue(_names_match("Marylin Lamarche", "Marylin Lamarche"))

    def test_case_insensitive(self):
        self.assertTrue(_names_match("MARYLINE LAMARCHE", "Marylin Lamarche"))

    def test_spelling_variation(self):
        # MARYLINE vs Marylin — one extra letter
        self.assertTrue(_names_match("MARYLINE LAMARCHE", "Marylin Lamarche"))

    def test_accented_names(self):
        self.assertTrue(_names_match("René Lévesque", "rene levesque"))
        self.assertTrue(_names_match("HÉLOÏSE CÔTÉ", "Heloise Cote"))

    def test_substring_match(self):
        self.assertTrue(_names_match("Carl-David", "Carl-David Fortin"))

    def test_completely_different_names(self):
        self.assertFalse(_names_match("Jean Tremblay", "Marie Bouchard"))

    def test_empty_strings(self):
        self.assertFalse(_names_match("", "Someone"))
        self.assertFalse(_names_match("Someone", ""))
        self.assertFalse(_names_match("", ""))

    def test_normalize_strips_accents(self):
        self.assertEqual(_normalize_name("Héloïse Côté"), "heloise cote")
        self.assertEqual(_normalize_name("  CARL-DAVID  FORTIN  "), "carl-david fortin")


# ═══════════════════════════════════════════════════════════════════════════
# Phase 10 Tests: Duplicate Detection, Export Gating, Audit Trail
# ═══════════════════════════════════════════════════════════════════════════

class DuplicateDetectionBaseTest(TestCase):
    """Base setup for duplicate detection tests."""

    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from bons.models import ReceiptFile, ReceiptExtractedFields, OcrStatus

        self.house = House.objects.create(
            code="BB", name="Maison BB", account_number="13-51200"
        )
        self.budget_year = BudgetYear.objects.create(
            house=self.house, year=2026,
            annual_budget_total=Decimal("12237.00")
        )
        self.sub_budget = SubBudget.objects.create(
            budget_year=self.budget_year, trace_code=7,
            name="Produits ménager", planned_amount=Decimal("300.00")
        )
        self.member = Member.objects.create(first_name="Marylin", last_name="Lamarche")
        self.apartment = Apartment.objects.create(house=self.house, code="202")
        Residency.objects.create(
            member=self.member, apartment=self.apartment,
            start_date=date(2020, 1, 1)
        )
        self.user = User.objects.create_user(
            username="tresorier", password="test123",
            role="TREASURER", house=self.house, member=self.member,
        )

        # Create existing bon with a receipt
        self.existing_bon = BonDeCommande.objects.create(
            house=self.house, budget_year=self.budget_year,
            number="BB260001", purchase_date=date(2026, 1, 7),
            short_description="Existing purchase", total=Decimal("19.49"),
            sub_budget=self.sub_budget, purchaser_member=self.member,
            status=BonStatus.VALIDATED,
        )
        fake_file = SimpleUploadedFile("receipt1.png", b"fake image data", content_type="image/png")
        self.existing_receipt = ReceiptFile.objects.create(
            bon_de_commande=self.existing_bon,
            file=fake_file,
            original_filename="receipt1.png",
            content_type="image/png",
            ocr_status=OcrStatus.EXTRACTED,
        )
        self.existing_ef = ReceiptExtractedFields.objects.create(
            receipt_file=self.existing_receipt,
            total_candidate=Decimal("19.49"),
            final_total=Decimal("19.49"),
        )

        # Create new bon with a receipt (same total)
        self.new_bon = BonDeCommande.objects.create(
            house=self.house, budget_year=self.budget_year,
            number="BB260002", purchase_date=date(2026, 3, 15),
            short_description="New purchase", total=Decimal("19.49"),
            sub_budget=self.sub_budget, purchaser_member=self.member,
            status=BonStatus.READY_FOR_VALIDATION,
        )
        fake_file2 = SimpleUploadedFile("receipt2.png", b"fake image data 2", content_type="image/png")
        self.new_receipt = ReceiptFile.objects.create(
            bon_de_commande=self.new_bon,
            file=fake_file2,
            original_filename="receipt2.png",
            content_type="image/png",
            ocr_status=OcrStatus.EXTRACTED,
        )
        self.new_ef = ReceiptExtractedFields.objects.create(
            receipt_file=self.new_receipt,
            total_candidate=Decimal("19.49"),
            final_total=Decimal("19.49"),
        )


class DigitalInvoiceDuplicateTests(DuplicateDetectionBaseTest):
    """Test digital invoice duplicate detection via matching totals."""

    def test_find_matching_totals_same_total(self):
        matches = DuplicateDetectionService.find_matching_totals(
            self.new_receipt, self.house
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].receipt_file, self.existing_receipt)

    def test_find_matching_totals_different_total(self):
        self.existing_ef.final_total = Decimal("99.99")
        self.existing_ef.total_candidate = Decimal("99.99")
        self.existing_ef.save()
        matches = DuplicateDetectionService.find_matching_totals(
            self.new_receipt, self.house
        )
        self.assertEqual(len(matches), 0)

    def test_find_matching_totals_different_house(self):
        other_house = House.objects.create(code="XX", name="Other", account_number="99-99999")
        matches = DuplicateDetectionService.find_matching_totals(
            self.new_receipt, other_house
        )
        self.assertEqual(len(matches), 0)

    @patch.object(DuplicateDetectionService, "compare_with_gpt")
    def test_check_and_flag_creates_flag(self, mock_gpt):
        mock_gpt.return_value = {
            "is_same_purchase": True,
            "confidence": 0.95,
            "reasoning": "Same merchant, date, and amount",
        }
        flags = DuplicateDetectionService.check_and_flag_duplicates(
            self.new_receipt, self.house
        )
        self.assertEqual(len(flags), 1)
        flag = flags[0]
        self.assertEqual(flag.receipt_file, self.new_receipt)
        self.assertEqual(flag.suspected_duplicate_receipt, self.existing_receipt)
        self.assertEqual(flag.confidence, Decimal("0.95"))
        self.assertEqual(flag.status, DuplicateFlagStatus.CONFIRMED_DUPLICATE)

    @patch.object(DuplicateDetectionService, "compare_with_gpt")
    def test_check_and_flag_pending_if_low_confidence(self, mock_gpt):
        mock_gpt.return_value = {
            "is_same_purchase": True,
            "confidence": 0.60,
            "reasoning": "Similar amounts but different dates",
        }
        flags = DuplicateDetectionService.check_and_flag_duplicates(
            self.new_receipt, self.house
        )
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].status, DuplicateFlagStatus.PENDING)

    @patch.object(DuplicateDetectionService, "compare_with_gpt")
    def test_no_duplicate_flag_for_same_receipt(self, mock_gpt):
        """Should not flag a receipt against itself."""
        mock_gpt.return_value = {
            "is_same_purchase": True, "confidence": 1.0, "reasoning": "Same"
        }
        matches = DuplicateDetectionService.find_matching_totals(
            self.existing_receipt, self.house
        )
        # The existing receipt should match the new one, not itself
        self.assertNotIn(
            self.existing_receipt,
            [m.receipt_file for m in matches]
        )

    @patch.object(DuplicateDetectionService, "compare_with_gpt")
    def test_no_double_flagging(self, mock_gpt):
        mock_gpt.return_value = {
            "is_same_purchase": True, "confidence": 0.95, "reasoning": "Dup"
        }
        # First call creates flags
        flags1 = DuplicateDetectionService.check_and_flag_duplicates(
            self.new_receipt, self.house
        )
        self.assertEqual(len(flags1), 1)

        # Second call should not create duplicates
        flags2 = DuplicateDetectionService.check_and_flag_duplicates(
            self.new_receipt, self.house
        )
        self.assertEqual(len(flags2), 0)


class PaperBcDuplicateTests(DuplicateDetectionBaseTest):
    """Test paper BC duplicate detection at finalization."""

    def test_paper_bc_same_number_same_total_blocks(self):
        """If a paper BC with the same number and total exists, it should be blocked."""
        self.existing_bon.is_paper_bc = True
        self.existing_bon.paper_bc_number = "16011"
        BonDeCommande.objects.filter(pk=self.existing_bon.pk).update(
            is_paper_bc=True, paper_bc_number="16011"
        )

        existing_dup = BonDeCommande.objects.filter(
            house=self.house,
            is_paper_bc=True,
            paper_bc_number="16011",
        ).exclude(status=BonStatus.VOID).first()

        self.assertIsNotNone(existing_dup)
        self.assertEqual(existing_dup.total, Decimal("19.49"))

    def test_paper_bc_voided_not_detected(self):
        """Voided paper BCs should not block new ones."""
        BonDeCommande.objects.filter(pk=self.existing_bon.pk).update(
            is_paper_bc=True, paper_bc_number="16011", status=BonStatus.VOID,
        )

        existing_dup = BonDeCommande.objects.filter(
            house=self.house,
            is_paper_bc=True,
            paper_bc_number="16011",
        ).exclude(status=BonStatus.VOID).first()

        self.assertIsNone(existing_dup)


class ExportGatingTests(DuplicateDetectionBaseTest):
    """Test that exports are blocked when receipts haven't been reviewed."""

    def test_export_ready_when_all_reviewed(self):
        self.assertTrue(_bon_is_export_ready(self.existing_bon))

    def test_export_not_ready_when_pending(self):
        from bons.models import ReceiptFile, OcrStatus
        from django.core.files.uploadedfile import SimpleUploadedFile
        fake = SimpleUploadedFile("unreviewed.png", b"data", content_type="image/png")
        ReceiptFile.objects.create(
            bon_de_commande=self.existing_bon,
            file=fake, original_filename="unreviewed.png",
            content_type="image/png",
            ocr_status=OcrStatus.PENDING,
        )
        self.assertFalse(_bon_is_export_ready(self.existing_bon))

    def test_export_ready_no_receipts(self):
        empty_bon = BonDeCommande.objects.create(
            house=self.house, budget_year=self.budget_year,
            number="BB260099", purchase_date=date(2026, 1, 1),
            short_description="Empty bon", total=Decimal("0.00"),
            sub_budget=self.sub_budget, purchaser_member=self.member,
        )
        self.assertTrue(_bon_is_export_ready(empty_bon))

    def test_export_pdf_blocked_when_not_ready(self):
        from bons.models import ReceiptFile, OcrStatus
        from django.core.files.uploadedfile import SimpleUploadedFile
        fake = SimpleUploadedFile("pending.png", b"data", content_type="image/png")
        ReceiptFile.objects.create(
            bon_de_commande=self.existing_bon,
            file=fake, original_filename="pending.png",
            content_type="image/png",
            ocr_status=OcrStatus.NOT_REQUESTED,
        )
        self.client.login(username="tresorier", password="test123")
        resp = self.client.get(f"/bons/{self.existing_bon.pk}/pdf/")
        self.assertEqual(resp.status_code, 302)  # redirect back to detail

    def test_export_xlsx_blocked_when_not_ready(self):
        from bons.models import ReceiptFile, OcrStatus
        from django.core.files.uploadedfile import SimpleUploadedFile
        fake = SimpleUploadedFile("pending.png", b"data", content_type="image/png")
        ReceiptFile.objects.create(
            bon_de_commande=self.existing_bon,
            file=fake, original_filename="pending.png",
            content_type="image/png",
            ocr_status=OcrStatus.NOT_REQUESTED,
        )
        self.client.login(username="tresorier", password="test123")
        resp = self.client.get(f"/bons/{self.existing_bon.pk}/xlsx/")
        self.assertEqual(resp.status_code, 302)  # redirect back to detail


class AuditTrailTests(DuplicateDetectionBaseTest):
    """Test that edits create audit log entries."""

    def test_edit_creates_audit_entry(self):
        from audits.models import AuditLogEntry

        self.client.login(username="tresorier", password="test123")

        # Set bon to editable status
        BonDeCommande.objects.filter(pk=self.existing_bon.pk).update(
            status=BonStatus.READY_FOR_VALIDATION,
        )
        self.existing_bon.refresh_from_db()

        resp = self.client.post(f"/bons/{self.existing_bon.pk}/edit/", {
            "budget_year": self.budget_year.pk,
            "purchase_date": "2026-01-08",
            "short_description": "Updated description",
            "total": "25.00",
            "sub_budget": self.sub_budget.pk,
            "purchaser_member": self.member.pk,
        })
        self.assertEqual(resp.status_code, 302)

        entries = AuditLogEntry.objects.filter(
            target_app_label="bons",
            target_model="bondecommande",
            target_object_id=str(self.existing_bon.pk),
            action="bon.edited",
        )
        self.assertTrue(entries.exists())
        entry = entries.first()
        self.assertIn("short_description", entry.summary)

    def test_no_audit_entry_when_nothing_changed(self):
        from audits.models import AuditLogEntry

        self.client.login(username="tresorier", password="test123")

        BonDeCommande.objects.filter(pk=self.existing_bon.pk).update(
            status=BonStatus.READY_FOR_VALIDATION,
        )
        self.existing_bon.refresh_from_db()

        # Submit the form with the same values
        resp = self.client.post(f"/bons/{self.existing_bon.pk}/edit/", {
            "budget_year": self.budget_year.pk,
            "purchase_date": str(self.existing_bon.purchase_date),
            "short_description": self.existing_bon.short_description,
            "total": str(self.existing_bon.total),
            "sub_budget": self.sub_budget.pk,
            "purchaser_member": self.member.pk,
        })
        self.assertEqual(resp.status_code, 302)

        entries = AuditLogEntry.objects.filter(
            target_app_label="bons",
            target_model="bondecommande",
            target_object_id=str(self.existing_bon.pk),
            action="bon.edited",
        )
        self.assertFalse(entries.exists())


class DuplicateFlagResolveTests(DuplicateDetectionBaseTest):
    """Test duplicate flag resolution."""

    def setUp(self):
        super().setUp()
        self.flag = DuplicateFlag.objects.create(
            receipt_file=self.new_receipt,
            suspected_duplicate_receipt=self.existing_receipt,
            confidence=Decimal("0.95"),
            gpt_comparison_result="Same merchant and amount",
            status=DuplicateFlagStatus.PENDING,
        )

    def test_dismiss_duplicate_flag(self):
        self.client.login(username="tresorier", password="test123")
        resp = self.client.post(
            f"/bons/duplicates/{self.flag.pk}/resolve/",
            {"action": "dismiss"},
        )
        self.assertEqual(resp.status_code, 302)
        self.flag.refresh_from_db()
        self.assertEqual(self.flag.status, DuplicateFlagStatus.DISMISSED)
        self.assertIsNotNone(self.flag.resolved_at)

    def test_confirm_duplicate_flag(self):
        self.client.login(username="tresorier", password="test123")
        resp = self.client.post(
            f"/bons/duplicates/{self.flag.pk}/resolve/",
            {"action": "confirm"},
        )
        self.assertEqual(resp.status_code, 302)
        self.flag.refresh_from_db()
        self.assertEqual(self.flag.status, DuplicateFlagStatus.CONFIRMED_DUPLICATE)


class DetailViewDuplicateTests(DuplicateDetectionBaseTest):
    """Test that the detail view shows duplicate warnings and export gating."""

    def test_detail_shows_duplicate_flag(self):
        DuplicateFlag.objects.create(
            receipt_file=self.new_receipt,
            suspected_duplicate_receipt=self.existing_receipt,
            confidence=Decimal("0.95"),
            gpt_comparison_result="Same merchant",
            status=DuplicateFlagStatus.PENDING,
        )
        self.client.login(username="tresorier", password="test123")
        resp = self.client.get(f"/bons/{self.new_bon.pk}/")
        self.assertContains(resp, "DOUBLON POSSIBLE")

    def test_detail_hides_dismissed_flags(self):
        DuplicateFlag.objects.create(
            receipt_file=self.new_receipt,
            suspected_duplicate_receipt=self.existing_receipt,
            confidence=Decimal("0.95"),
            gpt_comparison_result="Same merchant",
            status=DuplicateFlagStatus.DISMISSED,
        )
        self.client.login(username="tresorier", password="test123")
        resp = self.client.get(f"/bons/{self.new_bon.pk}/")
        self.assertNotContains(resp, "DOUBLON POSSIBLE")

    def test_detail_shows_export_links_when_ready(self):
        self.client.login(username="tresorier", password="test123")
        resp = self.client.get(f"/bons/{self.existing_bon.pk}/")
        self.assertContains(resp, "📄 PDF")
        self.assertContains(resp, "📊 Excel")
        # Links should be active (not greyed out)
        self.assertContains(resp, f'href="/bons/{self.existing_bon.pk}/pdf/"')

    def test_detail_disables_export_when_not_ready(self):
        from bons.models import ReceiptFile, OcrStatus
        from django.core.files.uploadedfile import SimpleUploadedFile
        fake = SimpleUploadedFile("pending.png", b"data", content_type="image/png")
        ReceiptFile.objects.create(
            bon_de_commande=self.existing_bon,
            file=fake, original_filename="pending.png",
            content_type="image/png",
            ocr_status=OcrStatus.PENDING,
        )
        self.client.login(username="tresorier", password="test123")
        resp = self.client.get(f"/bons/{self.existing_bon.pk}/")
        # Should show greyed-out export buttons
        self.assertContains(resp, "cursor:not-allowed")
