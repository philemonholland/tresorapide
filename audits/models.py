from django.db import models
from core.models import TimeStampedModel, NonDestructiveModel


class AuditLogEntry(TimeStampedModel, NonDestructiveModel):
    """Immutable, append-only audit trail for all material actions."""
    actor = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="audit_log_entries"
    )
    actor_role_snapshot = models.CharField(max_length=20, blank=True)
    action = models.CharField(
        max_length=100,
        help_text="ex : 'bon.validated', 'expense.created'"
    )
    target_app_label = models.CharField(max_length=100)
    target_model = models.CharField(max_length=100)
    target_object_id = models.CharField(max_length=64)
    summary = models.CharField(max_length=255)
    payload = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["target_app_label", "target_model", "target_object_id"]),
            models.Index(fields=["created_at"]),
        ]

    def save(self, *args, **kwargs):
        if self.actor and not self.actor_role_snapshot:
            self.actor_role_snapshot = self.actor.role
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.action} on {self.target_app_label}.{self.target_model}:{self.target_object_id}"
