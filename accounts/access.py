from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from .models import Role


def user_has_minimum_role(user, role):
    if not user.is_authenticated or not user.is_active:
        return False
    return user.has_minimum_role(role)


class RoleRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    required_role = Role.VIEWER
    raise_exception = True

    def test_func(self):
        return user_has_minimum_role(self.request.user, self.required_role)


class TreasurerRequiredMixin(RoleRequiredMixin):
    required_role = Role.TREASURER


class AdminRequiredMixin(RoleRequiredMixin):
    required_role = Role.ADMIN


class GestionnaireRequiredMixin(RoleRequiredMixin):
    required_role = Role.GESTIONNAIRE


class ViewerRequiredMixin(RoleRequiredMixin):
    required_role = Role.VIEWER


def check_house_permission(user, house):
    """Raise PermissionDenied if user is a non-gestionnaire treasurer
    trying to access a different house."""
    if user.is_superuser or user.is_gestionnaire:
        return
    if user.house_id != house.id:
        raise PermissionDenied("Vous ne pouvez modifier que votre propre maison.")
