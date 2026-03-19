from datetime import date
from django.test import TestCase
from django.core.exceptions import ValidationError

from houses.models import House
from members.models import Member, Apartment, Residency


class MemberModelTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(code="BB", name="Maison BB", account_number="13-51200")
        self.member = Member.objects.create(first_name="Marylin", last_name="Lamarche")
        self.apt = Apartment.objects.create(house=self.house, code="202")

    def test_display_name_defaults_to_full_name(self):
        self.assertEqual(self.member.display_name, "Marylin Lamarche")

    def test_display_name_uses_preferred_name(self):
        self.member.preferred_name = "Mary"
        self.assertEqual(self.member.display_name, "Mary")

    def test_current_house_derived_from_residency(self):
        Residency.objects.create(
            member=self.member, apartment=self.apt, start_date=date(2020, 1, 1)
        )
        self.assertEqual(self.member.current_house(), self.house)

    def test_member_without_residency_has_no_house(self):
        self.assertIsNone(self.member.current_house())

    def test_member_can_move_between_houses(self):
        house2 = House.objects.create(code="DB", name="Maison DB", account_number="16-51200")
        apt2 = Apartment.objects.create(house=house2, code="101")
        Residency.objects.create(
            member=self.member, apartment=self.apt,
            start_date=date(2020, 1, 1), end_date=date(2025, 12, 31)
        )
        Residency.objects.create(
            member=self.member, apartment=apt2, start_date=date(2026, 1, 1)
        )
        self.assertEqual(self.member.current_house(), house2)


class ResidencyValidationTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(code="BB", name="Maison BB", account_number="13-51200")
        self.member = Member.objects.create(first_name="Marylin", last_name="Lamarche")
        self.apt = Apartment.objects.create(house=self.house, code="202")

    def test_end_date_cannot_precede_start_date(self):
        with self.assertRaises(ValidationError):
            Residency.objects.create(
                member=self.member, apartment=self.apt,
                start_date=date(2026, 6, 1), end_date=date(2026, 1, 1)
            )

    def test_overlapping_residencies_rejected(self):
        Residency.objects.create(
            member=self.member, apartment=self.apt,
            start_date=date(2020, 1, 1)
        )
        apt2 = Apartment.objects.create(house=self.house, code="203")
        with self.assertRaises(ValidationError):
            Residency.objects.create(
                member=self.member, apartment=apt2,
                start_date=date(2023, 6, 1)
            )

    def test_non_overlapping_residencies_accepted(self):
        Residency.objects.create(
            member=self.member, apartment=self.apt,
            start_date=date(2020, 1, 1), end_date=date(2025, 12, 31)
        )
        apt2 = Apartment.objects.create(house=self.house, code="203")
        r2 = Residency.objects.create(
            member=self.member, apartment=apt2,
            start_date=date(2026, 1, 1)
        )
        self.assertIsNotNone(r2.pk)

    def test_apartment_code_unique_per_house(self):
        with self.assertRaises(Exception):
            Apartment.objects.create(house=self.house, code="202")

    def test_apartment_code_allowed_in_different_house(self):
        house2 = House.objects.create(code="DB", name="Maison DB", account_number="16-51200")
        apt2 = Apartment.objects.create(house=house2, code="202")
        self.assertIsNotNone(apt2.pk)
