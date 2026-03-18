from django import forms
from django.utils import timezone

from .models import BudgetYear, SubBudget, Expense


class BudgetYearForm(forms.ModelForm):
    seed_default_categories = forms.BooleanField(
        required=False,
        initial=True,
        label="Créer les sous-budgets par défaut",
        help_text="Ajoute automatiquement les catégories standards (réparations, corvées, etc.)",
    )

    class Meta:
        model = BudgetYear
        fields = [
            "house", "year", "annual_budget_total", "snow_budget",
            "imprevues_rate", "notes",
        ]
        labels = {
            "house": "Immeuble",
            "year": "Année",
            "annual_budget_total": "Budget annuel total",
            "snow_budget": "Budget déneigement",
            "imprevues_rate": "Taux imprévues",
            "notes": "Notes",
        }
        widgets = {
            "year": forms.NumberInput(attrs={
                "placeholder": timezone.now().year,
            }),
            "annual_budget_total": forms.NumberInput(attrs={
                "step": "0.01", "placeholder": "ex : 50000.00",
            }),
            "snow_budget": forms.NumberInput(attrs={
                "step": "0.01", "placeholder": "ex : 5000.00",
            }),
            "imprevues_rate": forms.NumberInput(attrs={
                "step": "0.01", "placeholder": "ex : 0.05",
            }),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Remarques optionnelles"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["year"].initial = timezone.now().year
        # Auto-assign house for non-gestionnaire users
        if user and not user.is_gestionnaire and user.house:
            self.fields["house"].initial = user.house
            self.fields["house"].widget = self.fields["house"].hidden_widget()

    # Fields shown in the collapsible "Plus de détails" section
    DETAIL_FIELDS = ("imprevues_rate", "notes")


class SubBudgetForm(forms.ModelForm):
    class Meta:
        model = SubBudget
        fields = [
            "trace_code", "name", "repeat_type", "planned_amount",
            "sort_order", "notes",
        ]
        labels = {
            "trace_code": "Code de suivi",
            "name": "Nom",
            "repeat_type": "Type de récurrence",
            "planned_amount": "Montant prévu",
            "sort_order": "Ordre d'affichage",
            "notes": "Notes",
        }
        widgets = {
            "trace_code": forms.NumberInput(attrs={"placeholder": "ex : 1"}),
            "name": forms.TextInput(attrs={"placeholder": "ex : Réparations courantes"}),
            "planned_amount": forms.NumberInput(attrs={
                "step": "0.01", "placeholder": "ex : 5000.00",
            }),
            "sort_order": forms.NumberInput(attrs={"placeholder": "0"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Remarques optionnelles"}),
        }


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = [
            "entry_date", "description", "amount", "sub_budget",
            "bon_number", "supplier_name", "spent_by_label",
            "validated_gl", "source_type", "notes",
        ]
        labels = {
            "sub_budget": "Sous-budget",
            "entry_date": "Date",
            "description": "Description",
            "bon_number": "N° bon de commande",
            "supplier_name": "Fournisseur",
            "spent_by_label": "Dépensé par",
            "amount": "Montant",
            "validated_gl": "Validé au grand livre",
            "source_type": "Type de source",
            "notes": "Notes",
        }
        widgets = {
            "entry_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.TextInput(attrs={"placeholder": "ex : Achat de sel de déglaçage"}),
            "amount": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "bon_number": forms.TextInput(attrs={"placeholder": "Optionnel"}),
            "supplier_name": forms.TextInput(attrs={"placeholder": "Optionnel"}),
            "spent_by_label": forms.TextInput(attrs={"placeholder": "ex : 202 / Marylin"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Remarques optionnelles"}),
        }

    def __init__(self, *args, budget_year=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["entry_date"].initial = timezone.now().date()
        if budget_year:
            self.fields["sub_budget"].queryset = SubBudget.objects.filter(
                budget_year=budget_year, is_active=True
            ).order_by("sort_order", "trace_code")

    # Essential fields shown at top; the rest go in collapsible section
    ESSENTIAL_FIELDS = ("entry_date", "description", "amount", "sub_budget",
                        "bon_number", "supplier_name", "spent_by_label")
    DETAIL_FIELDS = ("validated_gl", "source_type", "notes")
