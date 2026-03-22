from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.shortcuts import get_object_or_404

from accounts.access import RoleRequiredMixin, TreasurerRequiredMixin, check_house_permission
from .models import BudgetYear, SubBudget, Expense
from .forms import BudgetYearForm, SubBudgetForm, SubBudgetFormSet, ExpenseForm
from .services import BudgetCalculationService

SEED_CATEGORIES = [
    # (trace_code, name, repeat_type, sort_order)
    (1, "Réparations par appartement", "annual", 1),
    (2, "Réparations BB", "annual", 2),
    (3, "Inspection alarme/extincteurs", "annual", 3),
    (4, "Inspection extincteurs", "annual", 4),
    (5, "Exterminateur", "annual", 5),
    (6, "Corvées", "annual", 6),
    (7, "Produits ménager/entretien", "annual", 7),
    (8, "Transport", "annual", 8),
    (9, "Photocopies", "annual", 9),
    (10, "Activités sociales", "annual", 10),
    (11, "Peinture", "annual", 11),
    (12, "Rouille", "unique", 12),
    (99, "Autre dépenses", "annual", 99),
]


def _filter_by_house(qs, user):
    """Les non-gestionnaires ne voient que les données de leur maison."""
    if not user.is_gestionnaire:
        return qs.filter(house=user.house)
    return qs


# ---------------------------------------------------------------------------
# BudgetYear views
# ---------------------------------------------------------------------------

class BudgetYearListView(ListView):
    model = BudgetYear
    template_name = "budget/year_list.html"
    context_object_name = "budget_years"

    def get_queryset(self):
        return super().get_queryset()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_manage"] = (
            self.request.user.is_authenticated
            and self.request.user.can_manage_financials
        )
        return ctx


class BudgetYearDetailView(DetailView):
    model = BudgetYear
    template_name = "budget/year_detail.html"
    context_object_name = "budget_year"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        by = self.object
        svc = BudgetCalculationService

        ctx["base"] = svc.base_values(by)
        ctx["repair_totals"] = svc.repair_totals(by)
        ctx["imprevues_totals"] = svc.imprevues_totals(by)
        ctx["available"] = svc.available_money(by)
        ctx["unbudgeted"] = svc.unbudgeted_available(by)
        ctx["categories"] = svc.category_summary(by)
        ctx["ledger_rows"] = svc.running_balances(by)
        ctx["can_manage"] = (
            self.request.user.is_authenticated
            and self.request.user.can_manage_financials
        )

        # Totals excluding contingency for sub-budget table footer
        non_contingency = [
            c for c in ctx["categories"] if not c["sub_budget"].is_contingency
        ]
        ctx["cat_total_planned"] = (
            sum((c["planned"] for c in non_contingency), Decimal("0"))
        )
        ctx["cat_total_used"] = (
            sum((c["used"] for c in non_contingency), Decimal("0"))
        )
        ctx["cat_total_remaining"] = (
            sum((c["remaining"] for c in non_contingency), Decimal("0"))
        )
        return ctx


class BudgetYearCreateView(TreasurerRequiredMixin, CreateView):
    model = BudgetYear
    form_class = BudgetYearForm
    template_name = "budget/year_form.html"

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_gestionnaire:
            form.fields["house"].initial = self.request.user.house
            form.fields["house"].widget = form.fields["house"].hidden_widget()
        return form

    def form_valid(self, form):
        if not self.request.user.is_gestionnaire:
            form.instance.house = self.request.user.house
        check_house_permission(self.request.user, form.instance.house)
        response = super().form_valid(form)

        house = self.object.house
        seed = form.cleaned_data.get("seed_default_categories")

        # Try to carry over sub-budgets from the most recent previous year
        previous_year = BudgetYear.objects.filter(
            house=house, year__lt=self.object.year
        ).order_by("-year").first()

        if previous_year:
            prev_subs = SubBudget.objects.filter(
                budget_year=previous_year, is_active=True, is_contingency=False
            ).order_by("sort_order", "trace_code")
            for sb in prev_subs:
                SubBudget.objects.get_or_create(
                    budget_year=self.object,
                    trace_code=sb.trace_code,
                    defaults={
                        "name": sb.name,
                        "repeat_type": sb.repeat_type,
                        "planned_amount": sb.planned_amount,
                        "sort_order": sb.sort_order,
                    },
                )
        elif seed:
            # No previous year — use seed defaults
            for code, name, repeat, order in SEED_CATEGORIES:
                SubBudget.objects.get_or_create(
                    budget_year=self.object,
                    trace_code=code,
                    defaults={
                        "name": name,
                        "repeat_type": repeat,
                        "sort_order": order,
                    },
                )
        return response

    def get_success_url(self):
        return reverse("budget:year-detail", kwargs={"pk": self.object.pk})


