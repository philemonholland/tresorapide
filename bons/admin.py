from django.contrib import admin
from .models import BonDeCommande, ReceiptFile, Merchant


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


class ReceiptFileInline(admin.TabularInline):
    model = ReceiptFile
    extra = 0
    fields = ("file", "original_filename", "ocr_status")
    readonly_fields = ("original_filename", "ocr_status")


@admin.register(BonDeCommande)
class BonDeCommandeAdmin(admin.ModelAdmin):
    list_display = ("number", "purchase_date", "short_description", "total", "status", "house", "purchaser_name_snapshot")
    list_filter = ("status", "house", "budget_year")
    search_fields = ("number", "short_description", "merchant_name", "supplier_name", "purchaser_name_snapshot")
    raw_id_fields = ("house", "budget_year", "sub_budget", "purchaser_member", "purchaser_apartment",
                     "approver_member", "approver_apartment", "created_by", "validated_by", "signature_verified_by")
    date_hierarchy = "purchase_date"
    inlines = [ReceiptFileInline]
    fieldsets = (
        ("Identification", {
            "fields": ("number", "house", "budget_year", "status")
        }),
        ("Détails de l'achat", {
            "fields": ("purchase_date", "short_description", "merchant_name", "supplier_name",
                       "work_or_delivery_location", "claimant_address", "claimant_phone")
        }),
        ("Budget", {
            "fields": ("sub_budget", "subtotal", "tps", "tvq", "total")
        }),
        ("Personnes", {
            "fields": ("purchaser_member", "purchaser_apartment", "approver_member", "approver_apartment")
        }),
        ("Validation", {
            "fields": ("signature_verified", "signature_verified_by", "validated_by", "validated_at")
        }),
        ("Instantanés", {
            "classes": ("collapse",),
            "fields": ("purchaser_name_snapshot", "purchaser_unit_snapshot", "purchaser_phone_snapshot",
                       "approver_name_snapshot", "approver_unit_snapshot",
                       "budget_year_label_snapshot", "sub_budget_name_snapshot")
        }),
        ("Statut et notes", {
            "fields": ("void_reason", "historical_member_unmatched", "notes")
        }),
    )
