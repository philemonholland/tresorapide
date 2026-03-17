"""Service helpers for creating append-only audit log records."""

from __future__ import annotations

from typing import Any

from django.db import models

from accounts.models import User
from audits.models import AuditLogEntry


def create_audit_log_entry(
    *,
    action: str,
    target: models.Model,
    summary: str,
    actor: User | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditLogEntry:
    """Create an audit log entry for a concrete model instance."""

    return AuditLogEntry.objects.create(
        actor=actor,
        action=action,
        target_app_label=target._meta.app_label,
        target_model=target._meta.model_name,
        target_object_id=str(target.pk),
        summary=summary,
        payload=payload or {},
    )
