"""Authorization helpers for role-aware Django and DRF views."""
from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from rest_framework.permissions import BasePermission

from .models import User


def user_has_minimum_role(user: object, required_role: str) -> bool:
    """Return whether a user-like object satisfies a required application role."""
    if not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_active", False):
        return False
    if getattr(user, "is_superuser", False):
        return True

    has_minimum_role = getattr(user, "has_minimum_role", None)
    if callable(has_minimum_role):
        return bool(has_minimum_role(required_role))
    return False


class RoleRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Gate class-based views behind a minimum application role."""

    required_role = User.Role.VIEWER
    raise_exception = True

    def test_func(self) -> bool:
        """Return whether the current request user meets the role requirement."""
        return user_has_minimum_role(self.request.user, self.required_role)


class TreasurerRequiredMixin(RoleRequiredMixin):
    """Require treasurer-level or higher access."""

    required_role = User.Role.TREASURER


class AdminRequiredMixin(RoleRequiredMixin):
    """Require admin-level or higher access."""

    required_role = User.Role.ADMIN


class MinimumRolePermission(BasePermission):
    """Gate DRF views behind a minimum application role."""

    required_role = User.Role.VIEWER
    message = "You do not have permission to access this resource."

    def has_permission(self, request, view) -> bool:
        """Return whether the request user meets the role requirement."""
        return user_has_minimum_role(request.user, self.required_role)


class TreasurerPermission(MinimumRolePermission):
    """Require treasurer-level or higher API access."""

    required_role = User.Role.TREASURER


class AdminPermission(MinimumRolePermission):
    """Require admin-level or higher API access."""

    required_role = User.Role.ADMIN
