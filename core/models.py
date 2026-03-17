"""Shared abstract models used across the project."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Abstract base model that tracks creation and update timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ArchivableModel(TimeStampedModel):
    """Abstract model that supports soft archival instead of deletion."""

    archived_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When set, the record has been archived and should stay read-only.",
    )
    archive_reason = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional human-readable reason explaining why the record was archived.",
    )

    class Meta:
        abstract = True

    @property
    def is_archived(self) -> bool:
        """Return whether the record has been archived."""
        return self.archived_at is not None

    def archive(self, *, reason: str = "") -> None:
        """Archive the record without destroying historical data."""
        self.archived_at = timezone.now()
        self.archive_reason = reason


class NonDestructiveModel(models.Model):
    """Abstract model that prevents hard deletion in normal application code."""

    class Meta:
        abstract = True

    def delete(self, *args: object, **kwargs: object) -> tuple[int, dict[str, int]]:
        """Reject direct deletion because the data is part of the archive trail."""
        raise ValidationError(
            f"{self.__class__.__name__} records must be archived or voided, not deleted."
        )
