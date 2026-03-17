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
            "Application access",
            {
                "fields": ("role",),
            },
        ),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        (
            "Application access",
            {
                "classes": ("wide",),
                "fields": ("role",),
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
