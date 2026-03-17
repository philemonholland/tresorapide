"""Admin registrations for budget app."""

from __future__ import annotations

from django.contrib import admin

from budget.models import BudgetCategory, BudgetYear


class BudgetCategoryInline(admin.TabularInline):
    """Inline category configuration per budget year."""

    model = BudgetCategory
    extra = 0


@admin.register(BudgetYear)
class BudgetYearAdmin(admin.ModelAdmin):
    """Admin configuration for budget years."""

    list_display = ("label", "start_date", "end_date", "is_closed")
    list_filter = ("is_closed",)
    search_fields = ("label",)
    inlines = (BudgetCategoryInline,)


@admin.register(BudgetCategory)
class BudgetCategoryAdmin(admin.ModelAdmin):
    """Admin configuration for budget categories."""

    list_display = ("code", "name", "budget_year", "planned_amount", "is_active", "sort_order")
    list_filter = ("budget_year", "is_active")
    search_fields = ("code", "name")
    autocomplete_fields = ("budget_year",)
