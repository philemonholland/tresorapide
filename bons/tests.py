from datetime import date
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.core.exceptions import ValidationError

from houses.models import House
from accounts.models import User
from members.models import Member, Apartment, Residency
from budget.models import BudgetYear, SubBudget, Expense
from bons.models import BonDeCommande, BonStatus
from bons.services import generate_bon_number
from bons.ocr_service import ReceiptOcrService


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
    """Test OCR text parsing without requiring Tesseract binary."""

    def test_parse_typical_receipt(self):
        raw = """
DOLLARAMA
123 Rue Principale
Montréal QC

2025-01-15

Savon       2.50
Éponge      1.25

Sous-total   3.75
TPS          0.19
TVQ          0.37
Total        4.31
"""
        result = ReceiptOcrService.parse_receipt_text(raw)
        self.assertEqual(result["merchant"], "DOLLARAMA")
        self.assertEqual(result["purchase_date"], date(2025, 1, 15))
        self.assertEqual(result["subtotal"], Decimal("3.75"))
        self.assertEqual(result["tps"], Decimal("0.19"))
        self.assertEqual(result["tvq"], Decimal("0.37"))
        self.assertEqual(result["total"], Decimal("4.31"))

    def test_parse_empty_text(self):
        result = ReceiptOcrService.parse_receipt_text("")
        self.assertEqual(result["merchant"], "")
        self.assertIsNone(result["purchase_date"])
        self.assertIsNone(result["subtotal"])
        self.assertIsNone(result["tps"])
        self.assertIsNone(result["tvq"])
        self.assertIsNone(result["total"])

    def test_parse_total_only(self):
        raw = """
CANADIAN TIRE
Total  $29.99
"""
        result = ReceiptOcrService.parse_receipt_text(raw)
        self.assertEqual(result["merchant"], "CANADIAN TIRE")
        self.assertEqual(result["total"], Decimal("29.99"))
        self.assertIsNone(result["subtotal"])

    def test_parse_date_slash_format(self):
        raw = """
METRO
15/01/2025
Total 12.34
"""
        result = ReceiptOcrService.parse_receipt_text(raw)
        self.assertEqual(result["purchase_date"], date(2025, 1, 15))

    def test_parse_gst_qst_labels(self):
        raw = """
RONA
Subtotal  45.00
GST       2.25
QST       4.49
Total     51.74
"""
        result = ReceiptOcrService.parse_receipt_text(raw)
        self.assertEqual(result["tps"], Decimal("2.25"))
        self.assertEqual(result["tvq"], Decimal("4.49"))
        self.assertEqual(result["total"], Decimal("51.74"))

    def test_parse_amount_with_dollar_sign(self):
        raw = """
SHOP
Total $ 9.99
"""
        result = ReceiptOcrService.parse_receipt_text(raw)
        self.assertEqual(result["total"], Decimal("9.99"))

    def test_is_available_without_tesseract(self):
        with patch("bons.ocr_service.TESSERACT_AVAILABLE", False):
            self.assertFalse(ReceiptOcrService.is_available())

    @patch("bons.ocr_service.TESSERACT_AVAILABLE", True)
    @patch("bons.ocr_service.pytesseract")
    def test_is_available_with_tesseract(self, mock_pytesseract):
        mock_pytesseract.get_tesseract_version.return_value = "5.0.0"
        self.assertTrue(ReceiptOcrService.is_available())

    def test_parse_amount_helper(self):
        self.assertEqual(ReceiptOcrService._parse_amount("$12.50"), Decimal("12.50"))
        self.assertEqual(ReceiptOcrService._parse_amount("3,75"), Decimal("3.75"))
        self.assertIsNone(ReceiptOcrService._parse_amount(""))
        self.assertIsNone(ReceiptOcrService._parse_amount("abc"))

    def test_parse_date_helper(self):
        self.assertEqual(ReceiptOcrService._parse_date("2025-01-15"), date(2025, 1, 15))
        self.assertEqual(ReceiptOcrService._parse_date("2025/01/15"), date(2025, 1, 15))
        self.assertIsNone(ReceiptOcrService._parse_date(""))
        self.assertIsNone(ReceiptOcrService._parse_date("not-a-date"))
