from django.db import models
from django.core.exceptions import ValidationError
from core.models import TimeStampedModel


class Member(TimeStampedModel):
    """
    A coop member. Not tied to a specific house — their house is derived
    from their active Residency → Apartment → House chain. This allows
    members to move between houses while preserving historical records.
    """
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    preferred_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["last_name", "first_name", "id"]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def display_name(self):
        return self.preferred_name if self.preferred_name else self.full_name

    def current_residency(self):
        """Returns the active residency (no end_date), or None."""
        return self.residencies.filter(end_date__isnull=True).order_by('-start_date').first()

    def current_house(self):
        """Derives the member's current house from their active residency."""
        residency = self.current_residency()
        return residency.apartment.house if residency else None

    def current_apartment(self):
        """Derives the member's current apartment from their active residency."""
        residency = self.current_residency()
        return residency.apartment if residency else None

    def residency_on(self, on_date):
        """Returns the residency active on the given date, or None."""
        return self.residencies.filter(
            start_date__lte=on_date
        ).filter(
            models.Q(end_date__isnull=True) | models.Q(end_date__gte=on_date)
        ).order_by('-start_date').first()

    def __str__(self):
        return self.display_name


class Apartment(TimeStampedModel):
    """A physical unit in a house. Stable over time."""
    house = models.ForeignKey(
        "houses.House", on_delete=models.PROTECT, related_name="apartments"
    )
    code = models.CharField(
        max_length=10,
        help_text="Identifiant de l'unité dans la maison, ex : '101', '202', 'BB'"
    )
    street_address = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["house__code", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["house", "code"],
                name="unique_apartment_per_house"
            )
        ]

    @property
    def display_name(self):
        if self.street_address:
            return f"{self.code} — {self.street_address}"
        return self.code

    def residents_on(self, on_date):
        """Returns members with active residencies on the given date."""
        from django.db.models import Q
        residencies = self.residencies.filter(
            start_date__lte=on_date
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=on_date)
        )
        return Member.objects.filter(
            id__in=residencies.values_list("member_id", flat=True)
        )

    def __str__(self):
        return f"{self.house.code}-{self.code}"


class ResidencyQuerySet(models.QuerySet):
    def active_on(self, on_date):
        from django.db.models import Q
        return self.filter(
            start_date__lte=on_date
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=on_date)
        )

    def current(self):
        from django.utils import timezone
        return self.active_on(timezone.now().date())


class Residency(TimeStampedModel):
    """
    Historical link between a member and an apartment.
    A null end_date means the member currently lives there.
    """
    member = models.ForeignKey(
        Member, on_delete=models.PROTECT, related_name="residencies"
    )
    apartment = models.ForeignKey(
        Apartment, on_delete=models.PROTECT, related_name="residencies"
    )
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    is_primary_contact = models.BooleanField(default=False)
    is_coop_member = models.BooleanField(
        default=True,
        help_text="Si décoché, la personne habite encore l'appartement mais n'est plus membre de la coop."
    )
    notes = models.TextField(blank=True)

    objects = ResidencyQuerySet.as_manager()

    class Meta:
        ordering = ["-start_date", "-id"]
        verbose_name_plural = "résidences"
        constraints = [
            models.UniqueConstraint(
                fields=["member", "apartment", "start_date"],
                name="unique_residency_start"
            )
        ]
        indexes = [
            models.Index(fields=["member", "start_date", "end_date"]),
            models.Index(fields=["apartment", "start_date", "end_date"]),
        ]

    @property
    def is_current(self):
        from django.utils import timezone
        today = timezone.now().date()
        if self.end_date:
            return self.start_date <= today <= self.end_date
        return self.start_date <= today

    def is_active_on(self, on_date):
        if self.end_date:
            return self.start_date <= on_date <= self.end_date
        return self.start_date <= on_date

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("La date de fin ne peut pas être antérieure à la date de début.")
        # Check for overlapping residencies for the same member
        overlapping = Residency.objects.filter(
            member=self.member
        ).exclude(pk=self.pk)
        for r in overlapping:
            if self._overlaps(r):
                raise ValidationError(
                    f"Résidence chevauchante : {r.apartment} "
                    f"({r.start_date} – {r.end_date or 'présent'})"
                )

    def _overlaps(self, other):
        """Check if this residency overlaps with another."""
        if self.end_date is None and other.end_date is None:
            return True  # Both open-ended
        if self.end_date is None:
            return other.end_date >= self.start_date
        if other.end_date is None:
            return self.end_date >= other.start_date
        return self.start_date <= other.end_date and other.start_date <= self.end_date

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        end = self.end_date or "présent"
        return f"{self.member.display_name} @ {self.apartment} ({self.start_date} – {end})"
