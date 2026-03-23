from django.db import models
from core.models import TimeStampedModel


class House(TimeStampedModel):
    code = models.CharField(
        max_length=2, unique=True,
        help_text="Code de 2 caractères alphanumériques, ex : 'BB'"
    )
    accounting_code = models.CharField(
        max_length=10, blank=True,
        help_text="Code comptable court du tableau coop, ex : '13'"
    )
    name = models.CharField(max_length=150, help_text="ex : 'Maison BB'")
    account_number = models.CharField(
        max_length=10, unique=True,
        help_text="Numéro de compte coop à 7 car., ex : '13-51200'"
    )
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    treasurer_member = models.ForeignKey(
        "members.Member", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="treasurer_of_houses",
        help_text="Membre trésorier de cette maison"
    )
    correspondent_member = models.ForeignKey(
        "members.Member", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="correspondent_of_houses",
        help_text="Membre correspondant de cette maison"
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"
