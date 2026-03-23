from io import StringIO

from django.test import TestCase
from django.db import IntegrityError
from django.core.management import call_command
from django.urls import reverse

from accounts.models import User
from houses.coop_directory import COOP_HOUSE_DIRECTORY
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


class HouseImportCommandTests(TestCase):
    def test_import_command_creates_reference_houses(self):
        output = StringIO()
        call_command("import_coop_houses", stdout=output)

        self.assertEqual(House.objects.count(), len(COOP_HOUSE_DIRECTORY))
        bb = House.objects.get(code="BB")
        self.assertEqual(bb.accounting_code, "13")
        self.assertEqual(bb.account_number, "13-51200")
        self.assertEqual(bb.name, "La Pédagogique")

        l_house = House.objects.get(code="L")
        self.assertIn("433-445, rue de Vimy", l_house.address)
        self.assertIn("459-461, rue de Vimy", l_house.address)
        self.assertIn("Import terminé", output.getvalue())

    def test_import_command_updates_existing_house(self):
        House.objects.create(
            code="BB",
            name="Maison BB",
            account_number="13-51200",
            address="Ancienne adresse",
        )

        call_command("import_coop_houses")

        bb = House.objects.get(code="BB")
        self.assertEqual(bb.name, "La Pédagogique")
        self.assertEqual(bb.accounting_code, "13")
        self.assertEqual(bb.address, "1215, rue Kitchener")


class HouseVisibilityTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(
            code="BB",
            name="La Pédagogique",
            accounting_code="13",
            account_number="13-51200",
            address="1215, rue Kitchener",
        )
        self.user = User.objects.create_user(
            username="membre-bb",
            password="test123",
            role=User.Role.VIEWER,
            house=self.house,
        )

    def test_authenticated_viewer_sees_houses_nav_link(self):
        self.client.login(username="membre-bb", password="test123")
        response = self.client.get(reverse("budget:year-list"))
        self.assertContains(response, reverse("houses:list"))

    def test_house_detail_shows_accounting_code(self):
        response = self.client.get(reverse("houses:detail", kwargs={"pk": self.house.pk}))
        self.assertContains(response, "Code comptable")
        self.assertContains(response, "13")
        self.assertContains(response, "13-51200")
