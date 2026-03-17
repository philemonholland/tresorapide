from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from budget.models import BudgetCategory, BudgetYear
from members.models import Apartment, Member, Residency
from reimbursements.models import Reimbursement, ReimbursementStatus


class BudgetModelTests(TestCase):
    def test_budget_year_rejects_inverted_dates(self) -> None:
        budget_year = BudgetYear(
            label="FY2025",
            start_date=date(2025, 1, 1),
            end_date=date(2024, 12, 31),
        )

        with self.assertRaises(ValidationError):
            budget_year.full_clean()

    def test_budget_category_normalizes_code(self) -> None:
        budget_year = BudgetYear.objects.create(
            label="FY2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )

        category = BudgetCategory.objects.create(
            budget_year=budget_year,
            code=" office ",
            name="Office",
        )

        self.assertEqual(category.code, "OFFICE")


class BudgetTransparencyViewTests(TestCase):
    def setUp(self) -> None:
        self.viewer = User.objects.create_user(
            username="budget-viewer",
            password="StrongPassw0rd!",
            role=User.Role.VIEWER,
        )
        self.treasurer = User.objects.create_user(
            username="budget-treasurer",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
        )
        self.member = Member.objects.create(first_name="Taylor", last_name="Brooks")
        self.apartment = Apartment.objects.create(code="B-12")
        Residency.objects.create(
            member=self.member,
            apartment=self.apartment,
            start_date=date(2025, 1, 1),
        )
        self.budget_year = BudgetYear.objects.create(
            label="FY2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        self.category = BudgetCategory.objects.create(
            budget_year=self.budget_year,
            code="ops",
            name="Operations",
            planned_amount=Decimal("100.00"),
        )
        self.approved_reimbursement = Reimbursement.objects.create(
            requested_by_member=self.member,
            apartment=self.apartment,
            budget_year=self.budget_year,
            budget_category=self.category,
            created_by=self.viewer,
            title="Approved expense",
            expense_date=date(2025, 2, 20),
            amount_requested=Decimal("30.00"),
            amount_approved=Decimal("25.00"),
            status=ReimbursementStatus.APPROVED,
        )
        self.submitted_reimbursement = Reimbursement.objects.create(
            requested_by_member=self.member,
            apartment=self.apartment,
            budget_year=self.budget_year,
            budget_category=self.category,
            created_by=self.viewer,
            title="Submitted expense",
            expense_date=date(2025, 2, 21),
            amount_requested=Decimal("15.00"),
            status=ReimbursementStatus.SUBMITTED,
        )

    def test_viewer_category_page_is_read_only_and_hides_active_submitted_claims(self) -> None:
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("budget:category-detail", args=[self.category.pk]))
        post_response = self.client.post(reverse("budget:category-detail", args=[self.category.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "$100.00")
        self.assertContains(response, self.approved_reimbursement.reference_code)
        self.assertNotContains(response, self.submitted_reimbursement.reference_code)
        self.assertEqual(post_response.status_code, 405)

    def test_treasurer_category_page_includes_submitted_claims(self) -> None:
        self.client.force_login(self.treasurer)

        response = self.client.get(reverse("budget:category-detail", args=[self.category.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.submitted_reimbursement.reference_code)


class BudgetManagementViewTests(TestCase):
    def setUp(self) -> None:
        self.treasurer = User.objects.create_user(
            username="budget-manager",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
        )
        self.viewer = User.objects.create_user(
            username="budget-view-only",
            password="StrongPassw0rd!",
            role=User.Role.VIEWER,
        )
        self.budget_year = BudgetYear.objects.create(
            label="FY2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        self.category = BudgetCategory.objects.create(
            budget_year=self.budget_year,
            code="OPS",
            name="Operations",
            planned_amount=Decimal("1000.00"),
        )

    def test_viewer_cannot_access_budget_management_routes(self) -> None:
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("budget:year-create"))

        self.assertEqual(response.status_code, 403)

    def test_treasurer_can_create_and_edit_budget_year(self) -> None:
        self.client.force_login(self.treasurer)

        create_response = self.client.post(
            reverse("budget:year-create"),
            {
                "label": "FY2027",
                "start_date": "2027-01-01",
                "end_date": "2027-12-31",
                "is_closed": "",
                "notes": "Draft planning year.",
            },
        )
        created_year = BudgetYear.objects.get(label="FY2027")
        update_response = self.client.post(
            reverse("budget:year-edit", args=[created_year.pk]),
            {
                "label": "FY2027",
                "start_date": "2027-01-01",
                "end_date": "2027-12-31",
                "is_closed": "on",
                "notes": "Closed after year-end review.",
            },
        )
        created_year.refresh_from_db()

        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(update_response.status_code, 302)
        self.assertTrue(created_year.is_closed)
        self.assertIsNotNone(created_year.closed_at)

    def test_treasurer_can_create_and_edit_budget_category(self) -> None:
        self.client.force_login(self.treasurer)

        create_response = self.client.post(
            reverse("budget:category-create", args=[self.budget_year.pk]),
            {
                "budget_year": self.budget_year.pk,
                "code": "capx",
                "name": "Capital",
                "description": "Long-term assets.",
                "planned_amount": "2500.00",
                "sort_order": "3",
                "is_active": "on",
            },
        )
        created_category = BudgetCategory.objects.get(code="CAPX")
        update_response = self.client.post(
            reverse("budget:category-edit", args=[created_category.pk]),
            {
                "budget_year": self.budget_year.pk,
                "code": "capx",
                "name": "Capital reserve",
                "description": "Updated planning bucket.",
                "planned_amount": "3000.00",
                "sort_order": "5",
                "is_active": "on",
            },
        )
        created_category.refresh_from_db()

        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(update_response.status_code, 302)
        self.assertEqual(created_category.name, "Capital reserve")
        self.assertEqual(created_category.planned_amount, Decimal("3000.00"))
