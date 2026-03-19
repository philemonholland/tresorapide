from .models import AuditLogEntry


def create_audit_log_entry(action, target, summary, actor=None, payload=None, ip_address=None):
    """Create an append-only audit log entry."""
    return AuditLogEntry.objects.create(
        actor=actor,
        action=action,
        target_app_label=target._meta.app_label,
        target_model=target._meta.model_name,
        target_object_id=str(target.pk),
        summary=summary,
        payload=payload or {},
        ip_address=ip_address,
    )
