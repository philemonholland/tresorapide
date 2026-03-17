"""Admin registrations for audits app."""

from __future__ import annotations

from django.contrib import admin

from audits.models import AuditLogEntry


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    """Admin configuration for append-only audit records."""

    list_display = (
        "action",
        "target_app_label",
        "target_model",
        "target_object_id",
        "actor",
        "created_at",
    )
    list_filter = ("action", "target_app_label", "target_model", "actor_role_snapshot")
    search_fields = ("summary", "target_object_id", "actor__username")
    autocomplete_fields = ("actor",)
    readonly_fields = (
        "actor",
        "actor_role_snapshot",
        "action",
        "target_app_label",
        "target_model",
        "target_object_id",
        "summary",
        "payload",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:  # type: ignore[override]
        """Prevent manual audit entry creation through the admin UI."""

        return False

    def has_change_permission(self, request, obj=None) -> bool:  # type: ignore[override]
        """Keep audit entries view-only inside Django admin."""

        return False

    def has_delete_permission(self, request, obj=None) -> bool:  # type: ignore[override]
        """Disable hard deletion for audit records."""
        return False
