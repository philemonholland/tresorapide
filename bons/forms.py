from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import BonDeCommande, BonStatus, ReceiptFile
from .services import generate_bon_number
from budget.models import BudgetYear, SubBudget
from members.models import Member


class MultipleFileField(forms.FileField):
    """FileField that properly handles lists from widgets with allow_multiple_selected."""

    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            if not data:
                if self.required:
                    raise ValidationError(self.error_messages["required"])
                return []
            return [super().clean(item, initial) for item in data]
        return [super().clean(data, initial)] if data else []


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


ALLOWED_RECEIPT_TYPES = {
    "application/pdf", "image/jpeg", "image/png",
}
MAX_RECEIPT_SIZE = 10 * 1024 * 1024  # 10 MB


def _validate_receipt_file(f):
    """Server-side validation for receipt file type and size."""
    if f.size > MAX_RECEIPT_SIZE:
        raise forms.ValidationError(
            f"Fichier trop volumineux ({f.size // (1024*1024)} Mo). Maximum : 10 Mo."
        )
    if f.content_type not in ALLOWED_RECEIPT_TYPES:
        raise forms.ValidationError(
            f"Type de fichier non autorisé ({f.content_type}). "
            "Seuls les PDF, JPEG et PNG sont acceptés."
        )
    return f


class ReceiptUploadForm(forms.Form):
    file = forms.FileField(
        label="Fichier (reçu / facture)",
        help_text="PDF, JPEG ou PNG acceptés (max 10 Mo).",
    )

    def clean_file(self):
        return _validate_receipt_file(self.cleaned_data["file"])


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
    files = MultipleFileField(
        label="Reçus / factures",
        help_text="PDF, JPEG ou PNG acceptés (max 10 Mo chaque). Vous pouvez sélectionner plusieurs fichiers.",
        widget=forms.FileInput(),
        required=True,
    )

    def __init__(self, *args, house=None, **kwargs):
        super().__init__(*args, **kwargs)
        widget = self.fields["files"].widget
        widget.attrs["multiple"] = True
        widget.attrs["accept"] = ".pdf,.jpg,.jpeg,.png"
        widget.allow_multiple_selected = True
        if house:
            self.fields["budget_year"].queryset = BudgetYear.objects.filter(
                house=house, is_active=True
            )
        else:
            self.fields["budget_year"].queryset = BudgetYear.objects.filter(is_active=True)

    def clean_files(self):
        files = self.cleaned_data.get("files", [])
        for f in files:
            _validate_receipt_file(f)
        return files


class OcrReviewForm(forms.Form):
    """Review/correct GPT-extracted data for a single receipt."""
    member_name_raw = forms.CharField(
        max_length=200, required=False,
        label="Nom extrait (IA)",
        widget=forms.TextInput(attrs={"readonly": "readonly", "class": "text-muted"}),
        help_text="Nom manuscrit lu par l'IA (lecture seule)",
    )
    apartment_number = forms.CharField(
        max_length=10, required=False,
        label="Appartement",
        widget=forms.TextInput(attrs={"placeholder": "ex : 307"}),
    )
    purchaser_member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        required=True,
        label="Membre (acheteur)",
        empty_label="-- Choisir un membre --",
    )
    matched_member_id = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(),
    )
    sub_budget = forms.ModelChoiceField(
        queryset=SubBudget.objects.none(),
        required=True,
        label="Sous-budget",
        empty_label="-- Choisir un sous-budget --",
    )
    merchant_name = forms.CharField(
        max_length=200, required=False,
        label="Marchand",
    )
    purchase_date = forms.DateField(
        required=False,
        label="Date d'achat",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        input_formats=["%Y-%m-%d"],
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
    summary = forms.CharField(
        max_length=255, required=False,
        label="Résumé des achats",
        widget=forms.TextInput(attrs={"placeholder": "Ex: Quincaillerie - vis et peinture"}),
        help_text="Résumé généré par l'IA, modifiable par le trésorier.",
    )


# ---------------------------------------------------------------------------
# Search form
# ---------------------------------------------------------------------------

class BonSearchForm(forms.Form):
    """Powerful yet user-friendly search for bons de commande."""
    q = forms.CharField(
        required=False,
        label="Recherche libre",
        widget=forms.TextInput(attrs={
            "placeholder": "No bon, description, marchand…",
            "autofocus": True,
        }),
    )
    house = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="Maison",
        empty_label="Toutes les maisons",
    )
    budget_year = forms.ModelChoiceField(
        queryset=BudgetYear.objects.none(),
        required=False,
        label="Année budgétaire",
        empty_label="Toutes les années",
    )
    purchaser = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        required=False,
        label="Membre (acheteur)",
        empty_label="Tous les membres",
    )
    merchant = forms.CharField(
        required=False,
        label="Marchand",
        widget=forms.TextInput(attrs={"placeholder": "Nom du marchand"}),
    )
    amount_min = forms.DecimalField(
        required=False, decimal_places=2,
        label="Montant min ($)",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    amount_max = forms.DecimalField(
        required=False, decimal_places=2,
        label="Montant max ($)",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    date_from = forms.DateField(
        required=False,
        label="Date du",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        input_formats=["%Y-%m-%d"],
    )
    date_to = forms.DateField(
        required=False,
        label="Date au",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        input_formats=["%Y-%m-%d"],
    )
    SEARCH_STATUSES = [
        (v, l) for v, l in BonStatus.choices
        if v != BonStatus.OCR_PENDING
    ]

    status = forms.ChoiceField(
        required=False,
        label="Statut",
        choices=[("", "Tous les statuts")] + SEARCH_STATUSES,
    )

    def __init__(self, *args, user=None, **kwargs):
        from houses.models import House
        super().__init__(*args, **kwargs)
        self.fields["house"].queryset = House.objects.filter(is_active=True).order_by("code")
        self.fields["budget_year"].queryset = BudgetYear.objects.order_by("-year")
        self.fields["purchaser"].queryset = Member.objects.filter(
            is_active=True
        ).order_by("last_name", "first_name")

        # Pre-select user's house if available
        if user and hasattr(user, 'house') and user.house and not self.is_bound:
            self.fields["house"].initial = user.house.pk
