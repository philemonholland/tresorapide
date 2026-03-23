from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Sum, Q
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.shortcuts import get_object_or_404, redirect

from accounts.access import RoleRequiredMixin, TreasurerRequiredMixin, check_house_permission
from .models import BudgetYear, SubBudget, Expense, GrandLivreUpload, GrandLivreEntry, ReconciliationResult
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


def _can_manage_financial_house(user, house) -> bool:
    """Whether the user may modify financial data for the given house."""
    return bool(
        user.is_authenticated
        and user.can_manage_financials
        and (user.is_gestionnaire or user.house_id == house.id)
    )


def _show_cancelled_expenses(request) -> bool:
    """Return whether cancelled expenses should remain visible in ledgers."""
    return str(request.GET.get("show_cancelled", "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _attach_receipt_metadata(ledger_rows):
    """Annotate ledger rows with active receipt counts for linked bons."""
    from django.db.models import Count
    from bons.models import ReceiptFile

    bon_ids = {
        row["expense"].bon_de_commande_id
        for row in ledger_rows
        if row["expense"].bon_de_commande_id
    }
    if not bon_ids:
        for row in ledger_rows:
            row["receipt_count"] = 0
        return

    receipt_counts = dict(
        ReceiptFile.objects.filter(
            bon_de_commande_id__in=bon_ids,
            archived_at__isnull=True,
        ).values("bon_de_commande_id").annotate(
            total=Count("pk"),
        ).values_list("bon_de_commande_id", "total")
    )
    for row in ledger_rows:
        row["receipt_count"] = receipt_counts.get(row["expense"].bon_de_commande_id, 0)


# ---------------------------------------------------------------------------
# BudgetYear views
# ---------------------------------------------------------------------------

class BudgetYearListView(RoleRequiredMixin, ListView):
    model = BudgetYear
    template_name = "budget/year_list.html"
    context_object_name = "budget_years"

    def get_queryset(self):
        from datetime import date
        from houses.models import House

        qs = super().get_queryset().select_related("house")

        # Default house: user's house (admin/gestionnaire = all houses)
        requested_house = self.request.GET.get("house")
        user = self.request.user
        if requested_house is None:
            if user.is_gestionnaire or user.is_app_admin:
                selected_house = ""
            elif user.house_id:
                selected_house = str(user.house_id)
            else:
                selected_house = ""
        else:
            selected_house = (requested_house or "").strip()

        # Default year: current year
        requested_year = self.request.GET.get("year")
        if requested_year is None:
            selected_year = str(date.today().year)
        else:
            selected_year = (requested_year or "").strip()

        if selected_house.isdigit():
            qs = qs.filter(house_id=int(selected_house))
        else:
            selected_house = ""

        if selected_year.isdigit():
            qs = qs.filter(year=int(selected_year))
        else:
            selected_year = ""

        self.selected_house = selected_house
        self.selected_year = selected_year
        self.house_choices = House.objects.order_by("code")
        self.year_choices = (
            BudgetYear.objects.order_by("-year")
            .values_list("year", flat=True)
            .distinct()
        )

        return qs.annotate(
            expenses_to_date=Sum(
                "expenses__amount",
                filter=Q(expenses__is_cancellation=False),
                default=Decimal("0"),
            ),
        ).order_by("-year", "house__code", "id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_create_budget_year"] = (
            self.request.user.is_authenticated
            and self.request.user.can_manage_financials
        )
        if self.request.user.is_authenticated and self.request.user.can_manage_financials:
            if self.request.user.is_gestionnaire:
                manageable_house_ids = list(self.house_choices.values_list("id", flat=True))
            elif self.request.user.house_id:
                manageable_house_ids = [self.request.user.house_id]
            else:
                manageable_house_ids = []
        else:
            manageable_house_ids = []

        ctx["manageable_house_ids"] = manageable_house_ids
        ctx["house_choices"] = self.house_choices
        ctx["year_choices"] = self.year_choices
        ctx["selected_house"] = getattr(self, "selected_house", "")
        ctx["selected_year"] = getattr(self, "selected_year", "")
        return ctx


class BudgetYearDetailView(RoleRequiredMixin, DetailView):
    model = BudgetYear
    template_name = "budget/year_detail.html"
    context_object_name = "budget_year"

    def get_queryset(self):
        return super().get_queryset().select_related("house")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        by = self.object
        svc = BudgetCalculationService
        show_cancelled = _show_cancelled_expenses(self.request)

        ctx["base"] = svc.base_values(by)
        ctx["repair_totals"] = svc.repair_totals(by)
        ctx["imprevues_totals"] = svc.imprevues_totals(by)
        ctx["available"] = svc.available_money(by)
        ctx["unbudgeted"] = svc.unbudgeted_available(by)
        ctx["categories"] = svc.category_summary(by)
        ctx["ledger_rows"] = svc.running_balances(
            by,
            include_cancelled=show_cancelled,
        )
        _attach_receipt_metadata(ctx["ledger_rows"])
        ctx["show_cancelled"] = show_cancelled
        ctx["can_manage"] = _can_manage_financial_house(self.request.user, by.house)

        # Annotate ledger rows with duplicate flag info
        from bons.models import DuplicateFlag
        bon_ids_with_dup = set()
        for row in ctx["ledger_rows"]:
            exp = row["expense"]
            if exp.bon_de_commande_id:
                bon_ids_with_dup.add(exp.bon_de_commande_id)

        if bon_ids_with_dup:
            from bons.models import ReceiptFile
            flagged_bon_ids = set(
                ReceiptFile.objects.filter(
                    bon_de_commande_id__in=bon_ids_with_dup,
                    archived_at__isnull=True,
                    duplicate_flags__status__in=["PENDING", "CONFIRMED_DUPLICATE"],
                    duplicate_flags__suspected_duplicate_receipt__bon_de_commande__is_scan_session=False,
                    duplicate_flags__suspected_duplicate_receipt__archived_at__isnull=True,
                ).exclude(
                    duplicate_flags__suspected_duplicate_receipt__bon_de_commande__status="VOID",
                ).values_list("bon_de_commande_id", flat=True).distinct()
            )
        else:
            flagged_bon_ids = set()

        for row in ctx["ledger_rows"]:
            row["has_duplicate_flag"] = (
                row["expense"].bon_de_commande_id in flagged_bon_ids
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

    def _get_house(self):
        """Resolve the house from the user or from POST data."""
        user = self.request.user
        if not user.is_gestionnaire:
            return user.house
        # For gestionnaires, try to get from POST or GET
        house_pk = self.request.POST.get("house") or self.request.GET.get("house")
        if house_pk:
            from houses.models import House
            try:
                return House.objects.get(pk=house_pk)
            except House.DoesNotExist:
                pass
        return None

    def _get_previous_sub_budgets(self, house):
        """Return list of dicts with previous year's sub-budget data, or seed defaults."""
        if house:
            previous_year = BudgetYear.objects.filter(
                house=house
            ).order_by("-year").first()
            if previous_year:
                prev_subs = SubBudget.objects.filter(
                    budget_year=previous_year, is_active=True, is_contingency=False
                ).order_by("sort_order", "trace_code")
                return [
                    {
                        "trace_code": sb.trace_code,
                        "name": sb.name,
                        "repeat_type": sb.repeat_type,
                        "planned_amount": sb.planned_amount,
                        "sort_order": sb.sort_order,
                        "is_active": True,
                        "notes": sb.notes or "",
                    }
                    for sb in prev_subs
                ]
        # No previous year — use seed defaults
        return [
            {
                "trace_code": code,
                "name": name,
                "repeat_type": repeat,
                "planned_amount": Decimal("0.00"),
                "sort_order": order,
                "is_active": True,
                "notes": "",
            }
            for code, name, repeat, order in SEED_CATEGORIES
        ]

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_gestionnaire:
            form.fields["house"].initial = self.request.user.house
            form.fields["house"].widget = form.fields["house"].hidden_widget()
        # Remove seed checkbox — replaced by visible editable formset
        if "seed_default_categories" in form.fields:
            del form.fields["seed_default_categories"]
        return form

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        house = self._get_house()
        prev_subs = self._get_previous_sub_budgets(house)

        if self.request.POST:
            ctx["subbudget_formset"] = SubBudgetFormSet(
                self.request.POST,
                instance=BudgetYear(),  # unsaved placeholder
            )
        else:
            initial_data = prev_subs
            # Create formset with initial data (no instance yet)
            formset = SubBudgetFormSet(
                instance=BudgetYear(),  # unsaved placeholder
                initial=initial_data,
            )
            # Override TOTAL_FORMS to show all initial rows + 2 extras
            formset.extra = len(initial_data) + 2
            ctx["subbudget_formset"] = formset

        ctx["subbudget_usage"] = {}
        ctx["subbudget_usage_json"] = {}
        ctx["is_create"] = True
        ctx["prev_sub_count"] = len(prev_subs)
        return ctx

    def form_valid(self, form):
        if not self.request.user.is_gestionnaire:
            form.instance.house = self.request.user.house
        check_house_permission(self.request.user, form.instance.house)

        # Save the BudgetYear first (triggers contingency sub-budget creation)
        response = super().form_valid(form)

        # Now process the sub-budget formset
        formset = SubBudgetFormSet(
            self.request.POST,
            instance=self.object,
        )
        if formset.is_valid():
            formset.save()
        else:
            # If formset is invalid, the sub-budgets from the form are lost.
            # Fall back to previous year copy as before.
            house = self.object.house
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
            else:
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

class ExpenseLedgerView(RoleRequiredMixin, ListView):
    model = Expense
    template_name = "budget/expense_ledger.html"
    context_object_name = "ledger_rows"

    def dispatch(self, request, *args, **kwargs):
        self.budget_year = get_object_or_404(
            BudgetYear.objects.select_related("house"),
            pk=self.kwargs["budget_year_pk"],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        # queryset not used directly; running_balances returns dicts
        return Expense.objects.none()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        show_cancelled = _show_cancelled_expenses(self.request)
        ctx["budget_year"] = self.budget_year
        ctx["base"] = BudgetCalculationService.base_values(self.budget_year)
        ctx["ledger_rows"] = BudgetCalculationService.running_balances(
            self.budget_year,
            include_cancelled=show_cancelled,
        )
        _attach_receipt_metadata(ctx["ledger_rows"])
        ctx["show_cancelled"] = show_cancelled
        ctx["can_manage"] = _can_manage_financial_house(
            self.request.user,
            self.budget_year.house,
        )
        return ctx


class ExpenseReceiptsView(RoleRequiredMixin, DetailView):
    model = Expense
    template_name = "budget/expense_receipts.html"
    context_object_name = "expense"

    def get_queryset(self):
        return Expense.objects.select_related(
            "budget_year",
            "budget_year__house",
            "sub_budget",
            "bon_de_commande",
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        expense = self.object
        bon = expense.bon_de_commande
        receipts = []
        if bon:
            receipts = list(
                bon.active_receipt_files.select_related("uploaded_by").order_by("created_at", "pk")
            )
        ctx["budget_year"] = expense.budget_year
        ctx["bon"] = bon
        ctx["receipts"] = receipts
        ctx["receipt_count"] = len(receipts)
        ctx["can_manage"] = _can_manage_financial_house(
            self.request.user,
            expense.budget_year.house,
        )
        return ctx


def _check_budget_year_active(budget_year):
    """Return an HttpResponseForbidden if the budget year is inactive, else None."""
    if budget_year.is_inactive:
        return HttpResponseForbidden(
            "Budget fermé — modifications non permises pour l'année "
            f"{budget_year.year}."
        )
    return None


class ExpenseCreateView(TreasurerRequiredMixin, CreateView):
    model = Expense
    form_class = ExpenseForm
    template_name = "budget/expense_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.budget_year = get_object_or_404(BudgetYear, pk=self.kwargs["budget_year_pk"])
        if request.user.is_authenticated:
            check_house_permission(request.user, self.budget_year.house)
        blocked = _check_budget_year_active(self.budget_year)
        if blocked:
            return blocked
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
        blocked = _check_budget_year_active(self.object.budget_year)
        if blocked:
            return blocked
        return response

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["budget_year"] = self.object.budget_year
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["budget_year"] = self.object.budget_year
        ctx["can_manage"] = (
            self.request.user.is_authenticated
            and self.request.user.can_manage_financials
        )
        return ctx

    def get_success_url(self):
        return reverse("budget:expense-ledger", kwargs={"budget_year_pk": self.object.budget_year.pk})


class ExpenseCancelView(TreasurerRequiredMixin, View):
    """Cancel an expense by creating a reversal entry with negative amount."""

    def post(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        check_house_permission(request.user, expense.budget_year.house)

        blocked = _check_budget_year_active(expense.budget_year)
        if blocked:
            return blocked

        # Cannot cancel a cancellation entry itself
        if expense.is_cancellation:
            messages.error(request, "Impossible d'annuler une entrée d'annulation.")
            return redirect(
                reverse("budget:expense-ledger", kwargs={"budget_year_pk": expense.budget_year.pk})
            )

        # Cannot cancel if already cancelled
        if expense.is_cancelled:
            messages.warning(request, "Cette dépense a déjà été annulée.")
            return redirect(
                reverse("budget:expense-ledger", kwargs={"budget_year_pk": expense.budget_year.pk})
            )

        # Create the reversal entry
        Expense.objects.create(
            budget_year=expense.budget_year,
            sub_budget=expense.sub_budget,
            bon_de_commande=expense.bon_de_commande,
            entry_date=timezone.now().date(),
            description=f"[ANNULATION] {expense.description}",
            bon_number=expense.bon_number,
            supplier_name=expense.supplier_name,
            spent_by_label=expense.spent_by_label,
            amount=-expense.amount,
            source_type=expense.source_type,
            entered_by=request.user,
            is_cancellation=True,
            reversal_of=expense,
            notes=f"Annulation de la dépense #{expense.pk} par {request.user.get_full_name() or request.user.username}",
        )

        messages.success(
            request,
            f"La dépense « {expense.description} » ({expense.amount:.2f} $) a été annulée."
        )
        return redirect(
            reverse("budget:expense-ledger", kwargs={"budget_year_pk": expense.budget_year.pk})
        )


class ExpenseReactivateView(TreasurerRequiredMixin, View):
    """Reactivate a cancelled expense by removing its reversal entry."""

    def post(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        check_house_permission(request.user, expense.budget_year.house)

        blocked = _check_budget_year_active(expense.budget_year)
        if blocked:
            return blocked

        if expense.is_cancellation:
            messages.error(request, "Impossible de réactiver directement une entrée d'annulation.")
            return redirect(reverse("budget:expense-edit", kwargs={"pk": expense.reversal_of_id or expense.pk}))

        reversals = expense.reversals.all()
        if not reversals.exists():
            messages.warning(request, "Cette dépense n'est pas annulée.")
            return redirect(reverse("budget:expense-edit", kwargs={"pk": expense.pk}))

        reversal_amount = sum((reversal.amount for reversal in reversals), Decimal("0.00"))
        reversals.delete()

        messages.success(
            request,
            (
                f"La dépense « {expense.description} » a été réactivée. "
                f"L'annulation de {abs(reversal_amount):.2f} $ a été retirée."
            ),
        )
        return redirect(reverse("budget:expense-edit", kwargs={"pk": expense.pk}))


# ---------------------------------------------------------------------------
# Grand Livre views
# ---------------------------------------------------------------------------

class GrandLivreListView(TreasurerRequiredMixin, ListView):
    """List all GL uploads for the user's houses."""
    model = GrandLivreUpload
    template_name = "budget/gl_list.html"
    context_object_name = "uploads"

    def get_queryset(self):
        from .models import GrandLivreUpload
        qs = GrandLivreUpload.objects.select_related(
            "budget_year", "budget_year__house", "uploaded_by",
        )
        user = self.request.user
        if not user.is_gestionnaire:
            qs = qs.filter(budget_year__house=user.house)
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        if user.is_gestionnaire:
            budget_years = BudgetYear.objects.select_related("house").order_by("-year")
        else:
            budget_years = BudgetYear.objects.filter(
                house=user.house,
            ).select_related("house").order_by("-year")
        ctx["budget_years"] = budget_years
        # Pre-select current year budget for user's house
        from datetime import date
        default_by = budget_years.filter(year=date.today().year)
        if user.house_id and not user.is_gestionnaire:
            default_by = default_by.filter(house=user.house)
        ctx["default_budget_year_pk"] = (
            default_by.values_list("pk", flat=True).first() or ""
        )
        return ctx


class GrandLivreUploadView(TreasurerRequiredMixin, View):
    """Handle GL file upload and trigger reconciliation."""

    def post(self, request):
        from .models import GrandLivreUpload
        from .gl_reconciliation import GrandLivreReconciliationService

        budget_year_pk = request.POST.get("budget_year")
        file = request.FILES.get("file")

        if not budget_year_pk or not file:
            messages.error(request, "Veuillez sélectionner une année budgétaire et un fichier.")
            return redirect(reverse("budget:grand-livre-list"))

        budget_year = get_object_or_404(BudgetYear, pk=budget_year_pk)
        check_house_permission(request.user, budget_year.house)

        upload = GrandLivreUpload.objects.create(
            budget_year=budget_year,
            uploaded_file=file,
            uploaded_by=request.user,
        )

        try:
            GrandLivreReconciliationService.full_reconciliation(upload)
            messages.success(request, "Grand Livre analysé avec succès.")
        except Exception as e:
            upload.status = "error"
            upload.error_message = str(e)
            upload.save()
            messages.error(request, f"Erreur lors de l'analyse : {e}")

        return redirect(reverse("budget:grand-livre-detail", kwargs={"pk": upload.pk}))


class GrandLivreDetailView(TreasurerRequiredMixin, DetailView):
    """Reconciliation dashboard for a GL upload."""
    model = GrandLivreUpload
    template_name = "budget/gl_detail.html"
    context_object_name = "upload"

    def get_queryset(self):
        return GrandLivreUpload.objects.select_related(
            "budget_year", "budget_year__house", "uploaded_by",
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        upload = self.object

        # Fetch entries with related expense data
        entries = list(
            upload.entries.select_related("matched_expense", "matched_expense__sub_budget")
            .order_by("row_number")
        )

        matched = [e for e in entries if e.matched_expense_id]
        unmatched = [e for e in entries if not e.matched_expense_id]

        # Find expenses missing from GL
        matched_ids = {e.matched_expense_id for e in matched}
        all_expenses = Expense.objects.filter(
            budget_year=upload.budget_year,
        ).exclude(is_cancellation=True).select_related("sub_budget")
        missing = [e for e in all_expenses if e.id not in matched_ids]

        ctx["entries"] = entries
        ctx["matched_entries"] = matched
        ctx["unmatched_entries"] = unmatched
        ctx["missing_expenses"] = missing
        ctx["base"] = BudgetCalculationService.base_values(upload.budget_year)

        try:
            ctx["reconciliation"] = upload.reconciliation
        except ReconciliationResult.DoesNotExist:
            ctx["reconciliation"] = None

        ctx["can_manage"] = _can_manage_financial_house(
            self.request.user, upload.budget_year.house,
        )
        return ctx


class GrandLivreValidateView(TreasurerRequiredMixin, View):
    """Validate and import selected GL entries into the grille."""

    def post(self, request, pk):
        from .gl_reconciliation import GrandLivreReconciliationService

        upload = get_object_or_404(GrandLivreUpload, pk=pk)
        check_house_permission(request.user, upload.budget_year.house)

        entry_ids = request.POST.getlist("entry_ids")
        if not entry_ids:
            messages.warning(request, "Aucune entrée sélectionnée.")
            return redirect(reverse("budget:grand-livre-detail", kwargs={"pk": pk}))

        # Mark entries as validated
        upload.entries.filter(id__in=entry_ids).update(is_validated=True)

        # Import validated entries
        created, skipped = GrandLivreReconciliationService.import_validated_entries(
            upload, [int(x) for x in entry_ids],
        )

        # Re-run reconciliation to update counts
        from .gl_reconciliation import GrandLivreReconciliationService as Svc
        Svc.build_reconciliation(upload)

        msg_parts = []
        if created:
            msg_parts.append(f"{len(created)} dépense(s) importée(s)")
        if skipped:
            msg_parts.append(f"{skipped} doublon(s) évité(s) (liés aux dépenses existantes)")
        if msg_parts:
            messages.success(request, " · ".join(msg_parts) + ".")
        else:
            messages.info(request, "Aucune nouvelle dépense à importer.")

        return redirect(reverse("budget:grand-livre-detail", kwargs={"pk": pk}))


class GrandLivreEntryEditView(TreasurerRequiredMixin, View):
    """AJAX endpoint to update a GL entry's clean description, apartment, BC."""

    def post(self, request, pk, entry_pk):
        upload = get_object_or_404(GrandLivreUpload, pk=pk)
        check_house_permission(request.user, upload.budget_year.house)
        entry = get_object_or_404(GrandLivreEntry, pk=entry_pk, upload=upload)

        entry.description_clean = request.POST.get("description_clean", entry.description_clean)
        entry.extracted_apartment = request.POST.get("apartment", entry.extracted_apartment)
        entry.extracted_bc_number = request.POST.get("bc_number", entry.extracted_bc_number)

        # Allow overriding the sub-budget trace code for import
        trace = request.POST.get("trace_code")
        if trace is not None:
            entry.match_notes = f"trace_override={trace}"

        entry.save()
        messages.success(request, "Entrée mise à jour.")
        return redirect(reverse("budget:grand-livre-detail", kwargs={"pk": pk}))


# ──────────────────────────────────────────────────────────────────
#  Grille de dépenses — export PDF / XLSX
# ──────────────────────────────────────────────────────────────────

class ExpenseLedgerExportPDFView(RoleRequiredMixin, View):
    """Download the expense ledger as a landscape PDF."""

    def get(self, request, budget_year_pk):
        by = get_object_or_404(BudgetYear.objects.select_related("house"), pk=budget_year_pk)
        include_cancelled = request.GET.get("show_cancelled") == "1"

        from .export_service import generate_expense_ledger_pdf
        pdf_bytes = generate_expense_ledger_pdf(by, include_cancelled=include_cancelled)

        filename = f"Grille_depenses_{by.house.code}_{by.year}.pdf"
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp


class ExpenseLedgerExportXLSXView(RoleRequiredMixin, View):
    """Download the expense ledger as an Excel workbook."""

    def get(self, request, budget_year_pk):
        by = get_object_or_404(BudgetYear.objects.select_related("house"), pk=budget_year_pk)
        include_cancelled = request.GET.get("show_cancelled") == "1"

        from .export_service import generate_expense_ledger_xlsx
        xlsx_bytes = generate_expense_ledger_xlsx(by, include_cancelled=include_cancelled)

        filename = f"Grille_depenses_{by.house.code}_{by.year}.xlsx"
        resp = HttpResponse(
            xlsx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp
