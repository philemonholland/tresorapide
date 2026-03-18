from django import forms
from django.utils import timezone

from .models import BonDeCommande, BonStatus, ReceiptFile
from .services import generate_bon_number
from budget.models import BudgetYear, SubBudget
from members.models import Member


class BonDeCommandeForm(forms.ModelForm):
    class Meta:
        model = BonDeCommande
        fields = [
            "budget_year", "purchase_date", "short_description",
            "merchant_name", "supplier_name", "work_or_delivery_location",
            "sub_budget", "subtotal", "tps", "tvq", "total",
            "purchaser_member", "purchaser_apartment", "approver_member",
            "notes",
        ]
        labels = {
            "budget_year": "Année budgétaire",
            "purchase_date": "Date d'achat",
            "short_description": "Description courte",
            "merchant_name": "Marchand",
            "supplier_name": "Fournisseur",
            "work_or_delivery_location": "Lieu de travaux / livraison",
            "sub_budget": "Sous-budget",
            "subtotal": "Sous-total",
            "tps": "TPS (fédérale)",
            "tvq": "TVQ (provinciale)",
            "total": "Total",
            "purchaser_member": "Acheteur (membre)",
            "purchaser_apartment": "Appartement de l'acheteur",
            "approver_member": "Approbateur",
            "notes": "Notes",
        }
        widgets = {
            "purchase_date": forms.DateInput(attrs={"type": "date"}),
            "short_description": forms.TextInput(attrs={"placeholder": "ex : Achat de peinture"}),
            "merchant_name": forms.TextInput(attrs={"placeholder": "Optionnel"}),
            "supplier_name": forms.TextInput(attrs={"placeholder": "Optionnel"}),
            "work_or_delivery_location": forms.TextInput(attrs={"placeholder": "Optionnel"}),
            "subtotal": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "tps": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "tvq": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "total": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Remarques optionnelles"}),
        }

    # Essential fields at top; rest in collapsible section
    ESSENTIAL_FIELDS = ("purchase_date", "short_description", "total",
                        "sub_budget", "purchaser_member", "budget_year")
    DETAIL_FIELDS = ("merchant_name", "supplier_name", "work_or_delivery_location",
                     "subtotal", "tps", "tvq",
                     "purchaser_apartment", "approver_member", "notes")

    def __init__(self, *args, house=None, **kwargs):
        self.house = house
        super().__init__(*args, **kwargs)

        if not self.instance.pk:
            self.fields["purchase_date"].initial = timezone.now().date()

        if house:
            self.fields["budget_year"].queryset = BudgetYear.objects.filter(
                house=house, is_active=True
            )
            self.fields["sub_budget"].queryset = SubBudget.objects.filter(
                budget_year__house=house, is_active=True
            ).order_by("budget_year", "sort_order", "trace_code")
            self.fields["purchaser_apartment"].queryset = house.apartments.filter(
                is_active=True
            )
        else:
            self.fields["sub_budget"].queryset = SubBudget.objects.filter(
                is_active=True
            ).order_by("budget_year", "sort_order", "trace_code")

        self.fields["purchaser_member"].queryset = Member.objects.filter(is_active=True)
        self.fields["approver_member"].queryset = Member.objects.filter(is_active=True)


class ReceiptUploadForm(forms.Form):
    file = forms.FileField(
        label="Fichier (reçu / facture)",
        help_text="PDF, JPEG ou PNG acceptés.",
    )


class BonValidateForm(forms.Form):
    """Confirmation form for validating a bon de commande."""
    confirm = forms.BooleanField(
        required=True,
        label="Je confirme la validation de ce bon de commande",
    )


# ---------------------------------------------------------------------------
# OCR workflow forms
# ---------------------------------------------------------------------------

class MultiReceiptUploadForm(forms.Form):
    """Step 1: Upload multiple receipt files and select budget year."""
    budget_year = forms.ModelChoiceField(
        queryset=BudgetYear.objects.none(),
        label="Année budgétaire",
        empty_label="— Sélectionnez —",
    )
    files = forms.FileField(
        label="Reçus / factures",
        help_text="PDF, JPEG ou PNG acceptés. Vous pouvez sélectionner plusieurs fichiers.",
    )

    def __init__(self, *args, house=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Enable multiple file selection (must be set after init to avoid Django validation error)
        widget = self.fields["files"].widget
        widget.attrs.update({"multiple": True, "accept": ".pdf,.jpg,.jpeg,.png"})
        widget.allow_multiple_selected = True
        if house:
            self.fields["budget_year"].queryset = BudgetYear.objects.filter(
                house=house, is_active=True
            )
        else:
            self.fields["budget_year"].queryset = BudgetYear.objects.filter(is_active=True)


class OcrReviewForm(forms.Form):
    """Review/correct OCR-extracted data for a single receipt."""
    merchant_name = forms.CharField(
        max_length=200, required=False,
        label="Marchand",
    )
    purchase_date = forms.DateField(
        required=False,
        label="Date d'achat",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    subtotal = forms.DecimalField(
        max_digits=10, decimal_places=2, required=False,
        label="Sous-total",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    tps = forms.DecimalField(
        max_digits=10, decimal_places=2, required=False,
        label="TPS (fédérale)",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    tvq = forms.DecimalField(
        max_digits=10, decimal_places=2, required=False,
        label="TVQ (provinciale)",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    total = forms.DecimalField(
        max_digits=10, decimal_places=2, required=False,
        label="Total",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
