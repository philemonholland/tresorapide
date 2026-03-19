from django.db import models
from django.core.exceptions import ValidationError


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ArchivableModel(TimeStampedModel):
    archived_at = models.DateTimeField(blank=True, null=True)
    archive_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        abstract = True

    @property
    def is_archived(self):
        return self.archived_at is not None

    def archive(self, reason=""):
        from django.utils import timezone
        self.archived_at = timezone.now()
        self.archive_reason = reason
        self.save(update_fields=["archived_at", "archive_reason", "updated_at"])


class NonDestructiveModel(models.Model):
    """Empêche la suppression définitive des enregistrements faisant partie du journal d'audit."""
    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Cet enregistrement ne peut pas être supprimé. Utilisez l'archivage ou l'annulation."
        )
