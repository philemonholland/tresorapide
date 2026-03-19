"""House management views — admin/superuser only for create/edit."""
from django.contrib import messages
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView

from accounts.access import AdminRequiredMixin
from accounts.models import Role, User
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
        house = self.object
        code = house.code.upper()
        created_accounts = []

        # Auto-create treasurer account
        t_username = f"tresorier-{code}"
        if not User.objects.filter(username=t_username).exists():
            t_user = User(username=t_username, role=Role.TREASURER, house=house)
            t_user.set_password(f"tresorier-{code}")
            t_user.save()
            created_accounts.append(t_username)

        # Auto-create member (viewer) account
        m_username = f"membre-{code}"
        if not User.objects.filter(username=m_username).exists():
            m_user = User(username=m_username, role=Role.VIEWER, house=house)
            m_user.set_password(f"membre-{code}")
            m_user.save()
            created_accounts.append(m_username)

        msg = f"Maison « {house} » créée."
        if created_accounts:
            msg += f" Comptes créés : {', '.join(created_accounts)}."
        messages.success(self.request, msg)
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
