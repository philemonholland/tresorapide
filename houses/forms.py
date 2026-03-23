"""Forms for house management."""
from django import forms
from .models import House


class HouseForm(forms.ModelForm):
    class Meta:
        model = House
        fields = [
            "code", "accounting_code", "name", "account_number", "address",
            "phone", "email", "is_active",
            "treasurer_member", "correspondent_member", "notes",
        ]
        labels = {
            "code": "Code (2 caractères)",
            "accounting_code": "Code comptable",
            "name": "Nom de la maison",
            "account_number": "Numéro de compte",
            "address": "Adresse",
            "phone": "Téléphone",
            "email": "Courriel",
            "is_active": "Active",
            "treasurer_member": "Trésorier",
            "correspondent_member": "Correspondant",
            "notes": "Notes",
        }
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "ex : BB", "maxlength": 2}),
            "accounting_code": forms.TextInput(attrs={"placeholder": "ex : 13"}),
            "name": forms.TextInput(attrs={"placeholder": "ex : Maison BB"}),
            "account_number": forms.TextInput(attrs={"placeholder": "ex : 13-51200"}),
            "address": forms.Textarea(attrs={"rows": 2, "placeholder": "Adresse complète"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    ESSENTIAL_FIELDS = ("code", "accounting_code", "name", "account_number", "address")
    DETAIL_FIELDS = ("phone", "email", "is_active", "treasurer_member", "correspondent_member", "notes")
