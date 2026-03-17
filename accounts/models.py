"""Authentication models for the project."""
from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Application user with a role baseline for future authorization work."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        TREASURER = "treasurer", "Treasurer"
        VIEWER = "viewer", "Viewer"

    ROLE_PRIORITY: ClassVar[dict[str, int]] = {
        Role.VIEWER: 10,
        Role.TREASURER: 20,
        Role.ADMIN: 30,
    }

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.VIEWER,
        help_text="Co-op application role used for business-level authorization.",
    )

    def has_minimum_role(self, role: str) -> bool:
        """Return whether the user satisfies an application role threshold."""
        try:
            required_priority = self.ROLE_PRIORITY[role]
        except KeyError as exc:
            raise ValueError(f"Unsupported role: {role}") from exc

        if not self.is_authenticated or not self.is_active:
            return False
        if self.is_superuser:
            return True
        return self.ROLE_PRIORITY[self.role] >= required_priority

    @property
    def is_app_admin(self) -> bool:
        """Return whether the user is treated as an application administrator."""
        return self.has_minimum_role(self.Role.ADMIN)

    @property
    def can_manage_financials(self) -> bool:
        """Return whether the user can manage treasurer workflows."""
        return self.has_minimum_role(self.Role.TREASURER)

    @property
    def can_view_financials(self) -> bool:
        """Return whether the user can view financial information."""
        return self.has_minimum_role(self.Role.VIEWER)
