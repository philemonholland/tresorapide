from __future__ import annotations

from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from budget.models import BudgetYear
from houses.models import House
from members.models import Apartment, Member, Residency


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


class SeedBbDataCommandTests(TestCase):
    def test_seed_command_skips_when_database_already_has_app_data(self) -> None:
        User.objects.create_user(
            username="existing-admin",
            password="StrongPassw0rd!",
            role=User.Role.ADMIN,
        )

        output = StringIO()
        call_command("seed_bb_data", stdout=output)

        self.assertFalse(House.objects.filter(code="BB").exists())
        self.assertEqual(User.objects.count(), 1)
        self.assertIn("seed BB automatique ignore", output.getvalue())

    def test_seed_command_skip_preserves_member_moves_on_later_runs(self) -> None:
        call_command("seed_bb_data", stdout=StringIO())

        house = House.objects.get(code="BB")
        moved_member = Member.objects.get(first_name="Matey", last_name="Mandza")
        old_apartment = Apartment.objects.get(house=house, code="102")
        old_residency = Residency.objects.get(
            member=moved_member,
            apartment=old_apartment,
            start_date=date(2026, 1, 1),
        )
        old_residency.end_date = date(2026, 3, 21)
        old_residency.save()

        new_apartment = Apartment.objects.create(house=house, code="902")
        Residency.objects.create(
            member=moved_member,
            apartment=new_apartment,
            start_date=date(2026, 3, 22),
        )

        output = StringIO()
        call_command("seed_bb_data", stdout=output)

        self.assertEqual(
            Residency.objects.filter(
                member=moved_member,
                apartment=old_apartment,
                start_date=date(2026, 1, 1),
            ).count(),
            1,
        )
        self.assertEqual(Residency.objects.filter(member=moved_member).count(), 2)
        self.assertEqual(moved_member.current_apartment(), new_apartment)
        self.assertIn("seed BB automatique ignore", output.getvalue())

    def test_seed_command_force_allows_manual_seed_in_populated_database(self) -> None:
        User.objects.create_user(
            username="existing-admin",
            password="StrongPassw0rd!",
            role=User.Role.ADMIN,
        )

        call_command("seed_bb_data", force=True, stdout=StringIO())

        self.assertTrue(House.objects.filter(code="BB").exists())

    def test_seed_command_force_preserves_existing_residency_history(self) -> None:
        call_command("seed_bb_data", stdout=StringIO())

        house = House.objects.get(code="BB")
        moved_member = Member.objects.get(first_name="Matey", last_name="Mandza")
        old_apartment = Apartment.objects.get(house=house, code="102")
        old_residency = Residency.objects.get(
            member=moved_member,
            apartment=old_apartment,
            start_date=date(2026, 1, 1),
        )
        old_residency.end_date = date(2026, 3, 21)
        old_residency.save()

        new_apartment = Apartment.objects.create(house=house, code="903")
        Residency.objects.create(
            member=moved_member,
            apartment=new_apartment,
            start_date=date(2026, 3, 22),
        )

        call_command("seed_bb_data", force=True, stdout=StringIO())

        self.assertEqual(
            Residency.objects.filter(
                member=moved_member,
                apartment=old_apartment,
                start_date=date(2026, 1, 1),
            ).count(),
            1,
        )
        self.assertEqual(Residency.objects.filter(member=moved_member).count(), 2)
        self.assertEqual(moved_member.current_apartment(), new_apartment)
