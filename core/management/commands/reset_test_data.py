"""
Management command to wipe all transactional data while preserving
configuration (houses, members, apartments, budget years, sub-budgets).

Deletes (in dependency order):
    1. bons_duplicateflag
    2. bons_receiptextractedfields
    3. bons_receiptocrresult
    4. bons_receiptfile
    5. budget_expense
    6. bons_bondecommande
    7. bons_merchant
    8. audits_auditlogentry
    9. Uploaded receipt files under MEDIA_ROOT/receipts/

Preserves:
    - houses, members, apartments, residencies
    - budget years and sub-budgets
    - user accounts
    - django sessions, admin, auth, content types

NOTE: When adding new models that hold transactional data (e.g. a
      Reimbursement model), add a DELETE line here in the correct
      dependency position and update the counts dict.

Usage:
    python manage.py reset_test_data
    python manage.py reset_test_data --yes
    docker compose exec web python manage.py reset_test_data --yes
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Efface toutes les dépenses, bons de commande, fichiers de reçus "
        "et entrées d'audit pour repartir à zéro. Conserve les maisons, "
        "membres, années budgétaires et sous-budgets."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes", action="store_true",
            help="Skip confirmation prompt",
        )

    def handle(self, *args, **options):
        from bons.models import (
            BonDeCommande, ReceiptFile, ReceiptExtractedFields,
            ReceiptOcrResult, DuplicateFlag, Merchant,
        )
        from budget.models import Expense
        from audits.models import AuditLogEntry

        # ----------------------------------------------------------
        # 1. Gather counts for user confirmation
        # ----------------------------------------------------------
        counts = {
            "DuplicateFlag": DuplicateFlag.objects.count(),
            "ReceiptExtractedFields": ReceiptExtractedFields.objects.count(),
            "ReceiptOcrResult": ReceiptOcrResult.objects.count(),
            "ReceiptFile": ReceiptFile.objects.count(),
            "Expense": Expense.objects.count(),
            "BonDeCommande": BonDeCommande.objects.count(),
            "Merchant": Merchant.objects.count(),
            "AuditLogEntry": AuditLogEntry.objects.count(),
        }

        receipts_dir = Path(settings.MEDIA_ROOT) / "receipts"
        media_file_count = sum(
            1 for _ in receipts_dir.rglob("*") if _.is_file()
        ) if receipts_dir.exists() else 0

        total = sum(counts.values()) + media_file_count
        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                "\n✅ Rien à effacer — la base est déjà vide."
            ))
            return

        self.stdout.write("\nObjets à supprimer :")
        for model, count in counts.items():
            marker = self.style.WARNING("» ") if count else "  "
            self.stdout.write(f"  {marker}{model}: {count}")
        marker = self.style.WARNING("» ") if media_file_count else "  "
        self.stdout.write(
            f"  {marker}Fichiers média (receipts/): {media_file_count}"
        )

        self.stdout.write("\nConservés intacts :")
        self.stdout.write("  ✓ Maisons, membres, appartements, résidences")
        self.stdout.write("  ✓ Années budgétaires et sous-budgets")
        self.stdout.write("  ✓ Comptes utilisateurs")

        if not options["yes"]:
            confirm = input("\nConfirmer la suppression ? (oui/non) : ")
            if confirm.strip().lower() not in ("oui", "o", "yes", "y"):
                self.stdout.write(self.style.WARNING("Annulé."))
                return

        # ----------------------------------------------------------
        # 2. Delete DB rows (raw SQL to bypass NonDestructiveModel)
        #    Order matters: children before parents.
        # ----------------------------------------------------------
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM bons_duplicateflag")
            cursor.execute("DELETE FROM bons_receiptextractedfields")
            cursor.execute("DELETE FROM bons_receiptocrresult")
            cursor.execute("DELETE FROM bons_receiptfile")
            cursor.execute("DELETE FROM budget_expense")
            cursor.execute("DELETE FROM bons_bondecommande")
            cursor.execute("DELETE FROM bons_merchant")
            cursor.execute("DELETE FROM audits_auditlogentry")

        # ----------------------------------------------------------
        # 3. Delete uploaded receipt files from MEDIA_ROOT
        # ----------------------------------------------------------
        if receipts_dir.exists():
            shutil.rmtree(receipts_dir)
            self.stdout.write(f"  Supprimé : {receipts_dir}/")

        self.stdout.write(self.style.SUCCESS(
            "\n✅ Toutes les données de test ont été effacées.\n"
            "   Les années budgétaires et sous-budgets sont intacts.\n"
            "   Vous pouvez recommencer les tests proprement."
        ))
