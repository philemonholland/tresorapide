from django.db import models
from core.models import TimeStampedModel


class MaintenanceStatus(models.TextChoices):
    OPEN = "OPEN", "Ouvert"
    PLANNED = "PLANNED", "Planifié"
    IN_PROGRESS = "IN_PROGRESS", "En cours"
    DONE = "DONE", "Terminé"
    DEFERRED = "DEFERRED", "Reporté"


class MaintenancePlanItem(TimeStampedModel):
    """
    Maintenance item for a budget year, optionally linked to an apartment.
    Equivalent of the 'Plan d'entretien' sheet in the Excel workbook.
    """
    budget_year = models.ForeignKey(
        "budget.BudgetYear", on_delete=models.PROTECT,
        related_name="maintenance_items"
    )
    apartment = models.ForeignKey(
        "members.Apartment", on_delete=models.PROTECT,
        null=True, blank=True, related_name="maintenance_items"
    )
    apartment_label_snapshot = models.CharField(max_length=50, blank=True)
    maintenance_item = models.TextField(
        help_text="Description des travaux nécessaires"
    )
    responsible_party = models.CharField(
        max_length=50,
        help_text="ex : 'CHCE', 'Interne', 'Membre'"
    )
    comments = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=MaintenanceStatus.choices,
        default=MaintenanceStatus.OPEN
    )
    linked_sub_budget = models.ForeignKey(
        "budget.SubBudget", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="maintenance_items"
    )

    class Meta:
        ordering = ["budget_year", "apartment__code", "id"]

    def save(self, *args, **kwargs):
        if self.apartment and not self.apartment_label_snapshot:
            self.apartment_label_snapshot = str(self.apartment)
        super().save(*args, **kwargs)

    def __str__(self):
        apt = self.apartment_label_snapshot or "General"
        return f"{apt} — {self.maintenance_item[:60]}"
