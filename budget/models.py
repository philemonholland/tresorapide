from django.db import models
from django.core.exceptions import ValidationError
from core.models import TimeStampedModel


class RepeatType(models.TextChoices):
    ANNUAL = "annual", "Annuel"
    UNIQUE = "unique", "Unique"


class ExpenseSourceType(models.TextChoices):
    BON_DE_COMMANDE = "bon_de_commande", "Bon de commande"
    ACCOUNTANT_DIRECT = "accountant_direct", "Dépense directe gestionnaire"
    GL_IMPORT = "gl_import", "Import grand livre"


class BudgetYear(TimeStampedModel):
    """Annual budget for a specific house."""
    house = models.ForeignKey(
        "houses.House", on_delete=models.PROTECT, related_name="budget_years"
    )
    year = models.PositiveIntegerField(help_text="Année civile, ex : 2026")
    label = models.CharField(
        max_length=50, blank=True,
        help_text="Libellé d'affichage, généré automatiquement si vide"
    )
    annual_budget_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Budget total d'entretien pour l'année"
    )
    snow_budget = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Budget de déneigement séparé"
    )
    imprevues_rate = models.DecimalField(
        max_digits=5, decimal_places=4, default="0.1500",
        help_text="Taux d'imprévues, 15 % par défaut"
    )
    is_active = models.BooleanField(default=True)
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-year"]
        constraints = [
            models.UniqueConstraint(
                fields=["house", "year"],
                name="unique_budget_year_per_house"
            )
        ]

    def clean(self):
        if not self.label:
            self.label = str(self.year)

    def save(self, *args, **kwargs):
        if not self.label:
            self.label = str(self.year)
        self.full_clean()
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            self._create_contingency_sub_budget()

    def _create_contingency_sub_budget(self):
        """Auto-create the Imprévues sub-budget (trace_code=0) for new budget years."""
        if not self.sub_budgets.filter(trace_code=0).exists():
            SubBudget.objects.create(
                budget_year=self,
                trace_code=0,
                name="Imprévues",
                repeat_type=RepeatType.ANNUAL,
                planned_amount=self.annual_budget_total * self.imprevues_rate,
                sort_order=0,
                is_contingency=True,
            )

    @property
    def imprevues_amount(self):
        """Calculated contingency amount."""
        return self.annual_budget_total * self.imprevues_rate

    @property
    def budget_minus_imprevues(self):
        """Budget total minus contingency reserve."""
        return self.annual_budget_total - self.imprevues_amount

    def __str__(self):
        return f"{self.house.code} — {self.label}"

    @property
    def is_current_year(self):
        """True if this budget corresponds to the current calendar year."""
        from django.utils import timezone
        return self.year == timezone.now().year


class SubBudget(TimeStampedModel):
    """
    A sub-budget within a budget year. Each has a trace_code (0-99)
    that expenses reference to track spending against planned amounts.
    """
    budget_year = models.ForeignKey(
        BudgetYear, on_delete=models.PROTECT, related_name="sub_budgets"
    )
    trace_code = models.PositiveIntegerField(
        help_text="Code numérique (0-99) reliant les dépenses à ce sous-budget"
    )
    name = models.CharField(max_length=150)
    repeat_type = models.CharField(
        max_length=10, choices=RepeatType.choices, default=RepeatType.ANNUAL
    )
    planned_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_contingency = models.BooleanField(
        default=False,
        help_text="Vrai uniquement pour trace_code 0 (Imprévues)"
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["budget_year", "sort_order", "trace_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["budget_year", "trace_code"],
                name="unique_trace_per_budget_year"
            )
        ]

    @property
    def used(self):
        """Total spent against this sub-budget."""
        from django.db.models import Sum
        total = self.expenses.aggregate(total=Sum("amount"))["total"]
        return total or 0

    @property
    def remaining(self):
        """Planned minus used."""
        return self.planned_amount - self.used

    def __str__(self):
        return f"{self.budget_year.label} · {self.trace_code} · {self.name}"


class Expense(TimeStampedModel):
    """
    A single entry in the budget ledger. Can be linked to a BonDeCommande
    (member purchase) or standalone (gestionnaire direct expense / GL import).
    """
    budget_year = models.ForeignKey(
        BudgetYear, on_delete=models.PROTECT, related_name="expenses"
    )
    sub_budget = models.ForeignKey(
        SubBudget, on_delete=models.PROTECT, related_name="expenses"
    )
    bon_de_commande = models.ForeignKey(
        "bons.BonDeCommande", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="expenses"
    )
    entry_date = models.DateField(help_text="Date de la dépense")
    description = models.CharField(max_length=255)
    bon_number = models.CharField(
        max_length=20, blank=True,
        help_text="Numéro pré-imprimé ou format HHYYNNNN"
    )
    validated_gl = models.BooleanField(
        default=False,
        help_text="Confirmé dans le grand livre du gestionnaire"
    )
    supplier_name = models.CharField(max_length=200, blank=True)
    spent_by_label = models.CharField(
        max_length=200,
        help_text="ex : '202 / Marylin' ou 'BB' ou nom du fournisseur"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    source_type = models.CharField(
        max_length=20, choices=ExpenseSourceType.choices,
        default=ExpenseSourceType.BON_DE_COMMANDE
    )
    entered_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="entered_expenses"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["entry_date", "created_at", "id"]
        indexes = [
            models.Index(fields=["budget_year", "entry_date"]),
            models.Index(fields=["sub_budget"]),
        ]

    def clean(self):
        if self.sub_budget and self.budget_year:
            if self.sub_budget.budget_year_id != self.budget_year_id:
                raise ValidationError(
                    "Le sous-budget doit appartenir à la même année budgétaire."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.entry_date} · {self.description[:50]} · ${self.amount}"
