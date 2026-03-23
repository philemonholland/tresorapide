"""Member, apartment, and residency views."""
from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.access import TreasurerRequiredMixin, AdminRequiredMixin, ViewerRequiredMixin

from .forms import ApartmentForm, MemberForm, ResidencyForm
from .models import Apartment, Member, Residency


def _filter_by_house(qs, user, field="house"):
    """Les non-gestionnaires ne voient que les donnees de leur maison."""
    if user.is_authenticated and not user.is_gestionnaire and user.house:
        return qs.filter(**{field: user.house})
    return qs


def _restrict_members_by_house(qs, user):
    """Admins/gestionnaires/superusers see all; others see own house only."""
    if user.is_app_admin:
        return qs
    if user.house:
        return qs.filter(
            residencies__apartment__house=user.house,
            residencies__end_date__isnull=True,
        ).distinct()
    return qs.none()


# -- Members -----------------------------------------------------------------

class MemberListView(ViewerRequiredMixin, ListView):
    """Tous les membres. Accessible aux utilisateurs connectés."""
    model = Member
    template_name = "members/member_list.html"
    context_object_name = "members"

    def get_queryset(self):
        qs = Member.objects.prefetch_related(
            Prefetch(
                "residencies",
                queryset=Residency.objects.filter(
                    end_date__isnull=True,
                ).select_related("apartment__house"),
                to_attr="current_residencies",
            ),
        )

        # House-based security: non-admins see only their own house
        qs = _restrict_members_by_house(qs, self.request.user)

        # Activity filter
        show = self.request.GET.get("show", "active")
        if show == "active":
            qs = qs.filter(is_active=True)
        elif show == "inactive":
            qs = qs.filter(is_active=False)

        # Coop member filter
        coop = self.request.GET.get("coop", "")
        if coop == "yes":
            qs = qs.filter(residencies__end_date__isnull=True, residencies__is_coop_member=True).distinct()
        elif coop == "no":
            qs = qs.filter(residencies__end_date__isnull=True, residencies__is_coop_member=False).distinct()

        # House filter (admins can filter any house; non-admins already restricted)
        house_id = self.request.GET.get("house", "")
        if house_id:
            qs = qs.filter(residencies__apartment__house_id=house_id, residencies__end_date__isnull=True).distinct()

        # Sorting
        sort = self.request.GET.get("sort", "last_name")
        if sort in ("last_name", "first_name", "-last_name", "-first_name"):
            qs = qs.order_by(sort)
        else:
            qs = qs.order_by("last_name", "first_name")

        return qs

    def get_context_data(self, **kwargs):
        from houses.models import House
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx["show"] = self.request.GET.get("show", "active")
        ctx["coop_filter"] = self.request.GET.get("coop", "")
        ctx["house_filter"] = self.request.GET.get("house", "")
        ctx["sort_field"] = self.request.GET.get("sort", "last_name")
        ctx["can_see_all_houses"] = user.is_app_admin
        if user.is_app_admin:
            ctx["houses"] = House.objects.filter(is_active=True).order_by("code")
        elif user.house:
            ctx["houses"] = House.objects.filter(pk=user.house_id)
        else:
            ctx["houses"] = House.objects.none()
        ctx["can_manage"] = (
            user.is_authenticated and user.can_manage_financials
        )

        # Privacy: show contact info only if authenticated
        ctx["show_contact"] = user.is_authenticated

        # Build set of member PKs in user's own house (or all if admin)
        own_house_ids = set()
        if user.is_authenticated:
            if user.is_app_admin or user.is_superuser:
                # Admin sees all contact info
                own_house_ids = set(Member.objects.values_list("pk", flat=True))
            elif user.house:
                own_house_ids = set(
                    Residency.objects.filter(
                        apartment__house=user.house, end_date__isnull=True
                    ).values_list("member_id", flat=True)
                )
        ctx["own_house_member_ids"] = own_house_ids

        return ctx


