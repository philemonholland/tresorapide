from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.urls import reverse

from accounts.access import user_has_minimum_role
from accounts.models import User


class UserModelTests(TestCase):
    def test_auth_user_model_setting(self) -> None:
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.User")

    def test_user_role_defaults_to_viewer(self) -> None:
        user = User.objects.create_user(
            username="viewer",
            password="StrongPassw0rd!",
        )

        self.assertEqual(user.role, User.Role.VIEWER)
        self.assertTrue(user.can_view_financials)
        self.assertFalse(user.can_manage_financials)

    def test_treasurer_role_satisfies_viewer_and_treasurer_thresholds(self) -> None:
        user = User.objects.create_user(
            username="treasurer-thresholds",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
        )

        self.assertTrue(user.has_minimum_role(User.Role.VIEWER))
        self.assertTrue(user.has_minimum_role(User.Role.TREASURER))
        self.assertFalse(user.has_minimum_role(User.Role.ADMIN))

    def test_superuser_satisfies_admin_threshold(self) -> None:
        user = User.objects.create_superuser(
            username="super-admin",
            password="StrongPassw0rd!",
            email="admin@example.com",
        )

        self.assertTrue(user.has_minimum_role(User.Role.ADMIN))
        self.assertTrue(user.can_manage_financials)
        self.assertTrue(user.can_view_financials)

    def test_role_management_capability(self) -> None:
        expected_by_role = {
            User.Role.ADMIN: True,
            User.Role.TREASURER: True,
            User.Role.VIEWER: False,
        }

        for role, expected in expected_by_role.items():
            with self.subTest(role=role):
                user = User.objects.create_user(
                    username=f"user-{role}",
                    password="StrongPassw0rd!",
                    role=role,
                )
                self.assertEqual(user.can_manage_financials, expected)

    def test_role_helper_rejects_anonymous_user(self) -> None:
        self.assertFalse(user_has_minimum_role(AnonymousUser(), User.Role.VIEWER))

    def test_role_helper_rejects_inactive_user(self) -> None:
        user = User.objects.create_user(
            username="inactive-viewer",
            password="StrongPassw0rd!",
            is_active=False,
        )

        self.assertFalse(user_has_minimum_role(user, User.Role.VIEWER))


class AuthenticationFlowTests(TestCase):
    def test_login_and_logout_flow(self) -> None:
        User.objects.create_user(
            username="treasurer",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
        )

        login_response = self.client.post(
            reverse("accounts:login"),
            {"username": "treasurer", "password": "StrongPassw0rd!"},
        )
        logout_response = self.client.post(reverse("accounts:logout"))

        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.headers["Location"], reverse("home"))
        self.assertEqual(logout_response.status_code, 302)
        self.assertEqual(logout_response.headers["Location"], reverse("home"))
