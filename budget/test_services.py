from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase

from budget.models import BudgetCategory, BudgetYear
from budget.services import BudgetRollupService
from members.models import Apartment, Member, Residency
from reimbursements.models import Reimbursement, ReimbursementStatus


class BudgetRollupServiceTests(TestCase):
    def test_sync_year_and_category_totals_ignore_voided_reimbursements(self) -> None:
        budget_year = BudgetYear.objects.create(
            label="FY2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        category = BudgetCategory.objects.create(
            budget_year=budget_year,
            code="ops",
            name="Operations",
        )
        apartment = Apartment.objects.create(code="A-12")
        member = Member.objects.create(first_name="Taylor", last_name="Brooks")
        Residency.objects.create(
            member=member,
            apartment=apartment,
            start_date=date(2025, 1, 1),
        )
        Reimbursement.objects.create(
            requested_by_member=member,
            apartment=apartment,
            budget_year=budget_year,
            budget_category=category,
            title="Approved expense",
            expense_date=date(2025, 3, 1),
            amount_requested=Decimal("20.00"),
            amount_approved=Decimal("20.00"),
            status=ReimbursementStatus.APPROVED,
        )
        Reimbursement.objects.create(
            requested_by_member=member,
            apartment=apartment,
            budget_year=budget_year,
            budget_category=category,
            title="Voided expense",
            expense_date=date(2025, 3, 2),
            amount_requested=Decimal("15.00"),
            amount_approved=Decimal("15.00"),
            status=ReimbursementStatus.VOID,
            void_reason="Duplicate",
        )

        BudgetRollupService().sync_category(category)
        BudgetRollupService().sync_year(budget_year)
        category.refresh_from_db()
        budget_year.refresh_from_db()

        self.assertEqual(category.approved_reimbursement_total, Decimal("20.00"))
        self.assertEqual(budget_year.approved_reimbursement_total, Decimal("20.00"))

    def test_sync_totals_accumulate_approved_and_paid_reimbursements(self) -> None:
        budget_year = BudgetYear.objects.create(
            label="FY2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        operations = BudgetCategory.objects.create(
            budget_year=budget_year,
            code="ops",
            name="Operations",
        )
        maintenance = BudgetCategory.objects.create(
            budget_year=budget_year,
            code="maint",
            name="Maintenance",
        )
        apartment = Apartment.objects.create(code="B-22")
        member = Member.objects.create(first_name="Jordan", last_name="Miles")
        Residency.objects.create(
            member=member,
            apartment=apartment,
            start_date=date(2026, 1, 1),
        )
        Reimbursement.objects.create(
            requested_by_member=member,
            apartment=apartment,
            budget_year=budget_year,
            budget_category=operations,
            title="Approved expense",
            expense_date=date(2026, 3, 1),
            amount_requested=Decimal("20.00"),
            amount_approved=Decimal("20.00"),
            status=ReimbursementStatus.APPROVED,
        )
        Reimbursement.objects.create(
            requested_by_member=member,
            apartment=apartment,
            budget_year=budget_year,
            budget_category=operations,
            title="Paid expense",
            expense_date=date(2026, 3, 2),
            amount_requested=Decimal("30.00"),
            amount_approved=Decimal("30.00"),
            status=ReimbursementStatus.PAID,
        )
        Reimbursement.objects.create(
            requested_by_member=member,
            apartment=apartment,
            budget_year=budget_year,
            budget_category=operations,
            title="Voided expense",
            expense_date=date(2026, 3, 3),
            amount_requested=Decimal("15.00"),
            amount_approved=Decimal("15.00"),
            status=ReimbursementStatus.VOID,
            void_reason="Duplicate",
        )
        Reimbursement.objects.create(
            requested_by_member=member,
            apartment=apartment,
            budget_year=budget_year,
            budget_category=maintenance,
            title="Other approved expense",
            expense_date=date(2026, 3, 4),
            amount_requested=Decimal("40.00"),
            amount_approved=Decimal("40.00"),
            status=ReimbursementStatus.APPROVED,
        )

        service = BudgetRollupService()
        service.sync_category(operations)
        service.sync_category(maintenance)
        service.sync_year(budget_year)
        operations.refresh_from_db()
        maintenance.refresh_from_db()
        budget_year.refresh_from_db()

        self.assertEqual(operations.approved_reimbursement_total, Decimal("50.00"))
        self.assertEqual(operations.paid_reimbursement_total, Decimal("30.00"))
        self.assertEqual(maintenance.approved_reimbursement_total, Decimal("40.00"))
        self.assertEqual(maintenance.paid_reimbursement_total, Decimal("0.00"))
        self.assertEqual(budget_year.approved_reimbursement_total, Decimal("90.00"))
        self.assertEqual(budget_year.paid_reimbursement_total, Decimal("30.00"))
