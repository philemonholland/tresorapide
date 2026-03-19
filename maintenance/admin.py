from django.contrib import admin
from .models import MaintenancePlanItem


@admin.register(MaintenancePlanItem)
class MaintenancePlanItemAdmin(admin.ModelAdmin):
    list_display = ("maintenance_item_short", "apartment_label_snapshot", "responsible_party", "status", "budget_year")
    list_filter = ("status", "responsible_party", "budget_year")
    search_fields = ("maintenance_item", "apartment_label_snapshot", "comments")
    raw_id_fields = ("budget_year", "apartment", "linked_sub_budget")

    def maintenance_item_short(self, obj):
        return obj.maintenance_item[:80]
    maintenance_item_short.short_description = "Item"
