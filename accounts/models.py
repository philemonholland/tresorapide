from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    VIEWER = "VIEWER", "Lecteur"
    TREASURER = "TREASURER", "Trésorier"
    ADMIN = "ADMIN", "Administrateur"
    GESTIONNAIRE = "GESTIONNAIRE", "Gestionnaire"


ROLE_PRIORITY = {
    Role.VIEWER: 10,
    Role.TREASURER: 20,
    Role.ADMIN: 30,
    Role.GESTIONNAIRE: 40,
}


class User(AbstractUser):
    Role = Role  # backward-compatible alias for User.Role.X access

    role = models.CharField(
        max_length=20, choices=Role.choices, default=Role.VIEWER
    )
    house = models.ForeignKey(
        "houses.House",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="users",
        help_text="Required for VIEWER/TREASURER/ADMIN. Null for GESTIONNAIRE."
    )
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="user_accounts",
        help_text="Lien optionnel vers la fiche membre de cet utilisateur."
    )

    class Meta:
        ordering = ["username"]

    def has_minimum_role(self, role):
        if self.is_superuser:
            return True
        return ROLE_PRIORITY.get(self.role, 0) >= ROLE_PRIORITY.get(role, 0)

    @property
    def is_app_admin(self):
        return self.has_minimum_role(Role.ADMIN) or self.is_superuser

    @property
    def can_manage_financials(self):
        return self.has_minimum_role(Role.TREASURER) or self.is_superuser

    @property
    def can_view_financials(self):
        return self.has_minimum_role(Role.VIEWER) or self.is_superuser

    @property
    def is_gestionnaire(self):
        return self.role == Role.GESTIONNAIRE or self.is_superuser

    def __str__(self):
        house_label = f" ({self.house.code})" if self.house else ""
        return f"{self.username}{house_label} [{self.get_role_display()}]"
