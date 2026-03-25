import datetime

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
            "sub_budget", "subtotal", "tps", "tvq", "untaxed_extra_amount", "total",
            "purchaser_member", "purchaser_apartment",
            "approver_member", "approver_apartment",
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
            "untaxed_extra_amount": "Frais non taxables",
            "total": "Total",
            "purchaser_member": "Acheteur (membre)",
            "purchaser_apartment": "Appartement de l'acheteur",
            "approver_member": "Approbateur",
            "approver_apartment": "Appartement du validateur",
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
            "untaxed_extra_amount": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "total": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Remarques optionnelles"}),
        }

    # Essential fields at top; rest in collapsible section
    ESSENTIAL_FIELDS = ("purchase_date", "short_description", "total",
                        "sub_budget", "purchaser_member", "budget_year")
    DETAIL_FIELDS = ("merchant_name", "supplier_name", "work_or_delivery_location",
                     "subtotal", "tps", "tvq", "untaxed_extra_amount",
                     "purchaser_apartment", "approver_member", "approver_apartment", "notes")

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
            self.fields["approver_apartment"].queryset = house.apartments.filter(
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
ALLOWED_MOBILE_CAPTURE_TYPES = {
    "image/jpeg", "image/png",
}
MAX_RECEIPT_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_MOBILE_CAPTURE_SIZE = 20 * 1024 * 1024  # 20 MB


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


def _validate_mobile_capture_file(f):
    """Server-side validation for handheld capture photos."""
    if f.size > MAX_MOBILE_CAPTURE_SIZE:
        raise forms.ValidationError(
            f"Photo trop volumineuse ({f.size // (1024*1024)} Mo). Maximum : 20 Mo."
        )
    if f.content_type not in ALLOWED_MOBILE_CAPTURE_TYPES:
        raise forms.ValidationError(
            "La capture mobile accepte seulement les photos JPEG ou PNG."
        )
    return f


def _budget_year_queryset(house):
    if house:
        return BudgetYear.objects.filter(
            house=house,
            is_active=True,
        ).order_by("-year")
    return BudgetYear.objects.filter(is_active=True).order_by("-year")


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
    confirm_duplicates = forms.BooleanField(
        required=False,
        label="Je confirme vouloir valider malgré les doublons détectés",
    )
    cleanup_bon_ids = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        help_text="Comma-separated bon PKs to void as unvalidated duplicates",
    )


