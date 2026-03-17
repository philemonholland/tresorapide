"""Budgeting domain models."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from core.models import TimeStampedModel


class BudgetYear(TimeStampedModel):
    """A reporting year used for budgeting and reimbursement classification."""

    label = models.CharField(
        max_length=50,
        unique=True,
        help_text="Human-readable label such as FY2025 or 2025.",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(blank=True, null=True)
    approved_reimbursement_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    paid_reimbursement_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "-id"]

    def clean(self) -> None:
        """Ensure the year range is valid."""
        super().clean()
        if self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be earlier than start date."})

    def save(self, *args: object, **kwargs: object) -> None:
        """Validate the budget year before saving it."""
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return the report label."""
        return self.label


class BudgetCategory(TimeStampedModel):
    """A reimbursement category within a specific budget year."""

    budget_year = models.ForeignKey(
        BudgetYear,
        on_delete=models.PROTECT,
        related_name="categories",
    )
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    planned_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Planned spend available for transparency and roll-up reporting.",
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    approved_reimbursement_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    paid_reimbursement_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    class Meta:
        ordering = ["budget_year__start_date", "sort_order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["budget_year", "code"],
                name="uniq_budget_category_code_per_year",
            ),
        ]

    def save(self, *args: object, **kwargs: object) -> None:
        """Normalize category codes and validate before saving."""
        self.code = self.code.strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return a compact category label."""
        return f"{self.budget_year.label} · {self.code} · {self.name}"
