"""Core membership and residency domain models."""

from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, QuerySet

from core.models import TimeStampedModel


class Member(TimeStampedModel):
    """A co-op member whose identity should remain stable for historical records."""

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    preferred_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Marks whether the member is currently active in operations.",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["last_name", "first_name", "id"]

    @property
    def full_name(self) -> str:
        """Return the full legal/display name."""
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def display_name(self) -> str:
        """Return a preferred display name when available."""
        if self.preferred_name:
            return f"{self.preferred_name} {self.last_name}".strip()
        return self.full_name

    def residency_on(self, on_date: date) -> Residency | None:
        """Return the member's residency active on the given date, if any."""
        return self.residencies.active_on(on_date).select_related("apartment").first()

    def __str__(self) -> str:
        """Return a human-readable label for admin and reporting surfaces."""
        return self.display_name


class Apartment(TimeStampedModel):
    """A co-op apartment or unit that can have changing occupants over time."""

    code = models.CharField(
        max_length=50,
        unique=True,
        help_text="Stable apartment identifier used in reports and PDFs.",
    )
    street_address = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    @property
    def display_name(self) -> str:
        """Return a stable display label for the apartment."""
        if self.street_address:
            return f"{self.code} — {self.street_address}"
        return self.code

    def residents_on(self, on_date: date) -> QuerySet[Member]:
        """Return members with residencies active in the apartment on a date."""
        return Member.objects.filter(residencies__in=self.residencies.active_on(on_date))

    def __str__(self) -> str:
        """Return a human-readable apartment label."""
        return self.display_name


class ResidencyQuerySet(models.QuerySet["Residency"]):
    """Query helpers for residency history."""

    def active_on(self, on_date: date) -> ResidencyQuerySet:
        """Filter residencies active on the given date."""
        return self.filter(start_date__lte=on_date).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=on_date)
        )

    def current(self) -> ResidencyQuerySet:
        """Filter residencies active today."""
        return self.active_on(date.today())


class Residency(TimeStampedModel):
    """A dated occupancy history record linking a member to an apartment."""

    member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name="residencies",
    )
    apartment = models.ForeignKey(
        Apartment,
        on_delete=models.PROTECT,
        related_name="residencies",
    )
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True)

    objects = ResidencyQuerySet.as_manager()

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["member", "apartment", "start_date"],
                name="uniq_member_apartment_residency_start",
            ),
        ]
        indexes = [
            models.Index(fields=["member", "start_date", "end_date"]),
            models.Index(fields=["apartment", "start_date", "end_date"]),
        ]

    def clean(self) -> None:
        """Enforce chronological and non-overlapping residency history per member."""
        super().clean()
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be earlier than start date."})

        overlapping_residencies = (
            Residency.objects.filter(member=self.member)
            .exclude(pk=self.pk)
            .filter(start_date__lte=self.end_date or date.max)
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=self.start_date))
        )
        if overlapping_residencies.exists():
            raise ValidationError(
                {
                    "start_date": (
                        "Residency periods for the same member cannot overlap. "
                        "Close the previous residency before creating a new one."
                    )
                }
            )

    @property
    def is_current(self) -> bool:
        """Return whether the residency is active today."""
        return self.is_active_on(date.today())

    def is_active_on(self, on_date: date) -> bool:
        """Return whether the residency is active on the provided date."""
        if self.start_date > on_date:
            return False
        if self.end_date is None:
            return True
        return self.end_date >= on_date

    def save(self, *args: object, **kwargs: object) -> None:
        """Validate the residency before persisting it."""
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return a compact residency description."""
        date_range = f"{self.start_date} to {self.end_date or 'present'}"
        return f"{self.member.display_name} @ {self.apartment.code} ({date_range})"
