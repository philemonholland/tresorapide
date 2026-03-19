from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from accounts.models import User


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
