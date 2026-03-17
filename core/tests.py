from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from budget.models import BudgetCategory, BudgetYear
from members.models import Apartment, Member, Residency
from reimbursements.models import Reimbursement, ReimbursementStatus


class FoundationViewTests(TestCase):
    def test_home_page_renders(self) -> None:
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tresorapide")
        self.assertContains(response, "Housing co-op treasurer workspace")

    def test_healthcheck_endpoint_is_public(self) -> None:
        response = self.client.get(reverse("api-health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "tresorapide"})

    def test_readiness_endpoint_confirms_database(self) -> None:
        response = self.client.get(reverse("api-ready"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "service": "tresorapide", "database": "ok"},
        )


class DashboardAndSetupViewTests(TestCase):
    def setUp(self) -> None:
        self.viewer = User.objects.create_user(
            username="dashboard-viewer",
            password="StrongPassw0rd!",
            role=User.Role.VIEWER,
        )
        self.treasurer = User.objects.create_user(
            username="dashboard-treasurer",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
        )
        self.admin = User.objects.create_user(
            username="dashboard-admin",
            password="StrongPassw0rd!",
            role=User.Role.ADMIN,
        )
        member = Member.objects.create(first_name="Taylor", last_name="Brooks")
        apartment = Apartment.objects.create(code="A-10")
        Residency.objects.create(
            member=member,
            apartment=apartment,
            start_date=date(2025, 1, 1),
        )
        budget_year = BudgetYear.objects.create(
            label="FY2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        category = BudgetCategory.objects.create(
            budget_year=budget_year,
            code="house",
            name="House",
            planned_amount=Decimal("100.00"),
        )
        Reimbursement.objects.create(
            requested_by_member=member,
            apartment=apartment,
            budget_year=budget_year,
            budget_category=category,
            created_by=self.viewer,
            title="Light bulbs",
            expense_date=date(2025, 2, 15),
            amount_requested=Decimal("25.00"),
            amount_approved=Decimal("20.00"),
            status=ReimbursementStatus.APPROVED,
        )

    def test_viewer_dashboard_shows_read_only_navigation(self) -> None:
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")
        self.assertContains(response, reverse("budget:list"))
        self.assertContains(response, reverse("reimbursements:list"))
        self.assertNotContains(response, reverse("audits:list"))
        self.assertNotContains(response, reverse("admin-setup"))
        self.assertContains(response, "read-only")

    def test_admin_setup_requires_admin_role(self) -> None:
        self.client.force_login(self.treasurer)

        treasurer_response = self.client.get(reverse("admin-setup"))

        self.assertEqual(treasurer_response.status_code, 403)

        self.client.force_login(self.admin)
        admin_response = self.client.get(reverse("admin-setup"))

        self.assertEqual(admin_response.status_code, 200)
        self.assertContains(admin_response, "Admin setup hub")
        self.assertContains(admin_response, "User accounts")
