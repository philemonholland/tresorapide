"""Treasurer-facing member, apartment, and residency views."""

from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from accounts.access import TreasurerRequiredMixin
from members.forms import ApartmentForm, MemberForm, ResidencyForm
from members.models import Apartment, Member, Residency


class SuccessMessageFormMixin:
    """Attach a friendly success message after a create or update."""

    success_message = ""

    def form_valid(self, form):  # type: ignore[override]
        """Show a success toast after the object is saved."""
        response = super().form_valid(form)
        if self.success_message:
            messages.success(self.request, self.success_message)
        return response


class MembersDashboardView(TreasurerRequiredMixin, TemplateView):
    """Show quick treasurer access to membership, apartments, and residencies."""

    template_name = "members/index.html"

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Summarize core member management records."""
        context = super().get_context_data(**kwargs)
        today = date.today()
        context.update(
            {
                "member_count": Member.objects.count(),
                "active_member_count": Member.objects.filter(is_active=True).count(),
                "apartment_count": Apartment.objects.count(),
                "active_apartment_count": Apartment.objects.filter(is_active=True).count(),
                "current_residency_count": Residency.objects.current().count(),
                "latest_members": Member.objects.order_by("-created_at", "-id")[:8],
                "latest_apartments": Apartment.objects.order_by("code", "id")[:8],
                "current_residencies": Residency.objects.current()
                .select_related("member", "apartment")
                .order_by("apartment__code", "member__last_name", "member__first_name")[:12],
                "today": today,
            }
        )
        return context


class MemberListView(TreasurerRequiredMixin, ListView):
    """Browse and filter members for treasurer workflows."""

    model = Member
    template_name = "members/member_list.html"
    context_object_name = "members"

    def get_queryset(self):
        """Filter members by name and activity state."""
        queryset = Member.objects.annotate(
            residency_count=Count("residencies", distinct=True),
            current_residency_count=Count(
                "residencies",
                filter=Q(residencies__start_date__lte=date.today())
                & (
                    Q(residencies__end_date__isnull=True)
                    | Q(residencies__end_date__gte=date.today())
                ),
                distinct=True,
            ),
        ).order_by("last_name", "first_name", "id")
        query = self.request.GET.get("q", "").strip()
        activity = self.request.GET.get("activity", "").strip()
        if query:
            queryset = queryset.filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(preferred_name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone_number__icontains=query)
            )
        if activity == "active":
            queryset = queryset.filter(is_active=True)
        elif activity == "inactive":
            queryset = queryset.filter(is_active=False)
        return queryset

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Expose current filters and navigation helpers."""
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "query": self.request.GET.get("q", "").strip(),
                "activity": self.request.GET.get("activity", "").strip(),
            }
        )
        return context


class MemberCreateView(TreasurerRequiredMixin, SuccessMessageFormMixin, CreateView):
    """Create a member record without leaving the treasurer workspace."""

    model = Member
    form_class = MemberForm
    template_name = "members/member_form.html"
    success_url = reverse_lazy("members:member-list")
    success_message = "Member created."


class MemberUpdateView(TreasurerRequiredMixin, SuccessMessageFormMixin, UpdateView):
    """Update a member record."""

    model = Member
    form_class = MemberForm
    template_name = "members/member_form.html"
    success_url = reverse_lazy("members:member-list")
    success_message = "Member updated."


class ApartmentListView(TreasurerRequiredMixin, ListView):
    """Browse apartments and their occupancy footprint."""

    model = Apartment
    template_name = "members/apartment_list.html"
    context_object_name = "apartments"

    def get_queryset(self):
        """Filter apartments by code, address, and activity state."""
        queryset = Apartment.objects.annotate(
            residency_count=Count("residencies", distinct=True),
            current_resident_count=Count(
                "residencies",
                filter=Q(residencies__start_date__lte=date.today())
                & (
                    Q(residencies__end_date__isnull=True)
                    | Q(residencies__end_date__gte=date.today())
                ),
                distinct=True,
            ),
        ).order_by("code", "id")
        query = self.request.GET.get("q", "").strip()
        activity = self.request.GET.get("activity", "").strip()
        if query:
            queryset = queryset.filter(
                Q(code__icontains=query) | Q(street_address__icontains=query)
            )
        if activity == "active":
            queryset = queryset.filter(is_active=True)
        elif activity == "inactive":
            queryset = queryset.filter(is_active=False)
        return queryset

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Expose filter state to the template."""
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "query": self.request.GET.get("q", "").strip(),
                "activity": self.request.GET.get("activity", "").strip(),
            }
        )
        return context


class ApartmentCreateView(TreasurerRequiredMixin, SuccessMessageFormMixin, CreateView):
    """Create an apartment record."""

    model = Apartment
    form_class = ApartmentForm
    template_name = "members/apartment_form.html"
    success_url = reverse_lazy("members:apartment-list")
    success_message = "Apartment created."


class ApartmentUpdateView(TreasurerRequiredMixin, SuccessMessageFormMixin, UpdateView):
    """Update an apartment record."""

    model = Apartment
    form_class = ApartmentForm
    template_name = "members/apartment_form.html"
    success_url = reverse_lazy("members:apartment-list")
    success_message = "Apartment updated."


class ResidencyListView(TreasurerRequiredMixin, ListView):
    """Browse residency history with quick current-only filtering."""

    model = Residency
    template_name = "members/residency_list.html"
    context_object_name = "residencies"

    def get_queryset(self):
        """Filter residencies by member, apartment, and current state."""
        queryset = Residency.objects.select_related("member", "apartment").order_by(
            "-start_date",
            "-id",
        )
        query = self.request.GET.get("q", "").strip()
        scope = self.request.GET.get("scope", "").strip()
        if query:
            queryset = queryset.filter(
                Q(member__first_name__icontains=query)
                | Q(member__last_name__icontains=query)
                | Q(member__preferred_name__icontains=query)
                | Q(apartment__code__icontains=query)
                | Q(apartment__street_address__icontains=query)
            )
        if scope == "current":
            queryset = queryset.current()
        elif scope == "ended":
            queryset = queryset.filter(end_date__isnull=False, end_date__lt=date.today())
        return queryset

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Expose active filters."""
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "query": self.request.GET.get("q", "").strip(),
                "scope": self.request.GET.get("scope", "").strip(),
            }
        )
        return context


class ResidencyInitialMixin:
    """Seed residency form values from query parameters when present."""

    def get_initial(self) -> dict[str, object]:
        """Prefill member or apartment from the current query string."""
        initial = super().get_initial()
        member_id = self.request.GET.get("member")
        apartment_id = self.request.GET.get("apartment")
        if member_id and member_id.isdigit():
            initial["member"] = int(member_id)
        if apartment_id and apartment_id.isdigit():
            initial["apartment"] = int(apartment_id)
        return initial


class ResidencyCreateView(
    TreasurerRequiredMixin,
    ResidencyInitialMixin,
    SuccessMessageFormMixin,
    CreateView,
):
    """Create a residency record."""

    model = Residency
    form_class = ResidencyForm
    template_name = "members/residency_form.html"
    success_url = reverse_lazy("members:residency-list")
    success_message = "Residency created."


class ResidencyUpdateView(
    TreasurerRequiredMixin,
    ResidencyInitialMixin,
    SuccessMessageFormMixin,
    UpdateView,
):
    """Update a residency record."""

    model = Residency
    form_class = ResidencyForm
    template_name = "members/residency_form.html"
    success_url = reverse_lazy("members:residency-list")
    success_message = "Residency updated."
