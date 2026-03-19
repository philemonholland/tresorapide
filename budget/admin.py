from django.contrib import admin
from .models import BudgetYear, SubBudget, Expense


class SubBudgetInline(admin.TabularInline):
    model = SubBudget
    extra = 0
    fields = ("trace_code", "name", "repeat_type", "planned_amount", "sort_order", "is_contingency", "is_active")


@admin.register(BudgetYear)
class BudgetYearAdmin(admin.ModelAdmin):
    list_display = ("label", "house", "year", "annual_budget_total", "snow_budget", "is_active", "is_closed")
    list_filter = ("house", "is_active", "is_closed")
    search_fields = ("label", "house__code", "house__name")
    inlines = [SubBudgetInline]


@admin.register(SubBudget)
class SubBudgetAdmin(admin.ModelAdmin):
    list_display = ("name", "trace_code", "budget_year", "planned_amount", "repeat_type", "is_contingency", "is_active")
    list_filter = ("budget_year__house", "repeat_type", "is_contingency", "is_active")
    search_fields = ("name",)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("entry_date", "description_short", "amount", "sub_budget", "source_type", "validated_gl")
    list_filter = ("budget_year__house", "source_type", "validated_gl", "budget_year")
    search_fields = ("description", "supplier_name", "bon_number")
    raw_id_fields = ("budget_year", "sub_budget", "bon_de_commande", "entered_by")
    date_hierarchy = "entry_date"

    def description_short(self, obj):
        return obj.description[:80]
    description_short.short_description = "Description"
