"""Authentication-related views."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView

from accounts.access import TreasurerRequiredMixin
from accounts.forms import AccountCreateForm
from accounts.models import ROLE_PRIORITY, Role, User


class SetupAwareLoginView(auth_views.LoginView):
    """Expose first-user setup guidance on the login page when needed."""

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Add first-user setup hints for empty deployments."""

        context = super().get_context_data(**kwargs)
        context["needs_initial_superuser"] = User.objects.count() == 0
        context["first_superuser_command"] = (
            'docker compose --env-file ".env" exec web python manage.py createsuperuser'
        )
        return context


class AccountListView(TreasurerRequiredMixin, ListView):
    """List user accounts; strict hierarchy — only see accounts with role ≤ own."""

    model = User
    template_name = "accounts/list.html"
    context_object_name = "accounts"

    def get_queryset(self):
        qs = super().get_queryset().select_related("house", "member")
        user = self.request.user

        if user.is_superuser:
            return qs

        # Filter by role: only show accounts with priority ≤ user's own
        user_priority = ROLE_PRIORITY.get(user.role, 0)
        allowed_roles = [r for r, p in ROLE_PRIORITY.items() if p <= user_priority]
        qs = qs.filter(role__in=allowed_roles)

        # Exclude superusers from non-superuser views
        qs = qs.filter(is_superuser=False)

        # Admin/gestionnaire see all houses; treasurer only own house
        if user.is_gestionnaire or user.role == Role.ADMIN:
            return qs
        return qs.filter(house=user.house)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["can_create"] = True
        return context


class AccountCreateView(TreasurerRequiredMixin, CreateView):
    """Create a new user account with role-based permission checks."""

    model = User
    form_class = AccountCreateForm
    template_name = "accounts/create.html"
    success_url = reverse_lazy("accounts:list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["creating_user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"Le compte « {self.object.username} » a été créé avec succès.",
        )
        return response


# -- Delete is handled by a function-based view --
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required


@login_required
def account_delete_view(request, pk):
    """Delete a user account if the requesting user has permission."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    target = get_object_or_404(User, pk=pk)
    user = request.user

    if not user.has_minimum_role(Role.TREASURER):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    # Cannot delete yourself
    if target.pk == user.pk:
        messages.error(request, "Vous ne pouvez pas supprimer votre propre compte.")
        return redirect("accounts:list")

    # Cannot delete a higher or equal role (strict hierarchy)
    user_priority = ROLE_PRIORITY.get(user.role, 0)
    target_priority = ROLE_PRIORITY.get(target.role, 0)
    if not user.is_superuser and target_priority >= user_priority:
        messages.error(request, "Vous ne pouvez pas supprimer un compte de rôle supérieur ou égal.")
        return redirect("accounts:list")

    # Non-superuser cannot delete superusers
    if not user.is_superuser and target.is_superuser:
        messages.error(request, "Vous ne pouvez pas supprimer un superutilisateur.")
        return redirect("accounts:list")

    # Treasurer can only delete in their own house
    if (
        not user.is_superuser
        and not user.is_gestionnaire
        and user.role not in (Role.ADMIN,)
        and target.house_id != user.house_id
    ):
        messages.error(request, "Vous ne pouvez supprimer que les comptes de votre maison.")
        return redirect("accounts:list")

    username = target.username
    target.delete()
    messages.success(request, f"Le compte « {username} » a été supprimé.")
    return redirect("accounts:list")
