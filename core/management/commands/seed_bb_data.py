"""
Management command to seed House BB with real data from the Excel spreadsheet.
Creates: House BB, 20 members with apartments and residencies,
BudgetYear 2026 with all sub-budgets, and the 4 sample expenses.
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from houses.models import House
from members.models import Member, Apartment, Residency
from budget.models import BudgetYear, SubBudget, Expense
from accounts.models import User


# Real member data from the Références sheet
BB_MEMBERS = [
    ("101", "Oswaldo", "Bossio"),
    ("102", "Matey", "Mandza"),
    ("103", "Carole", "Lacourse"),
    ("104", "Mélyse", "Mupfasoni"),
    ("201", "Alexis", "Roman"),
    ("202", "Marylin", "Lamarche"),
    ("203", "Jessica", "Bergeron"),
    ("204", "Stephane", "Stephane"),
    ("205", "Marie-Josée", "Hamel"),
    ("206", "Carl-David", "Fortin"),
    ("207", "Abla", "Tougan"),
    ("208", "Sylvie", "St-Pierre"),
    ("301", "Céline", "Bouffard"),
    ("302", "Alexia", "Matte"),
    ("303", "Érick", "Lafrance"),
    ("304", "Serge", "Laroche"),
    ("305", "Leeanne", "Brown"),
    ("306", "Suzie", "Gilbert"),
    ("307", "Guillaume", "Bolduc"),
    ("308", "Gilles", "Bisson"),
]

# Sub-budgets from the "Résumé du Budget" sheet
BB_SUB_BUDGETS = [
    # (trace_code, name, repeat_type, planned_amount)
    (0, "Imprévues", "annual", Decimal("1835.55")),
    (1, "Réparations par appartement", "annual", Decimal("2000.00")),
    (2, "Réparations BB", "annual", Decimal("1000.00")),
    (3, "Inspection système d'alarme/changement extincteurs", "annual", Decimal("1050.00")),
    (4, "Inspection extincteurs", "annual", Decimal("0.00")),
    (5, "Exterminateur", "annual", Decimal("350.00")),
    (6, "Corvées", "annual", Decimal("250.00")),
    (7, "Produits ménager/entretien", "annual", Decimal("300.00")),
    (8, "Transport", "annual", Decimal("75.00")),
    (9, "Photocopies", "annual", Decimal("100.00")),
    (10, "Activités sociales", "annual", Decimal("400.00")),
    (11, "Peinture", "annual", Decimal("250.00")),
    (12, "Rouille", "unique", Decimal("5000.00")),
    (99, "Autre dépenses", "annual", Decimal("0.00")),
]

# Expenses from the Budget sheet (rows 4-7)
BB_EXPENSES = [
    # (date, description, bon_number, validated_gl, supplier, spent_by, amount, trace_code, source)
    (date(2026, 1, 7), "4999081-BC 16739-Nettoyants", "16739", True,
     "Sony", "202 / Marylin", Decimal("19.49"), 7, "bon_de_commande"),
    (date(2026, 1, 30), "40785-1215 Kitchener-Rep coulage locker", "n/a", True,
     "Pouliot", "BB", Decimal("564.92"), 0, "accountant_direct"),
    (date(2026, 2, 26), "511888-BC137940-Colleplomberie#102", "n/a", True,
     "Parent", "102 / Matey", Decimal("5.16"), 1, "accountant_direct"),
    (date(2026, 2, 26), "Robinet (304)", "17186", False,
     "Parent", "304 / Serge", Decimal("54.54"), 1, "bon_de_commande"),
]


class Command(BaseCommand):
    help = "Seed House BB with real data from the Excel spreadsheet (2026)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing BB data before seeding.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Seed BB even if the database already contains existing app data.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            self._reset()
        elif not options["force"]:
            existing_sections = self._existing_data_sections()
            if existing_sections:
                summary = ", ".join(existing_sections)
                self.stdout.write(
                    self.style.WARNING(
                        "Base de donnees deja configuree; seed BB automatique ignore."
                    )
                )
                self.stdout.write(
                    f"  Sections deja peuplees: {summary}. "
                    "Utilisez --force pour semer BB quand meme, ou --reset pour repartir de zero."
                )
                return

        house = self._create_house()
        members_map = self._create_members(house)
        budget_year = self._create_budget_year(house)
        sub_budgets = self._create_sub_budgets(budget_year)
        self._create_expenses(budget_year, sub_budgets)
        self._create_superuser(house)
        self._create_treasurer(house, members_map)

        self.stdout.write(self.style.SUCCESS(
            f"✓ Maison BB créée avec {len(members_map)} membres, "
            f"budget 2026, {len(sub_budgets)} sous-budgets, "
            f"{len(BB_EXPENSES)} dépenses."
        ))

    def _existing_data_sections(self):
        existing_sections = []
        checks = (
            ("maisons", House.objects.all()),
            ("membres", Member.objects.all()),
            ("budgets", BudgetYear.objects.all()),
            ("dépenses", Expense.objects.all()),
            ("utilisateurs", User.objects.all()),
        )
        for label, queryset in checks:
            if queryset.exists():
                existing_sections.append(label)
        return existing_sections

    def _reset(self):
        """Delete all BB-related data for a clean re-seed."""
        house = House.objects.filter(code="BB").first()
        if house:
            Expense.objects.filter(budget_year__house=house).delete()
            SubBudget.objects.filter(budget_year__house=house).delete()
            BudgetYear.objects.filter(house=house).delete()
            Residency.objects.filter(apartment__house=house).delete()
            Apartment.objects.filter(house=house).delete()
            # Unlink house from members before deleting
            house.treasurer_member = None
            house.correspondent_member = None
            house.save()
            Member.objects.filter(
                residency_set__apartment__house=house
            ).delete()
            # Delete members that were created for BB (orphans after residency delete)
            # Re-query since residencies are gone
            house.delete()
            self.stdout.write(self.style.WARNING("Données BB supprimées."))

    def _create_house(self):
        house, created = House.objects.get_or_create(
            code="BB",
            defaults={
                "name": "Maison BB",
                "account_number": "13-51200",
                "address": "1215 Kitchener, Montréal",
            },
        )
        if created:
            self.stdout.write(f"  Maison BB créée.")
        else:
            self.stdout.write(f"  Maison BB existe déjà.")
        return house

    def _create_members(self, house):
        """Create members, apartments, and residencies for BB."""
        members_map = {}
        seed_start_date = date(2026, 1, 1)
        for apt_code, first, last in BB_MEMBERS:
            # Create or get member
            member, _ = Member.objects.get_or_create(
                first_name=first,
                last_name=last,
                defaults={"is_active": True},
            )

            # Create or get apartment
            apartment, _ = Apartment.objects.get_or_create(
                house=house,
                code=apt_code,
                defaults={"is_active": True},
            )

            existing_seed_residency = Residency.objects.filter(
                member=member,
                apartment=apartment,
                start_date=seed_start_date,
            ).exists()
            if existing_seed_residency:
                members_map[apt_code] = member
                continue

            if member.residencies.exists():
                self.stdout.write(
                    self.style.WARNING(
                        f"  Résidence BB ignorée pour {member.display_name} : historique existant."
                    )
                )
            else:
                Residency.objects.create(
                    member=member,
                    apartment=apartment,
                    start_date=seed_start_date,
                )

            members_map[apt_code] = member

        # "BB" is used as a virtual spent_by for the house itself
        # Create a BB apartment for house-level entries
        Apartment.objects.get_or_create(
            house=house,
            code="BB",
            defaults={"is_active": True, "notes": "Entrée au niveau de la maison"},
        )

        self.stdout.write(f"  {len(members_map)} membres et appartements créés.")
        return members_map

    def _create_budget_year(self, house):
        # Delete auto-created contingency sub-budget (we'll create our own with correct amount)
        by, created = BudgetYear.objects.get_or_create(
            house=house,
            year=2026,
            defaults={
                "label": "Budget 2026",
                "annual_budget_total": Decimal("12237.00"),
                "snow_budget": Decimal("1860.00"),
                "imprevues_rate": Decimal("0.1500"),
                "is_active": True,
            },
        )
        if created:
            # Remove auto-generated contingency so we can set the correct planned_amount
            SubBudget.objects.filter(budget_year=by, trace_code=0).delete()
            self.stdout.write(f"  Budget 2026 créé (12 237 $ + 1 860 $ déneigement).")
        else:
            self.stdout.write(f"  Budget 2026 existe déjà.")
        return by

    def _create_sub_budgets(self, budget_year):
        sub_budgets = {}
        for trace_code, name, repeat_type, planned in BB_SUB_BUDGETS:
            sb, _ = SubBudget.objects.get_or_create(
                budget_year=budget_year,
                trace_code=trace_code,
                defaults={
                    "name": name,
                    "repeat_type": repeat_type,
                    "planned_amount": planned,
                    "sort_order": trace_code,
                    "is_contingency": trace_code == 0,
                },
            )
            sub_budgets[trace_code] = sb

        self.stdout.write(f"  {len(sub_budgets)} sous-budgets créés.")
        return sub_budgets

    def _create_expenses(self, budget_year, sub_budgets):
        created_count = 0
        for entry_date, desc, bon_num, gl, supplier, spent_by, amount, trace, source in BB_EXPENSES:
            sub_budget = sub_budgets[trace]
            _, created = Expense.objects.get_or_create(
                budget_year=budget_year,
                entry_date=entry_date,
                description=desc,
                amount=amount,
                defaults={
                    "sub_budget": sub_budget,
                    "bon_number": bon_num,
                    "validated_gl": gl,
                    "supplier_name": supplier,
                    "spent_by_label": spent_by,
                    "source_type": source,
                },
            )
            if created:
                created_count += 1

        self.stdout.write(f"  {created_count} dépenses créées.")

    def _create_superuser(self, house):
        """Create a default admin user if none exists."""
        if not User.objects.filter(is_superuser=True).exists():
            user = User.objects.create_superuser(
                username="admin",
                email="admin@tresorapide.local",
                password="admin",
                role="GESTIONNAIRE",
            )
            user.house = house
            user.save()
            self.stdout.write(self.style.WARNING(
                "  ⚠ Superutilisateur créé: admin / admin (changez le mot de passe!)"
            ))
        else:
            self.stdout.write(f"  Superutilisateur existe déjà.")

    def _create_treasurer(self, house, members_map):
        """Create tresorierBB user linked to house BB."""
        user, created = User.objects.get_or_create(
            username="tresorierBB",
            defaults={
                "role": "TREASURER",
                "house": house,
            },
        )
        if created:
            user.set_password("tresorier1215")
            # Link to Guillaume Bolduc (apt 307) as the treasurer member
            member = members_map.get("307")
            if member:
                user.member = member
                house.treasurer_member = member
                house.save()
            user.save()
            self.stdout.write(self.style.WARNING(
                "  ⚠ Trésorier créé: tresorierBB / tresorier1215"
            ))
        else:
            self.stdout.write(f"  Trésorier tresorierBB existe déjà.")
