"""Audit models for important domain events."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from accounts.models import User
from core.models import NonDestructiveModel, TimeStampedModel


class AuditLogEntry(TimeStampedModel, NonDestructiveModel):
    """An append-only audit entry capturing who did what to which record."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_log_entries",
        blank=True,
        null=True,
    )
    actor_role_snapshot = models.CharField(max_length=20, blank=True)
    action = models.CharField(max_length=100)
    target_app_label = models.CharField(max_length=100)
    target_model = models.CharField(max_length=100)
    target_object_id = models.CharField(max_length=64)
    summary = models.CharField(max_length=255)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["target_app_label", "target_model", "target_object_id"]),
            models.Index(fields=["created_at"]),
        ]

    def save(self, *args: object, **kwargs: object) -> None:
        """Snapshot the actor role when available before saving."""
        if self.actor is not None and not self.actor_role_snapshot:
            user = self.actor
            if isinstance(user, User):
                self.actor_role_snapshot = user.role
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return a concise admin label."""
        return f"{self.action} on {self.target_app_label}.{self.target_model}:{self.target_object_id}"