class BudgetYearUpdateView(TreasurerRequiredMixin, UpdateView):
    model = BudgetYear
    form_class = BudgetYearForm
    template_name = "budget/year_form.html"

    def get_queryset(self):
        qs = super().get_queryset()
        return _filter_by_house(qs, self.request.user)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if "seed_default_categories" in form.fields:
            del form.fields["seed_default_categories"]
        if not self.request.user.is_gestionnaire:
            form.fields["house"].widget = form.fields["house"].hidden_widget()
        return form

    def _subbudget_usage(self):
        """Return {subbudget_pk: {'expenses': N, 'bons': N}} for this budget year."""
        from django.db.models import Count
        subs = SubBudget.objects.filter(budget_year=self.object)
        expense_counts = dict(
            subs.annotate(c=Count("expenses")).values_list("pk", "c")
        )
        bon_counts = dict(
            subs.annotate(c=Count("bons_de_commande")).values_list("pk", "c")
        )
        return {
            pk: {
                "expenses": expense_counts.get(pk, 0),
                "bons": bon_counts.get(pk, 0),
            }
            for pk in subs.values_list("pk", flat=True)
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = SubBudget.objects.filter(
            budget_year=self.object
        ).order_by("sort_order", "trace_code")
        if self.request.POST:
            ctx["subbudget_formset"] = SubBudgetFormSet(
                self.request.POST, instance=self.object, queryset=qs,
            )
        else:
            ctx["subbudget_formset"] = SubBudgetFormSet(
                instance=self.object, queryset=qs,
            )
        usage = self._subbudget_usage()
        ctx["subbudget_usage"] = usage
        # json_script needs the raw dict — use string keys for JS compatibility
        ctx["subbudget_usage_json"] = {str(k): v for k, v in usage.items()}
        return ctx

    def form_valid(self, form):
        ctx = self.get_context_data()
        formset = ctx["subbudget_formset"]
        if not formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        self.object = form.save()
        formset.instance = self.object

        # Reassign expenses/bons from sub-budgets marked for deletion
        from bons.models import BonDeCommande
        for del_form in formset.deleted_forms:
            sb = del_form.instance
            if not sb.pk:
                continue
            has_expenses = sb.expenses.exists()
            has_bons = sb.bons_de_commande.exists()
            if has_expenses or has_bons:
                target_pk = self.request.POST.get(f"reassign_{sb.pk}")
                if not target_pk:
                    form.add_error(None,
                        f"Le sous-budget « {sb} » a des dépenses/bons liés. "
                        f"Choisissez un sous-budget de remplacement."
                    )
                    return self.render_to_response(self.get_context_data(form=form))
                try:
                    target = SubBudget.objects.get(
                        pk=target_pk, budget_year=self.object
                    )
                except SubBudget.DoesNotExist:
                    form.add_error(None, "Sous-budget de remplacement invalide.")
                    return self.render_to_response(self.get_context_data(form=form))
                sb.expenses.update(sub_budget=target)
                sb.bons_de_commande.update(sub_budget=target)

        formset.save()
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse("budget:year-detail", kwargs={"pk": self.object.pk})


# ---------------------------------------------------------------------------
# SubBudget views
# ---------------------------------------------------------------------------

class SubBudgetCreateView(TreasurerRequiredMixin, CreateView):
    model = SubBudget
    form_class = SubBudgetForm
    template_name = "budget/subbudget_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.budget_year = get_object_or_404(BudgetYear, pk=self.kwargs["budget_year_pk"])
        if request.user.is_authenticated:
            check_house_permission(request.user, self.budget_year.house)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.budget_year = self.budget_year
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["budget_year"] = self.budget_year
        return ctx

    def get_success_url(self):
        return reverse("budget:year-detail", kwargs={"pk": self.budget_year.pk})


class SubBudgetUpdateView(TreasurerRequiredMixin, UpdateView):
    model = SubBudget
    form_class = SubBudgetForm
    template_name = "budget/subbudget_form.html"

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if request.user.is_authenticated:
            check_house_permission(request.user, self.object.budget_year.house)
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["budget_year"] = self.object.budget_year
        return ctx

    def get_success_url(self):
        return reverse("budget:year-detail", kwargs={"pk": self.object.budget_year.pk})


# ---------------------------------------------------------------------------
# Expense views
# ---------------------------------------------------------------------------

class ExpenseLedgerView(ListView):
    model = Expense
    template_name = "budget/expense_ledger.html"
    context_object_name = "ledger_rows"

    def dispatch(self, request, *args, **kwargs):
        self.budget_year = get_object_or_404(BudgetYear, pk=self.kwargs["budget_year_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        # queryset not used directly; running_balances returns dicts
        return Expense.objects.none()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["budget_year"] = self.budget_year
        ctx["ledger_rows"] = BudgetCalculationService.running_balances(self.budget_year)
        ctx["can_manage"] = (
            self.request.user.is_authenticated
            and self.request.user.can_manage_financials
        )
        return ctx


class ExpenseCreateView(TreasurerRequiredMixin, CreateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "budget/expense_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.budget_year = get_object_or_404(BudgetYear, pk=self.kwargs["budget_year_pk"])
        if request.user.is_authenticated:
            check_house_permission(request.user, self.budget_year.house)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["budget_year"] = self.budget_year
        return kwargs

    def form_valid(self, form):
        form.instance.budget_year = self.budget_year
        form.instance.entered_by = self.request.user
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["budget_year"] = self.budget_year
        return ctx

    def get_success_url(self):
        return reverse("budget:expense-ledger", kwargs={"budget_year_pk": self.budget_year.pk})


class ExpenseUpdateView(TreasurerRequiredMixin, UpdateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "budget/expense_form.html"

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if request.user.is_authenticated:
            check_house_permission(request.user, self.object.budget_year.house)
        return response

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["budget_year"] = self.object.budget_year
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["budget_year"] = self.object.budget_year
        return ctx

    def get_success_url(self):
        return reverse("budget:expense-ledger", kwargs={"budget_year_pk": self.object.budget_year.pk})
