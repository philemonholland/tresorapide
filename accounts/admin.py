"""Admin configuration for authentication models."""
from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Expose the custom user model in the Django admin."""

    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "Accès application",
            {
                "fields": ("role", "house", "member"),
            },
        ),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        (
            "Accès application",
            {
                "classes": ("wide",),
                "fields": ("role", "house", "member"),
            },
        ),
    )
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "role",
        "is_staff",
        "is_active",
    )
    list_filter = DjangoUserAdmin.list_filter + ("role",)
