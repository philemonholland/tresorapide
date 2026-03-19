from datetime import date
from decimal import Decimal
from django.test import TestCase

from houses.models import House
from budget.models import BudgetYear, SubBudget, Expense
from budget.services import BudgetCalculationService


class BudgetYearAutoContingencyTests(TestCase):
    def test_imprevues_sub_budget_auto_created(self):
        house = House.objects.create(code="BB", name="Maison BB", account_number="13-51200")
        by = BudgetYear.objects.create(
            house=house, year=2026, annual_budget_total=Decimal("12237.00")
        )
        imprevues = by.sub_budgets.filter(trace_code=0).first()
        self.assertIsNotNone(imprevues)
        self.assertTrue(imprevues.is_contingency)
        self.assertEqual(imprevues.planned_amount, Decimal("12237.00") * Decimal("0.15"))
        self.assertEqual(imprevues.name, "Imprévues")


class BudgetCalculationTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(code="BB", name="Maison BB", account_number="13-51200")
        self.by = BudgetYear.objects.create(
            house=self.house, year=2026,
            annual_budget_total=Decimal("12237.00"),
            snow_budget=Decimal("1860.00"),
        )
        # Get the auto-created imprévues sub-budget
        self.sb_imprevues = self.by.sub_budgets.get(trace_code=0)
        self.sb_repairs_apt = SubBudget.objects.create(
            budget_year=self.by, trace_code=1,
            name="Réparations par appartement", planned_amount=Decimal("2000.00")
        )
        self.sb_repairs_bb = SubBudget.objects.create(
            budget_year=self.by, trace_code=2,
            name="Réparations BB", planned_amount=Decimal("1000.00")
        )
        self.sb_produits = SubBudget.objects.create(
            budget_year=self.by, trace_code=7,
            name="Produits ménager", planned_amount=Decimal("300.00")
        )

    def test_base_values_no_expenses(self):
        vals = BudgetCalculationService.base_values(self.by)
        self.assertEqual(vals["budget_total"], Decimal("12237.00"))
        self.assertEqual(vals["snow_budget"], Decimal("1860.00"))
        self.assertEqual(vals["imprevues"], Decimal("12237.00") * Decimal("0.15"))
        self.assertEqual(vals["expenses_to_date"], Decimal("0"))

    def test_base_values_with_expenses(self):
        Expense.objects.create(
            budget_year=self.by, sub_budget=self.sb_produits,
            entry_date=date(2026, 1, 7), description="Nettoyants",
            amount=Decimal("19.49"), spent_by_label="202 / Marylin"
        )
        vals = BudgetCalculationService.base_values(self.by)
        self.assertEqual(vals["expenses_to_date"], Decimal("19.49"))

    def test_repair_totals(self):
        Expense.objects.create(
            budget_year=self.by, sub_budget=self.sb_repairs_apt,
            entry_date=date(2026, 2, 26), description="Robinet",
            amount=Decimal("54.54"), spent_by_label="304 / Serge"
        )
        totals = BudgetCalculationService.repair_totals(self.by)
        self.assertEqual(totals["planned"], Decimal("3000.00"))
        self.assertEqual(totals["used"], Decimal("54.54"))
        self.assertEqual(totals["remaining"], Decimal("2945.46"))

    def test_imprevues_totals(self):
        Expense.objects.create(
            budget_year=self.by, sub_budget=self.sb_imprevues,
            entry_date=date(2026, 1, 30), description="Pouliot repair",
            amount=Decimal("564.92"), spent_by_label="BB",
            source_type="accountant_direct"
        )
        totals = BudgetCalculationService.imprevues_totals(self.by)
        self.assertEqual(totals["used"], Decimal("564.92"))
        expected_remaining = Decimal("12237.00") * Decimal("0.15") - Decimal("564.92")
        self.assertEqual(totals["remaining"], expected_remaining)

    def test_available_money(self):
        Expense.objects.create(
            budget_year=self.by, sub_budget=self.sb_produits,
            entry_date=date(2026, 1, 7), description="Nettoyants",
            amount=Decimal("19.49"), spent_by_label="202 / Marylin"
        )
        Expense.objects.create(
            budget_year=self.by, sub_budget=self.sb_imprevues,
            entry_date=date(2026, 1, 30), description="Pouliot",
            amount=Decimal("564.92"), spent_by_label="BB",
            source_type="accountant_direct"
        )
        avail = BudgetCalculationService.available_money(self.by)
        self.assertEqual(avail["available"], Decimal("12237.00") - Decimal("19.49") - Decimal("564.92"))

    def test_category_summary(self):
        Expense.objects.create(
            budget_year=self.by, sub_budget=self.sb_produits,
            entry_date=date(2026, 1, 7), description="Nettoyants",
            amount=Decimal("19.49"), spent_by_label="202 / Marylin"
        )
        summary = BudgetCalculationService.category_summary(self.by)
        produits = [s for s in summary if s["trace_code"] == 7][0]
        self.assertEqual(produits["used"], Decimal("19.49"))
        self.assertEqual(produits["remaining"], Decimal("300.00") - Decimal("19.49"))

    def test_running_balances(self):
        Expense.objects.create(
            budget_year=self.by, sub_budget=self.sb_produits,
            entry_date=date(2026, 1, 7), description="Nettoyants",
            amount=Decimal("19.49"), spent_by_label="202 / Marylin"
        )
        Expense.objects.create(
            budget_year=self.by, sub_budget=self.sb_imprevues,
            entry_date=date(2026, 1, 30), description="Pouliot",
            amount=Decimal("564.92"), spent_by_label="BB"
        )
        balances = BudgetCalculationService.running_balances(self.by)
        self.assertEqual(len(balances), 2)
        self.assertEqual(balances[0]["balance"], Decimal("12237.00") - Decimal("19.49"))
        self.assertEqual(balances[1]["balance"], Decimal("12237.00") - Decimal("19.49") - Decimal("564.92"))
        budget_minus_15 = Decimal("12237.00") - Decimal("12237.00") * Decimal("0.15")
        self.assertEqual(balances[0]["balance_minus_imprevues"], budget_minus_15 - Decimal("19.49"))
        self.assertEqual(balances[1]["balance_minus_imprevues"], budget_minus_15 - Decimal("19.49"))

    def test_unbudgeted_available(self):
        vals = BudgetCalculationService.unbudgeted_available(self.by)
        # budget_total=12237, imprevues=12237*0.15=1835.55
        # planned non-contingency: 2000 + 1000 + 300 = 3300
        # unbudgeted = 12237 - 3300 - 1835.55 = 7101.45
        imprevues = Decimal("12237.00") * Decimal("0.15")
        expected = Decimal("12237.00") - Decimal("3300.00") - imprevues
        self.assertEqual(vals["unbudgeted_available"], expected)
        # minus_15 = (12237 - imprevues) - 3300
        expected_minus_15 = Decimal("12237.00") - imprevues - Decimal("3300.00")
        self.assertEqual(vals["unbudgeted_available_minus_15"], expected_minus_15)
