"""Server-rendered forms for treasurer member management workflows."""

from __future__ import annotations

from django import forms

from members.models import Apartment, Member, Residency


DATE_INPUT = forms.DateInput(attrs={"type": "date"})


class MemberForm(forms.ModelForm):
    """Edit member identity and contact information."""

    class Meta:
        model = Member
        fields = [
            "first_name",
            "last_name",
            "preferred_name",
            "email",
            "phone_number",
            "is_active",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }


class ApartmentForm(forms.ModelForm):
    """Edit apartment identifiers and occupancy notes."""

    class Meta:
        model = Apartment
        fields = [
            "code",
            "street_address",
            "is_active",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }


class ResidencyForm(forms.ModelForm):
    """Edit residency history windows with model-level overlap validation."""

    class Meta:
        model = Residency
        fields = [
            "member",
            "apartment",
            "start_date",
            "end_date",
            "notes",
        ]
        widgets = {
            "start_date": DATE_INPUT,
            "end_date": DATE_INPUT,
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Order reference data for predictable treasurer selection."""
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = Member.objects.order_by(
            "last_name",
            "first_name",
            "id",
        )
        self.fields["apartment"].queryset = Apartment.objects.order_by("code", "id")
