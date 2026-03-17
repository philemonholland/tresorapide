"""Admin registrations for members app."""

from __future__ import annotations

from django.contrib import admin

from members.models import Apartment, Member, Residency


class ResidencyInline(admin.TabularInline):
    """Inline residency history for quick admin review."""

    model = Residency
    extra = 0
    autocomplete_fields = ("apartment",)


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    """Admin configuration for members."""

    list_display = ("display_name", "email", "phone_number", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("first_name", "last_name", "preferred_name", "email")
    inlines = (ResidencyInline,)


class ApartmentResidencyInline(admin.TabularInline):
    """Inline apartment occupancy history."""

    model = Residency
    extra = 0
    autocomplete_fields = ("member",)


@admin.register(Apartment)
class ApartmentAdmin(admin.ModelAdmin):
    """Admin configuration for apartments."""

    list_display = ("code", "street_address", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "street_address")
    inlines = (ApartmentResidencyInline,)


@admin.register(Residency)
class ResidencyAdmin(admin.ModelAdmin):
    """Admin configuration for residency history."""

    list_display = ("member", "apartment", "start_date", "end_date", "is_current")
    list_filter = ("apartment",)
    search_fields = ("member__first_name", "member__last_name", "apartment__code")
    autocomplete_fields = ("member", "apartment")
