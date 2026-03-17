"""Treasurer-facing forms for budget year and category maintenance."""

from __future__ import annotations

from django import forms
from django.utils import timezone

from budget.models import BudgetCategory, BudgetYear


DATE_INPUT = forms.DateInput(attrs={"type": "date"})


class BudgetYearForm(forms.ModelForm):
    """Edit budget year metadata used across reimbursement workflows."""

    class Meta:
        model = BudgetYear
        fields = [
            "label",
            "start_date",
            "end_date",
            "is_closed",
            "notes",
        ]
        widgets = {
            "start_date": DATE_INPUT,
            "end_date": DATE_INPUT,
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def save(self, commit: bool = True) -> BudgetYear:
        """Maintain the closed timestamp when treasurers toggle closure."""
        budget_year = super().save(commit=False)
        if budget_year.is_closed and budget_year.closed_at is None:
            budget_year.closed_at = timezone.now()
        elif not budget_year.is_closed:
            budget_year.closed_at = None
        if commit:
            budget_year.save()
            self.save_m2m()
        return budget_year


class BudgetCategoryForm(forms.ModelForm):
    """Edit budget category planning metadata."""

    class Meta:
        model = BudgetCategory
        fields = [
            "budget_year",
            "code",
            "name",
            "description",
            "planned_amount",
            "sort_order",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Order budget years predictably for treasurer data entry."""
        super().__init__(*args, **kwargs)
        self.fields["budget_year"].queryset = BudgetYear.objects.order_by(
            "-start_date",
            "-id",
        )