class MemberDetailView(ViewerRequiredMixin, DetailView):
    """Détail d'un membre. Accessible aux utilisateurs connectés."""
    model = Member
    template_name = "members/member_detail.html"
    context_object_name = "member"

    def get_queryset(self):
        return _restrict_members_by_house(
            super().get_queryset(), self.request.user,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["residencies"] = self.object.residencies.select_related(
            "apartment__house",
        ).order_by("-start_date")
        ctx["can_manage"] = (
            self.request.user.is_authenticated
            and self.request.user.can_manage_financials
        )
        return ctx


class MemberCreateView(TreasurerRequiredMixin, CreateView):
    model = Member
    form_class = MemberForm
    template_name = "members/member_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse("members:member-detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        apartment_code = form.cleaned_data.get("apartment_code", "").strip()
        user = self.request.user
        house = user.house
        if apartment_code and house:
            apartment, _created = Apartment.objects.get_or_create(
                house=house, code=apartment_code,
            )
            Residency.objects.create(
                member=self.object,
                apartment=apartment,
                start_date=date.today(),
            )
        messages.success(self.request, f"Membre \u00ab {self.object.display_name} \u00bb cree.")
        return response


class MemberUpdateView(TreasurerRequiredMixin, UpdateView):
    model = Member
    form_class = MemberForm
    template_name = "members/member_form.html"

    def get_queryset(self):
        return _restrict_members_by_house(
            super().get_queryset(), self.request.user,
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["is_update"] = True
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse("members:member-detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request, f"Membre \u00ab {self.object.display_name} \u00bb mis a jour.",
        )
        return response


# -- Apartments --------------------------------------------------------------

class ApartmentListView(ViewerRequiredMixin, ListView):
    """Liste des appartements. Accessible aux utilisateurs connectés."""
    model = Apartment
    template_name = "members/apartment_list.html"
    context_object_name = "apartments"

    def get_queryset(self):
        qs = Apartment.objects.select_related("house").prefetch_related(
            Prefetch(
                "residencies",
                queryset=Residency.objects.filter(
                    end_date__isnull=True,
                ).select_related("member"),
                to_attr="current_residencies",
            ),
        )
        show = self.request.GET.get("show", "active")
        if show == "active":
            qs = qs.filter(is_active=True)
        elif show == "inactive":
            qs = qs.filter(is_active=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["show"] = self.request.GET.get("show", "active")
        ctx["can_manage"] = (
            self.request.user.is_authenticated
            and self.request.user.can_manage_financials
        )
        return ctx


class ApartmentCreateView(TreasurerRequiredMixin, CreateView):
    model = Apartment
    form_class = ApartmentForm
    template_name = "members/apartment_form.html"
    success_url = reverse_lazy("members:apartment-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        if not self.request.user.is_gestionnaire:
            form.instance.house = self.request.user.house
        response = super().form_valid(form)
        messages.success(self.request, f"Appartement \u00ab {self.object} \u00bb cree.")
        return response


class ApartmentUpdateView(TreasurerRequiredMixin, UpdateView):
    model = Apartment
    form_class = ApartmentForm
    template_name = "members/apartment_form.html"
    success_url = reverse_lazy("members:apartment-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        if not self.request.user.is_gestionnaire:
            form.instance.house = self.request.user.house
        response = super().form_valid(form)
        messages.success(self.request, f"Appartement \u00ab {self.object} \u00bb mis a jour.")
        return response


# -- Residencies -------------------------------------------------------------

class ResidencyCreateView(TreasurerRequiredMixin, CreateView):
    model = Residency
    form_class = ResidencyForm
    template_name = "members/residency_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        if "member" in self.request.GET:
            initial["member"] = self.request.GET["member"]
        if "apartment" in self.request.GET:
            initial["apartment"] = self.request.GET["apartment"]
        return initial

    def get_success_url(self):
        return reverse("members:member-detail", kwargs={"pk": self.object.member.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"Residence creee pour \u00ab {self.object.member.display_name} \u00bb.",
        )
        return response


class ResidencyUpdateView(TreasurerRequiredMixin, UpdateView):
    model = Residency
    form_class = ResidencyForm
    template_name = "members/residency_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse("members:member-detail", kwargs={"pk": self.object.member.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"Residence mise a jour pour \u00ab {self.object.member.display_name} \u00bb.",
        )
        return response
