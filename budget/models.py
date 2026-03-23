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


class GLUploadStatus(models.TextChoices):
    PENDING = "pending", "En attente"
    PARSED = "parsed", "Analysé"
    RECONCILING = "reconciling", "Rapprochement en cours"
    RECONCILED = "reconciled", "Rapproché"
    ERROR = "error", "Erreur"


class GLMatchConfidence(models.TextChoices):
    EXACT = "exact", "Correspondance exacte"
    PROBABLE = "probable", "Correspondance probable"
    UNMATCHED = "unmatched", "Non apparié"


class GrandLivreUpload(TimeStampedModel):
    """A single Grand Livre Excel file uploaded by a treasurer."""
    budget_year = models.ForeignKey(
        "budget.BudgetYear", on_delete=models.CASCADE,
        related_name="gl_uploads",
    )
    uploaded_file = models.FileField(upload_to="grand_livre/%Y/")
    uploaded_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL,
        null=True, related_name="gl_uploads",
    )
    account_number = models.CharField(
        max_length=20, blank=True,
        help_text="Numéro de compte trouvé, ex : 13-51200",
    )
    status = models.CharField(
        max_length=15, choices=GLUploadStatus.choices,
        default=GLUploadStatus.PENDING,
    )
    gl_total_debit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    gl_total_credit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )
    gl_solde_fin = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Solde fin from the GL total line",
    )
    entry_count = models.PositiveIntegerField(default=0)
    period_end_date = models.DateField(
        null=True, blank=True,
        help_text="Date de fin de la période couverte par le GL",
    )
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"GL {self.budget_year} — {self.get_status_display()}"


class GrandLivreEntry(TimeStampedModel):
    """A single transaction row parsed from the Grand Livre."""
    upload = models.ForeignKey(
        GrandLivreUpload, on_delete=models.CASCADE,
        related_name="entries",
    )
    row_number = models.PositiveIntegerField()
    period = models.CharField(max_length=20, blank=True)
    date = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=100, blank=True)
    description_raw = models.TextField(blank=True)
    description_clean = models.TextField(
        blank=True,
        help_text="AI-rewritten description",
    )
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    solde_fin = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
    )

    # Reconciliation fields
    matched_expense = models.ForeignKey(
        "budget.Expense", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="gl_matches",
    )
    match_confidence = models.CharField(
        max_length=15, choices=GLMatchConfidence.choices,
        default=GLMatchConfidence.UNMATCHED,
    )
    match_notes = models.TextField(blank=True)
    extracted_apartment = models.CharField(max_length=20, blank=True)
    extracted_bc_number = models.CharField(max_length=30, blank=True)
    is_validated = models.BooleanField(default=False)
    needs_import = models.BooleanField(
        default=False,
        help_text="True for coop worker expenses not yet in our system",
    )

    class Meta:
        ordering = ["upload", "row_number"]

    @property
    def net_amount(self):
        return self.debit - self.credit

    def __str__(self):
        return f"GL#{self.row_number}: {self.description_raw[:50]} ({self.debit})"


class ReconciliationResult(TimeStampedModel):
    """Summary of a Grand Livre reconciliation."""
    upload = models.OneToOneField(
        GrandLivreUpload, on_delete=models.CASCADE,
        related_name="reconciliation",
    )
    gl_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grille_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    difference = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    matched_count = models.PositiveIntegerField(default=0)
    unmatched_gl_count = models.PositiveIntegerField(default=0)
    missing_from_gl_count = models.PositiveIntegerField(default=0)
    is_balanced = models.BooleanField(default=False)
    anomalies = models.JSONField(default=list, blank=True)
    ai_analysis = models.TextField(blank=True)
    status_light = models.CharField(
        max_length=10, default="red",
        help_text="green, yellow, or red",
    )

    def __str__(self):
        return f"Reconciliation {self.upload} — {self.status_light}"


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

    @property
    def is_year_active(self):
        """True when the calendar year is current or future (regardless of the manual is_active flag)."""
        from django.utils import timezone
        return self.year >= timezone.now().year

    @property
    def is_inactive(self):
        """Budget is inactive when the calendar year is over OR manually deactivated/closed."""
        return not self.is_year_active or not self.is_active or self.is_closed


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
        return f"{self.trace_code} – {self.name}"


class Expense(TimeStampedModel):
    """
    A single entry in the budget ledger. Can be linked to a BonDeCommande
    (member purchase) or standalone (gestionnaire direct expense / GL import).
    Cancellations are stored as reversal rows with a negative amount.
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
    reimburse_to = models.CharField(
        max_length=10, blank=True, default="",
        help_text="member = rembourser le membre, supplier = rembourser le fournisseur"
    )
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
    is_cancellation = models.BooleanField(
        default=False,
        help_text="Vrai si cette entrée est une annulation d'une dépense précédente"
    )
    reversal_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="reversals",
        help_text="La dépense originale que cette entrée annule"
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
        if not self.is_cancellation and self.amount is not None and self.amount < 0:
            raise ValidationError(
                "Le montant ne peut pas être négatif (sauf pour les annulations)."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_cancelled(self):
        """True if a reversal entry exists for this expense."""
        return self.reversals.exists()

    @property
    def display_spent_by_label(self) -> str:
        """Show bon purchaser label when available, otherwise stored label."""
        if self.bon_de_commande_id:
            return self.bon_de_commande.purchaser_display_label
        return self.spent_by_label or "—"

    @property
    def display_approved_by_label(self) -> str:
        """Show bon approver/treasurer label when available."""
        if self.bon_de_commande_id:
            return self.bon_de_commande.effective_validator_display_label
        return "—"

    @property
    def display_reimburse_label(self) -> str:
        """Show Membre or Fournisseur for the grille column."""
        target = self.reimburse_to
        if not target and self.bon_de_commande_id:
            target = self.bon_de_commande.reimburse_to
        if target == "member":
            return "Membre"
        elif target == "supplier":
            return "Fournisseur"
        return ""

    def __str__(self):
        return f"{self.entry_date} · {self.description[:50]} · ${self.amount}"
