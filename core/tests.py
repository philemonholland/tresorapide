from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from budget.models import BudgetYear
from houses.models import House


class FoundationViewTests(TestCase):
    def test_home_page_renders(self) -> None:
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tresorapide")

    def test_home_page_guides_first_user_creation_when_no_users_exist(self) -> None:
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Première configuration")

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


class LoginSetupGuidanceTests(TestCase):
    def test_login_page_guides_first_user_creation_when_no_users_exist(self) -> None:
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aucun utilisateur")

    def test_login_page_hides_first_user_guidance_after_users_exist(self) -> None:
        User.objects.create_user(
            username="existing-user",
            password="StrongPassw0rd!",
            role=User.Role.ADMIN,
        )
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Aucun utilisateur")


class HandheldHomeRedirectTests(TestCase):
    def test_home_redirects_treasurer_on_handheld_to_mobile_capture(self) -> None:
        house = House.objects.create(
            code="BB",
            name="Maison BB",
            account_number="13-51200",
        )
        BudgetYear.objects.create(
            house=house,
            year=2026,
            annual_budget_total=1000,
        )
        user = User.objects.create_user(
            username="mobile-treasurer",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
            house=house,
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("home"),
            HTTP_USER_AGENT=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("bons:mobile-capture"))
