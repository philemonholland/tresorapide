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
