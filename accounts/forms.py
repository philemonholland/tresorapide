"""Forms for account management."""
from __future__ import annotations

from django import forms
from django.contrib.auth.password_validation import validate_password

from accounts.models import Role, ROLE_PRIORITY, User
from houses.models import House
from members.models import Member


# Roles ordered by priority for display in dropdowns
ROLE_CHOICES_ORDERED = sorted(Role.choices, key=lambda c: ROLE_PRIORITY.get(c[0], 0))


class AccountCreateForm(forms.ModelForm):
    """Form for creating a new user account with role-based field filtering."""

    password1 = forms.CharField(
        label="Mot de passe",
        widget=forms.PasswordInput,
        help_text="Le mot de passe doit respecter les règles de validation Django.",
    )
    password2 = forms.CharField(
        label="Confirmer le mot de passe",
        widget=forms.PasswordInput,
    )

    class Meta:
        model = User
        fields = ["username", "role", "house", "member"]
        labels = {
            "username": "Nom d'utilisateur",
            "role": "Rôle",
            "house": "Maison",
            "member": "Membre associé",
        }
        help_texts = {
            "username": "Lettres, chiffres et @/./+/-/_ uniquement.",
            "member": "Optionnel — lien vers la fiche membre.",
        }

    def __init__(self, *args, creating_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.creating_user = creating_user

        # --- Filter role choices based on creating user ---
        if creating_user and creating_user.is_superuser:
            allowed_roles = ROLE_CHOICES_ORDERED
        elif creating_user and creating_user.role == Role.GESTIONNAIRE:
            max_priority = ROLE_PRIORITY[Role.ADMIN]
            allowed_roles = [
                (val, label) for val, label in ROLE_CHOICES_ORDERED
                if ROLE_PRIORITY.get(val, 0) <= max_priority
            ]
        elif creating_user and creating_user.role == Role.ADMIN:
            max_priority = ROLE_PRIORITY[Role.ADMIN]
            allowed_roles = [
                (val, label) for val, label in ROLE_CHOICES_ORDERED
                if ROLE_PRIORITY.get(val, 0) <= max_priority
            ]
        elif creating_user and creating_user.role == Role.TREASURER:
            max_priority = ROLE_PRIORITY[Role.TREASURER]
            allowed_roles = [
                (val, label) for val, label in ROLE_CHOICES_ORDERED
                if ROLE_PRIORITY.get(val, 0) <= max_priority
            ]
        else:
            allowed_roles = []

        self.fields["role"].choices = [("", "---------")] + allowed_roles

        # --- Filter house choices based on creating user ---
        if creating_user and (creating_user.is_superuser or creating_user.is_gestionnaire or creating_user.role == Role.ADMIN):
            self.fields["house"].queryset = House.objects.filter(is_active=True)
        elif creating_user and creating_user.house:
            self.fields["house"].queryset = House.objects.filter(pk=creating_user.house_id)
        else:
            self.fields["house"].queryset = House.objects.none()

        # Member is always optional
        self.fields["member"].required = False
        self.fields["member"].queryset = Member.objects.filter(is_active=True)

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Les deux mots de passe ne correspondent pas.")
        return p2

    def clean_password1(self):
        password = self.cleaned_data.get("password1")
        if password:
            validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        house = cleaned.get("house")

        # Roles below GESTIONNAIRE require a house
        if role and role != Role.GESTIONNAIRE and not house:
            self.add_error("house", "Une maison est requise pour ce rôle.")

        # Treasurer can only create for their own house
        if (
            self.creating_user
            and not self.creating_user.is_superuser
            and not self.creating_user.is_gestionnaire
            and self.creating_user.role == Role.TREASURER
            and house
            and house.pk != self.creating_user.house_id
        ):
            self.add_error("house", "Vous ne pouvez créer des comptes que pour votre propre maison.")

        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user