class BonExportConfigureForm(forms.Form):
    """Choose bon export format and optional AI confidence inclusion."""

    EXPORT_FORMAT_CHOICES = [
        ("pdf", "PDF"),
        ("xlsx", "Excel"),
    ]

    export_format = forms.ChoiceField(
        choices=EXPORT_FORMAT_CHOICES,
        initial="pdf",
        label="Format d'export",
    )
    include_ai_confidence = forms.BooleanField(
        required=False,
        label="Inclure les scores de confiance IA",
        help_text=(
            "Ajoute une section ou une feuille distincte avec les scores 0-9 ou NA "
            "associés aux champs issus de l'IA."
        ),
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
        qs = _budget_year_queryset(house)
        self.fields["budget_year"].queryset = qs
        # Pre-select current year budget
        from datetime import date
        current_year_by = qs.filter(year=date.today().year).first()
        if current_year_by and not self.initial.get("budget_year"):
            self.initial["budget_year"] = current_year_by.pk

    def clean_files(self):
        files = self.cleaned_data.get("files", [])
        for f in files:
            _validate_receipt_file(f)
        return files


class MobileReceiptCaptureForm(forms.Form):
    """Capture one handheld photo at a time into a scan session."""

    budget_year = forms.ModelChoiceField(
        queryset=BudgetYear.objects.none(),
        label="Année budgétaire",
        empty_label="— Sélectionnez —",
    )
    photo = forms.FileField(
        label="Photo",
        help_text=(
            "Photo JPEG ou PNG (max 20 Mo). Prenez une photo avec la caméra ou "
            "choisissez une image de votre galerie."
        ),
        widget=forms.FileInput(
            attrs={
                "accept": "image/jpeg,image/png,image/*",
                "data-mobile-native-camera": "true",
            }
        ),
        required=True,
    )

    def __init__(self, *args, house=None, locked_budget_year=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = _budget_year_queryset(house)
        self.fields["budget_year"].queryset = qs
        if locked_budget_year is not None:
            self.initial["budget_year"] = locked_budget_year.pk
            self.fields["budget_year"].widget = forms.HiddenInput()
        else:
            current_year = timezone.now().year
            current_year_by = qs.filter(year=current_year).first()
            if current_year_by and not self.initial.get("budget_year"):
                self.initial["budget_year"] = current_year_by.pk

    def clean_photo(self):
        return _validate_mobile_capture_file(self.cleaned_data["photo"])


class OcrReviewForm(forms.Form):
    """Review/correct GPT-extracted data for a single receipt."""

    DOCUMENT_TYPE_CHOICES = [
        ("receipt", "Reçu"),
        ("paper_bc", "Bon de commande papier"),
        ("invoice", "Facture"),
    ]

    document_type = forms.ChoiceField(
        choices=DOCUMENT_TYPE_CHOICES,
        required=True,
        label="Type de document",
        widget=forms.Select(attrs={"id": "id_document_type"}),
    )
    bc_number = forms.CharField(
        max_length=20, required=False, label="N° bon de commande",
    )
    associated_bc_number = forms.CharField(
        max_length=20, required=False, label="N° BC associé",
    )
    supplier_name = forms.CharField(
        max_length=200, required=False, label="Fournisseur",
    )
    supplier_address = forms.CharField(
        max_length=300, required=False, label="Adresse du fournisseur",
    )
    reimburse_to = forms.ChoiceField(
        choices=[("", "—"), ("member", "Membre"), ("supplier", "Fournisseur")],
        required=False,
        label="Rembourser",
        help_text="Qui doit être remboursé : le membre ou le fournisseur.",
    )
    expense_member_name = forms.CharField(
        max_length=200, required=False,
        label="Dépense effectuée par",
        widget=forms.TextInput(attrs={"readonly": "readonly", "class": "text-muted", "data-editable-when-ambiguous": "true"}),
        help_text="Nom extrait du signataire (modifiable si l'attribution est ambiguë)",
    )
    expense_apartment = forms.CharField(
        max_length=10, required=False,
        label="Appartement du signataire",
    )
    expense_member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        required=False,
        label="Membre ayant effectué la dépense",
        empty_label="-- Choisir un membre --",
    )
    validator_member_name = forms.CharField(
        max_length=200, required=False,
        label="Validé par (signataire)",
        widget=forms.TextInput(attrs={"readonly": "readonly", "class": "text-muted", "data-editable-when-ambiguous": "true"}),
        help_text="Nom extrait du 2e signataire (modifiable si l'attribution est ambiguë)",
    )
    validator_apartment = forms.CharField(
        max_length=10, required=False,
        label="Appartement du validateur",
    )
    validator_member = forms.ModelChoiceField(
        queryset=Member.objects.none(),
        required=False,
        label="Membre ayant validé",
        empty_label="-- Choisir un membre --",
    )
    signer_roles_ambiguous = forms.BooleanField(
        required=False,
        label="Attribution acheteur / validateur ambiguë",
        help_text="Cochez si les deux signatures doivent être validées manuellement.",
    )
    validator_is_external = forms.BooleanField(
        required=False,
        label="Fournisseur externe (non-membre)",
        help_text="Cochez si le validateur n'est pas un membre de la coopérative.",
    )

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
        required=False,
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
    untaxed_extra_amount = forms.DecimalField(
        max_digits=10, decimal_places=2, required=False,
        label="Frais non taxables",
        help_text="Pourboire, livraison ou autres frais non taxables inclus au total.",
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
        # help_text="Résumé généré par l'IA, modifiable par le trésorier.",
    )

    def clean(self):
        cleaned = super().clean()
        doc_type = cleaned.get("document_type", "receipt")

        if doc_type == "receipt":
            if not cleaned.get("purchaser_member"):
                self.add_error("purchaser_member", "Le membre est requis pour un reçu.")
        else:
            # paper_bc / invoice: member is optional
            if "purchaser_member" in self.errors:
                del self.errors["purchaser_member"]

        if doc_type == "paper_bc":
            if not cleaned.get("bc_number"):
                self.add_error("bc_number", "Le numéro de BC est requis pour un bon de commande papier.")
            if not cleaned.get("expense_member"):
                self.add_error(
                    "expense_member",
                    "Le membre ayant effectué la dépense est requis pour un bon de commande papier.",
                )
            expense_member = cleaned.get("expense_member")
            validator_member = cleaned.get("validator_member")
            if expense_member and validator_member and expense_member == validator_member:
                self.add_error(
                    "validator_member",
                    "Le validateur doit être différent de la personne qui a effectué la dépense.",
                )

        if doc_type == "invoice":
            if not cleaned.get("associated_bc_number"):
                self.add_error(
                    "associated_bc_number",
                    "Le numéro de BC associé est recommandé pour une facture.",
                )

        untaxed_extra_amount = cleaned.get("untaxed_extra_amount")
        if untaxed_extra_amount is not None and untaxed_extra_amount < 0:
            self.add_error("untaxed_extra_amount", "Les frais non taxables ne peuvent pas être négatifs.")

        return cleaned


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
    status = forms.ChoiceField(
        required=False,
        label="Statut",
        choices=[("", "Tous")] + list(BonStatus.choices),
    )

    def __init__(self, *args, user=None, **kwargs):
        from houses.models import House
        super().__init__(*args, **kwargs)
        self.fields["house"].queryset = House.objects.filter(is_active=True).order_by("code")
        self.fields["budget_year"].queryset = BudgetYear.objects.order_by("-year")
        self.fields["purchaser"].queryset = Member.objects.filter(
            is_active=True
        ).order_by("last_name", "first_name")

        # Default date range: Jan 1 of current year → today
        today = datetime.date.today()
        self.fields["date_from"].initial = datetime.date(today.year, 1, 1)
        self.fields["date_to"].initial = today

        # Pre-select user's house if available
        if user and hasattr(user, 'house') and user.house and not self.is_bound:
            self.fields["house"].initial = user.house.pk
