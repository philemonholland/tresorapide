"""House management views — admin/superuser only for create/edit."""
from django.contrib import messages
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView

from accounts.access import AdminRequiredMixin
from .forms import HouseForm
from .models import House


class HouseListView(ListView):
    """Liste des maisons — accessible à tous (lecture seule)."""
    model = House
    template_name = "houses/list.html"
    context_object_name = "houses"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_manage"] = (
            self.request.user.is_authenticated
            and (self.request.user.is_app_admin or self.request.user.is_superuser)
        )
        return ctx


class HouseDetailView(DetailView):
    """Détail d'une maison — accessible à tous."""
    model = House
    template_name = "houses/detail.html"
    context_object_name = "house"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_manage"] = (
            self.request.user.is_authenticated
            and (self.request.user.is_app_admin or self.request.user.is_superuser)
        )
        ctx["apartments"] = self.object.apartments.filter(is_active=True).order_by("code")
        return ctx


class HouseCreateView(AdminRequiredMixin, CreateView):
    """Créer une maison — admin/superuser seulement."""
    model = House
    form_class = HouseForm
    template_name = "houses/form.html"

    def get_success_url(self):
        return reverse("houses:detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Maison « {self.object} » créée.")
        return response


class HouseUpdateView(AdminRequiredMixin, UpdateView):
    """Modifier une maison — admin/superuser seulement."""
    model = House
    form_class = HouseForm
    template_name = "houses/form.html"

    def get_success_url(self):
        return reverse("houses:detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Maison « {self.object} » mise à jour.")
        return response
