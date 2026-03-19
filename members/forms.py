"""Member, apartment, and residency forms."""
from django import forms

from .models import Apartment, Member, Residency


class MemberForm(forms.ModelForm):
    apartment_code = forms.CharField(
        max_length=10,
        label="Numéro d'appartement",
        required=True,
        help_text="ex : 101, 202",
        widget=forms.TextInput(attrs={"placeholder": "ex : 101"}),
    )

    class Meta:
        model = Member
        fields = [
            "first_name", "last_name", "preferred_name",
            "email", "phone_number", "notes",
        ]
        labels = {
            "first_name": "Prénom",
            "last_name": "Nom de famille",
            "preferred_name": "Nom préféré",
            "email": "Courriel",
            "phone_number": "Téléphone",
            "notes": "Notes",
        }
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "Prénom du membre"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Nom de famille"}),
            "preferred_name": forms.TextInput(attrs={"placeholder": "Optionnel"}),
            "email": forms.EmailInput(attrs={"placeholder": "Optionnel"}),
            "phone_number": forms.TextInput(attrs={"placeholder": "Optionnel"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Remarques optionnelles"}),
        }

    # Essential fields shown prominently; rest in collapsible
    ESSENTIAL_FIELDS = ("first_name", "last_name", "apartment_code")
    DETAIL_FIELDS = ("preferred_name", "email", "phone_number", "notes")

    def __init__(self, *args, is_update=False, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if is_update:
            if "apartment_code" in self.fields:
                del self.fields["apartment_code"]
        # Only admin/superuser can toggle is_active
        if user and (user.is_app_admin or user.is_superuser):
            self.fields["is_active"] = forms.BooleanField(
                required=False, initial=True, label="Actif dans la coop",
                help_text="Seul un administrateur peut désactiver un membre.",
            )


class ApartmentForm(forms.ModelForm):
    class Meta:
        model = Apartment
        fields = ["house", "code", "street_address", "is_active", "notes"]
        labels = {
            "house": "Maison",
            "code": "Code",
            "street_address": "Adresse",
            "is_active": "Actif",
            "notes": "Notes",
        }
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "ex: 101, 202"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and not user.is_gestionnaire and user.house:
            self.fields["house"].initial = user.house
            self.fields["house"].widget = self.fields["house"].hidden_widget()


class ResidencyForm(forms.ModelForm):
    class Meta:
        model = Residency
        fields = [
            "member", "apartment", "start_date", "end_date",
            "is_primary_contact", "is_coop_member", "notes",
        ]
        labels = {
            "member": "Membre",
            "apartment": "Appartement",
            "start_date": "Date de début",
            "end_date": "Date de fin",
            "is_primary_contact": "Contact principal",
            "is_coop_member": "Membre de la coop",
            "notes": "Notes",
        }
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and not user.is_gestionnaire and user.house:
            self.fields["apartment"].queryset = Apartment.objects.filter(
                house=user.house, is_active=True,
            ).select_related("house")
