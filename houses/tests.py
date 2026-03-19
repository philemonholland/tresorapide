from django.test import TestCase
from django.db import IntegrityError

from houses.models import House


class HouseModelTests(TestCase):
    def test_house_creation(self):
        house = House.objects.create(code="BB", name="Maison BB", account_number="13-51200")
        self.assertEqual(str(house), "BB — Maison BB")

    def test_unique_code(self):
        House.objects.create(code="BB", name="Maison BB", account_number="13-51200")
        with self.assertRaises(IntegrityError):
            House.objects.create(code="BB", name="Another", account_number="14-51200")

    def test_unique_account_number(self):
        House.objects.create(code="BB", name="Maison BB", account_number="13-51200")
        with self.assertRaises(IntegrityError):
            House.objects.create(code="DB", name="Maison DB", account_number="13-51200")


class HouseMemberFieldsTests(TestCase):
    def setUp(self):
        from members.models import Member
        self.house = House.objects.create(code="BB", name="Maison BB", account_number="13-51200")
        self.member1 = Member.objects.create(first_name="Alice", last_name="Tremblay")
        self.member2 = Member.objects.create(first_name="Bob", last_name="Gagnon")

    def test_house_can_have_treasurer_member(self):
        self.house.treasurer_member = self.member1
        self.house.save()
        self.house.refresh_from_db()
        self.assertEqual(self.house.treasurer_member, self.member1)

    def test_house_can_have_correspondent_member(self):
        self.house.correspondent_member = self.member2
        self.house.save()
        self.house.refresh_from_db()
        self.assertEqual(self.house.correspondent_member, self.member2)

    def test_house_treasurer_and_correspondent_can_be_same_member(self):
        self.house.treasurer_member = self.member1
        self.house.correspondent_member = self.member1
        self.house.save()
        self.house.refresh_from_db()
        self.assertEqual(self.house.treasurer_member, self.house.correspondent_member)

    def test_house_member_fields_nullable(self):
        self.assertIsNone(self.house.treasurer_member)
        self.assertIsNone(self.house.correspondent_member)

    def test_reverse_relation_treasurer_of_houses(self):
        self.house.treasurer_member = self.member1
        self.house.save()
        self.assertIn(self.house, self.member1.treasurer_of_houses.all())

    def test_reverse_relation_correspondent_of_houses(self):
        self.house.correspondent_member = self.member2
        self.house.save()
        self.assertIn(self.house, self.member2.correspondent_of_houses.all())
