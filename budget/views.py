"""Budget transparency and treasurer management views."""
from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.access import RoleRequiredMixin, TreasurerRequiredMixin
from budget.forms import BudgetCategoryForm, BudgetYearForm
from budget.models import BudgetCategory, BudgetYear
from reimbursements.models import ReimbursementStatus
from reimbursements.queries import (
    can_user_review_internal_reimbursements,
    transparency_visibility_note,
    viewer_visible_reimbursement_q,
    visible_reimbursements_for_user,
)

MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)
ZERO_MONEY = Value(Decimal("0.00"), output_field=MONEY_FIELD)


class SuccessMessageFormMixin:
    """Attach a success message for budget management forms."""

    success_message = ""

    def form_valid(self, form):  # type: ignore[override]
        """Show a friendly message after saving."""
        response = super().form_valid(form)
        if self.success_message:
            messages.success(self.request, self.success_message)
        return response


class BudgetYearListView(RoleRequiredMixin, ListView):
    """List budget years with planned-versus-used summaries."""

    model = BudgetYear
    template_name = "budget/year_list.html"
    context_object_name = "budget_years"

    def get_queryset(self):
        """Annotate year-level budget planning totals."""

        return (
            BudgetYear.objects.annotate(
                category_count=Count("categories", distinct=True),
                planned_total=Coalesce(
                    Sum("categories__planned_amount"),
                    ZERO_MONEY,
                    output_field=MONEY_FIELD,
                ),
            )
            .annotate(
                remaining_total=ExpressionWrapper(
                    F("planned_total") - F("approved_reimbursement_total"),
                    output_field=MONEY_FIELD,
                )
            )
            .order_by("-start_date", "-id")
        )

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Add a short visibility note for viewer roles."""

        context = super().get_context_data(**kwargs)
        context["transparency_note"] = transparency_visibility_note(self.request.user)
        context["can_manage"] = can_user_review_internal_reimbursements(self.request.user)
        return context


class BudgetYearDetailView(RoleRequiredMixin, DetailView):
    """Show category-level planned-versus-used reporting for a year."""

    model = BudgetYear
    template_name = "budget/year_detail.html"
    context_object_name = "budget_year"

    def get_queryset(self):
        """Annotate the selected year with roll-up planning totals."""

        return BudgetYear.objects.annotate(
            category_count=Count("categories", distinct=True),
            planned_total=Coalesce(
                Sum("categories__planned_amount"),
                ZERO_MONEY,
                output_field=MONEY_FIELD,
            ),
        ).annotate(
            remaining_total=ExpressionWrapper(
                F("planned_total") - F("approved_reimbursement_total"),
                output_field=MONEY_FIELD,
            ),
        )

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Add category summaries and recent visible reimbursements."""

        context = super().get_context_data(**kwargs)
        visible_filter = (
            Q()
            if can_user_review_internal_reimbursements(self.request.user)
            else viewer_visible_reimbursement_q("reimbursements__")
        )
        context["categories"] = self.object.categories.annotate(
            visible_reimbursement_count=Count(
                "reimbursements",
                filter=visible_filter,
                distinct=True,
            ),
            archived_reimbursement_count=Count(
                "reimbursements",
                filter=visible_filter & Q(reimbursements__archived_at__isnull=False),
                distinct=True,
            ),
            void_reimbursement_count=Count(
                "reimbursements",
                filter=visible_filter & Q(reimbursements__status=ReimbursementStatus.VOID),
                distinct=True,
            ),
            remaining_amount=ExpressionWrapper(
                F("planned_amount") - F("approved_reimbursement_total"),
                output_field=MONEY_FIELD,
            ),
        ).order_by("sort_order", "code", "id")
        context["recent_reimbursements"] = visible_reimbursements_for_user(
            self.request.user
        ).filter(budget_year=self.object)[:10]
        context["transparency_note"] = transparency_visibility_note(self.request.user)
        context["can_manage"] = can_user_review_internal_reimbursements(self.request.user)
        return context


class BudgetCategoryDetailView(RoleRequiredMixin, DetailView):
    """Show read-only category transparency details."""

    model = BudgetCategory
    template_name = "budget/category_detail.html"
    context_object_name = "category"

    def get_queryset(self):
        """Annotate the category with remaining-planned information."""

        return BudgetCategory.objects.select_related("budget_year").annotate(
            remaining_amount=ExpressionWrapper(
                F("planned_amount") - F("approved_reimbursement_total"),
                output_field=MONEY_FIELD,
            )
        )

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Add visible reimbursements and transparency summaries."""

        context = super().get_context_data(**kwargs)
        reimbursements = visible_reimbursements_for_user(self.request.user).filter(
            budget_category=self.object
        )
        context["reimbursements"] = reimbursements
        context["reimbursement_summary"] = reimbursements.aggregate(
            visible_count=Count("id"),
            archived_count=Count("id", filter=Q(archived_at__isnull=False)),
            void_count=Count("id", filter=Q(status=ReimbursementStatus.VOID)),
        )
        context["transparency_note"] = transparency_visibility_note(self.request.user)
        context["can_manage"] = can_user_review_internal_reimbursements(self.request.user)
        return context


class BudgetYearCreateView(TreasurerRequiredMixin, SuccessMessageFormMixin, CreateView):
    """Create a budget year from the treasurer interface."""

    model = BudgetYear
    form_class = BudgetYearForm
    template_name = "budget/year_form.html"
    success_url = reverse_lazy("budget:list")
    success_message = "Budget year created."


class BudgetYearUpdateView(TreasurerRequiredMixin, SuccessMessageFormMixin, UpdateView):
    """Update budget year metadata."""

    model = BudgetYear
    form_class = BudgetYearForm
    template_name = "budget/year_form.html"
    success_message = "Budget year updated."

    def get_success_url(self) -> str:
        """Return to the year detail after saving."""
        return reverse("budget:year-detail", args=[self.object.pk])


class BudgetCategoryInitialMixin:
    """Prefill the budget year when creating categories from a year detail page."""

    def get_initial(self) -> dict[str, object]:
        """Seed the budget year from the routed year id when available."""
        initial = super().get_initial()
        year_pk = self.kwargs.get("year_pk")
        if year_pk is not None:
            initial["budget_year"] = year_pk
        return initial


class BudgetCategoryCreateView(
    TreasurerRequiredMixin,
    BudgetCategoryInitialMixin,
    SuccessMessageFormMixin,
    CreateView,
):
    """Create a budget category for a year."""

    model = BudgetCategory
    form_class = BudgetCategoryForm
    template_name = "budget/category_form.html"
    success_message = "Budget category created."

    def get_success_url(self) -> str:
        """Return to the parent year after creation."""
        return reverse("budget:year-detail", args=[self.object.budget_year_id])


class BudgetCategoryUpdateView(
    TreasurerRequiredMixin,
    SuccessMessageFormMixin,
    UpdateView,
):
    """Update a budget category."""

    model = BudgetCategory
    form_class = BudgetCategoryForm
    template_name = "budget/category_form.html"
    success_message = "Budget category updated."

    def get_success_url(self) -> str:
        """Return to the category detail after saving."""
        return reverse("budget:category-detail", args=[self.object.pk])
