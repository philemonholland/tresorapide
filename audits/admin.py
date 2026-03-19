from django.contrib import admin
from .models import AuditLogEntry


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    list_display = ("action", "target_model", "target_object_id", "actor", "created_at")
    list_filter = ("action", "target_app_label", "target_model")
    search_fields = ("action", "summary", "target_object_id")
    readonly_fields = ("actor", "actor_role_snapshot", "action", "target_app_label",
                       "target_model", "target_object_id", "summary", "payload",
                       "ip_address", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
