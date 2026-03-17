"""Server-rendered treasurer workflow forms for reimbursements."""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from budget.models import BudgetCategory, BudgetYear
from members.models import Apartment, Member
from reimbursements.models import ReceiptFile, Reimbursement, ReimbursementStatus
from reimbursements.services import FinalValidationInput


DATE_INPUT = forms.DateInput(attrs={"type": "date"})
MONEY_INPUT = forms.NumberInput(attrs={"step": "0.01", "min": "0"})


class BudgetCategoryChoiceField(forms.ModelChoiceField):
    """Show budget category labels with their year for treasurer clarity."""

    def label_from_instance(self, obj: BudgetCategory) -> str:
        """Format budget category labels for server-rendered selection lists."""
        return f"{obj.budget_year.label} / {obj.code} - {obj.name}"


class ReimbursementForm(forms.ModelForm):
    """Create or edit the core reimbursement record before final validation."""

    budget_category = BudgetCategoryChoiceField(queryset=BudgetCategory.objects.none())

    class Meta:
        model = Reimbursement
        fields = [
            "requested_by_member",
            "apartment",
            "budget_year",
            "budget_category",
            "title",
            "description",
            "expense_date",
            "amount_requested",
            "status",
            "notes",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "expense_date": DATE_INPUT,
            "amount_requested": MONEY_INPUT,
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Order chooser fields and keep the selected records available."""
        super().__init__(*args, **kwargs)
        selected_member_id = self.instance.requested_by_member_id if self.instance.pk else None
        selected_apartment_id = self.instance.apartment_id if self.instance.pk else None
        selected_category_id = self.instance.budget_category_id if self.instance.pk else None
        selected_year_id = self._selected_budget_year_id()
        self.fields["requested_by_member"].queryset = Member.objects.filter(
            Q(is_active=True) | Q(pk=selected_member_id)
        ).order_by("last_name", "first_name", "id")
        self.fields["apartment"].queryset = Apartment.objects.filter(
            Q(is_active=True) | Q(pk=selected_apartment_id)
        ).order_by("code", "id")
        self.fields["budget_year"].queryset = BudgetYear.objects.order_by(
            "-start_date",
            "-id",
        )
        category_queryset = BudgetCategory.objects.select_related("budget_year").filter(
            Q(is_active=True) | Q(pk=selected_category_id)
        )
        if selected_year_id is not None:
            category_queryset = category_queryset.filter(budget_year_id=selected_year_id)
        self.fields["budget_category"].queryset = category_queryset.order_by(
            "-budget_year__start_date",
            "sort_order",
            "code",
            "id",
        )
        self.fields["status"].choices = [
            (ReimbursementStatus.DRAFT, ReimbursementStatus.DRAFT.label),
            (ReimbursementStatus.SUBMITTED, ReimbursementStatus.SUBMITTED.label),
        ]
        self.fields["apartment"].required = False

    def _selected_budget_year_id(self) -> int | None:
        """Return the selected budget year from bound data, initial data, or instance."""
        if self.is_bound:
            raw_value = self.data.get(self.add_prefix("budget_year"), "").strip()
            if raw_value.isdigit():
                return int(raw_value)
            return None
        initial_value = self.initial.get("budget_year")
        if isinstance(initial_value, BudgetYear):
            return initial_value.pk
        if isinstance(initial_value, int):
            return initial_value
        return self.instance.budget_year_id if self.instance.pk else None

    def clean(self) -> dict[str, object]:
        """Keep the category aligned to the selected budget year."""
        cleaned_data = super().clean()
        budget_year = cleaned_data.get("budget_year")
        budget_category = cleaned_data.get("budget_category")
        if (
            isinstance(budget_year, BudgetYear)
            and isinstance(budget_category, BudgetCategory)
            and budget_category.budget_year_id != budget_year.pk
        ):
            self.add_error(
                "budget_category",
                "Budget category must belong to the selected budget year.",
            )
        return cleaned_data


class ReceiptUploadForm(forms.ModelForm):
    """Upload a receipt file into the reimbursement archive."""

    class Meta:
        model = ReceiptFile
        fields = ["file"]


class ReceiptArchiveForm(forms.Form):
    """Capture a treasurer-provided receipt archive reason."""

    reason = forms.CharField(
        label="Archive reason",
        widget=forms.Textarea(attrs={"rows": 2}),
        max_length=255,
    )


class ReimbursementFinalValidationForm(forms.Form):
    """Capture the explicit confirmations required by the workflow service."""

    approver_member = forms.ModelChoiceField(queryset=Member.objects.none())
    treasurer_member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        required=False,
        help_text=(
            "Optional member identity for the treasurer. When supplied, it must be "
            "different from the receipt approver."
        ),
    )
    signed_receipt_received = forms.BooleanField(required=False)
    signature_verified = forms.BooleanField(required=False)
    approved_amount = forms.DecimalField(
        required=False,
        decimal_places=2,
        max_digits=10,
        widget=MONEY_INPUT,
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(
        self,
        *args: object,
        reimbursement: Reimbursement,
        **kwargs: object,
    ) -> None:
        """Order member choices and preload the requested amount."""
        self.reimbursement = reimbursement
        super().__init__(*args, **kwargs)
        members_queryset = Member.objects.filter(is_active=True).order_by(
            "last_name",
            "first_name",
            "id",
        )
        self.fields["approver_member"].queryset = members_queryset.exclude(
            pk=reimbursement.requested_by_member_id
        )
        self.fields["treasurer_member"].queryset = members_queryset
        self.fields["approved_amount"].initial = reimbursement.amount_requested

    def clean(self) -> dict[str, object]:
        """Raise user-facing errors before the workflow service runs."""
        cleaned_data = super().clean()
        approver_member = cleaned_data.get("approver_member")
        treasurer_member = cleaned_data.get("treasurer_member")
        approved_amount = cleaned_data.get("approved_amount")

        if not cleaned_data.get("signed_receipt_received"):
            self.add_error(
                "signed_receipt_received",
                "Confirm that the signed receipt has been received.",
            )
        if not cleaned_data.get("signature_verified"):
            self.add_error(
                "signature_verified",
                "Confirm that the signature was verified.",
            )
        if approver_member is not None and approver_member.pk == self.reimbursement.requested_by_member_id:
            self.add_error(
                "approver_member",
                "Purchaser and approver must be different members.",
            )
        if (
            approver_member is not None
            and treasurer_member is not None
            and approver_member.pk == treasurer_member.pk
        ):
            self.add_error(
                "approver_member",
                "The receipt approver must be different from the treasurer member identity.",
            )
        if approved_amount is not None:
            if approved_amount <= Decimal("0.00"):
                self.add_error(
                    "approved_amount",
                    "Approved amount must be greater than zero.",
                )
            if approved_amount > self.reimbursement.amount_requested:
                self.add_error(
                    "approved_amount",
                    "Approved amount cannot exceed the requested amount.",
                )
        return cleaned_data

    def to_validation_input(self) -> FinalValidationInput:
        """Convert cleaned form data into the workflow service DTO."""
        if not self.is_valid():
            raise ValidationError("Validation input requested from an invalid form.")
        approved_amount = self.cleaned_data["approved_amount"]
        return FinalValidationInput(
            approver_member=self.cleaned_data["approver_member"],
            signed_receipt_received=self.cleaned_data["signed_receipt_received"],
            signature_verified=self.cleaned_data["signature_verified"],
            approved_amount=approved_amount,
            treasurer_member=self.cleaned_data["treasurer_member"],
            note=self.cleaned_data["note"],
        )


class ReimbursementVoidForm(forms.Form):
    """Capture a reason for voiding a reimbursement."""

    reason = forms.CharField(
        label="Void reason",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
