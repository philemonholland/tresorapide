from django.core.management.base import BaseCommand
from django.db import transaction

from houses.coop_directory import COOP_HOUSE_DIRECTORY, PDF_SOURCE_PATH
from houses.models import House


class Command(BaseCommand):
    help = (
        "Importe ou met à jour les maisons de la coop à partir du tableau "
        f"transcrit depuis {PDF_SOURCE_PATH}."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche les changements prévus sans écrire en base de données.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        created = 0
        updated = 0

        for record in COOP_HOUSE_DIRECTORY:
            defaults = {
                "accounting_code": record["accounting_code"],
                "name": record["name"],
                "account_number": record["account_number"],
                "address": record["address"],
            }
            house, was_created = House.objects.get_or_create(
                code=record["code"],
                defaults=defaults,
            )
            if was_created:
                created += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[créée] {house.code} — {record['name']} ({record['account_number']})"
                    )
                )
                continue

            changed_fields = []
            for field_name, value in defaults.items():
                if getattr(house, field_name) != value:
                    setattr(house, field_name, value)
                    changed_fields.append(field_name)

            if changed_fields:
                updated += 1
                house.save(update_fields=changed_fields + ["updated_at"])
                self.stdout.write(
                    self.style.WARNING(
                        f"[mise à jour] {house.code} — champs: {', '.join(changed_fields)}"
                    )
                )
            else:
                self.stdout.write(f"[inchangée] {house.code}")

        total = len(COOP_HOUSE_DIRECTORY)
        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("Dry-run: aucune modification enregistrée."))

        self.stdout.write(
            self.style.SUCCESS(
                f"Import terminé depuis {PDF_SOURCE_PATH}: "
                f"{total} maisons traitées, {created} créées, {updated} mises à jour."
            )
        )
