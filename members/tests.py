from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from members.models import Apartment, Member, Residency


class MemberAndResidencyTests(TestCase):
    def test_member_display_name_prefers_preferred_name(self) -> None:
        member = Member.objects.create(
            first_name="Alexandra",
            last_name="Nguyen",
            preferred_name="Alex",
            email="alex@example.com",
        )

        self.assertEqual(member.display_name, "Alex Nguyen")

    def test_residency_rejects_overlapping_periods_for_same_member(self) -> None:
        member = Member.objects.create(first_name="Sam", last_name="Lee")
        apartment_one = Apartment.objects.create(code="A-101")
        apartment_two = Apartment.objects.create(code="B-201")
        Residency.objects.create(
            member=member,
            apartment=apartment_one,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
        )

        overlapping = Residency(
            member=member,
            apartment=apartment_two,
            start_date=date(2024, 6, 15),
            end_date=date(2024, 12, 31),
        )

        with self.assertRaises(ValidationError):
            overlapping.full_clean()

    def test_residency_query_helpers_return_historical_occupants(self) -> None:
        apartment = Apartment.objects.create(code="C-301")
        member = Member.objects.create(first_name="Jordan", last_name="Patel")
        residency = Residency.objects.create(
            member=member,
            apartment=apartment,
            start_date=date(2024, 3, 1),
        )

        self.assertTrue(residency.is_active_on(date(2024, 3, 20)))
        self.assertEqual(member.residency_on(date(2024, 3, 20)), residency)
        self.assertIn(member, apartment.residents_on(date(2024, 3, 20)))

    def test_residency_query_helpers_exclude_members_after_move_out(self) -> None:
        first_apartment = Apartment.objects.create(code="C-302")
        second_apartment = Apartment.objects.create(code="C-303")
        member = Member.objects.create(first_name="Casey", last_name="Lopez")
        first_residency = Residency.objects.create(
            member=member,
            apartment=first_apartment,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
        )
        second_residency = Residency.objects.create(
            member=member,
            apartment=second_apartment,
            start_date=date(2024, 7, 1),
        )

        self.assertEqual(member.residency_on(date(2024, 5, 15)), first_residency)
        self.assertEqual(member.residency_on(date(2024, 7, 15)), second_residency)
        self.assertIn(member, first_apartment.residents_on(date(2024, 5, 15)))
        self.assertNotIn(member, first_apartment.residents_on(date(2024, 7, 15)))
        self.assertIn(member, second_apartment.residents_on(date(2024, 7, 15)))


class MembersWorkflowViewTests(TestCase):
    def setUp(self) -> None:
        self.treasurer = User.objects.create_user(
            username="member-treasurer",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
        )
        self.viewer = User.objects.create_user(
            username="member-viewer",
            password="StrongPassw0rd!",
            role=User.Role.VIEWER,
        )
        self.member = Member.objects.create(
            first_name="Robin",
            last_name="Singh",
            email="robin@example.com",
        )
        self.apartment = Apartment.objects.create(code="A-100")
        self.residency = Residency.objects.create(
            member=self.member,
            apartment=self.apartment,
            start_date=date(2025, 1, 1),
        )

    def test_members_dashboard_requires_treasurer_access(self) -> None:
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("members:index"))

        self.assertEqual(response.status_code, 403)

    def test_treasurer_can_create_and_edit_member(self) -> None:
        self.client.force_login(self.treasurer)

        create_response = self.client.post(
            reverse("members:member-create"),
            {
                "first_name": "Taylor",
                "last_name": "Brooks",
                "preferred_name": "Tay",
                "email": "tay@example.com",
                "phone_number": "555-0101",
                "is_active": "on",
                "notes": "Joined recently.",
            },
        )
        created_member = Member.objects.get(email="tay@example.com")
        update_response = self.client.post(
            reverse("members:member-edit", args=[created_member.pk]),
            {
                "first_name": "Taylor",
                "last_name": "Brooks",
                "preferred_name": "Tay",
                "email": "tay.updated@example.com",
                "phone_number": "555-0102",
                "is_active": "on",
                "notes": "Updated.",
            },
        )
        created_member.refresh_from_db()

        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(update_response.status_code, 302)
        self.assertEqual(created_member.email, "tay.updated@example.com")
        self.assertEqual(created_member.phone_number, "555-0102")

    def test_residency_create_surfaces_overlap_validation_errors(self) -> None:
        other_apartment = Apartment.objects.create(code="B-200")
        self.client.force_login(self.treasurer)

        response = self.client.post(
            reverse("members:residency-create"),
            {
                "member": self.member.pk,
                "apartment": other_apartment.pk,
                "start_date": "2025-02-01",
                "end_date": "",
                "notes": "Should overlap existing residency.",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Residency periods for the same member cannot overlap.",
        )
        self.assertEqual(Residency.objects.count(), 1)
