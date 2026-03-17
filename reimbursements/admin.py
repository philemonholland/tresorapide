"""Admin registrations for reimbursements app."""

from __future__ import annotations

from django.contrib import admin

from reimbursements.models import ReceiptFile, Reimbursement


class ReceiptFileInline(admin.TabularInline):
    """Inline receipt files for a reimbursement."""

    model = ReceiptFile
    extra = 0
    readonly_fields = ("created_at", "updated_at", "archived_at")


@admin.register(Reimbursement)
class ReimbursementAdmin(admin.ModelAdmin):
    """Admin configuration for reimbursements."""

    list_display = (
        "reference_code",
        "member_name_snapshot",
        "budget_category_name_snapshot",
        "amount_requested",
        "status",
        "expense_date",
        "archived_at",
    )
    list_filter = ("status", "budget_year", "budget_category", "archived_at")
    search_fields = (
        "reference_code",
        "member_name_snapshot",
        "apartment_code_snapshot",
        "title",
    )
    autocomplete_fields = (
        "requested_by_member",
        "apartment",
        "budget_year",
        "budget_category",
        "created_by",
        "approved_by",
        "paid_by",
    )
    readonly_fields = (
        "reference_code",
        "member_name_snapshot",
        "member_email_snapshot",
        "apartment_code_snapshot",
        "apartment_display_snapshot",
        "residency_start_snapshot",
        "residency_end_snapshot",
        "budget_year_label_snapshot",
        "budget_category_code_snapshot",
        "budget_category_name_snapshot",
        "created_at",
        "updated_at",
        "archived_at",
    )
    inlines = (ReceiptFileInline,)

    def has_delete_permission(self, request, obj=None) -> bool:  # type: ignore[override]
        """Disable hard deletion for archive-sensitive financial records."""
        return False


@admin.register(ReceiptFile)
class ReceiptFileAdmin(admin.ModelAdmin):
    """Admin configuration for receipt files."""

    list_display = (
        "original_filename",
        "reimbursement",
        "ocr_status",
        "created_at",
        "archived_at",
    )
    list_filter = ("ocr_status", "archived_at")
    search_fields = ("original_filename", "reimbursement__reference_code")
    autocomplete_fields = ("reimbursement", "uploaded_by")
    readonly_fields = ("created_at", "updated_at", "archived_at")

    def has_delete_permission(self, request, obj=None) -> bool:  # type: ignore[override]
        """Disable hard deletion for receipt archive records."""
        return False
