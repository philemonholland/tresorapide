from django.contrib import admin
from .models import Member, Apartment, Residency


class ResidencyInline(admin.TabularInline):
    model = Residency
    extra = 0
    fields = ("apartment", "start_date", "end_date", "is_primary_contact")


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "email", "phone_number", "is_active")
    list_filter = ("is_active",)
    search_fields = ("first_name", "last_name", "email", "phone_number")
    inlines = [ResidencyInline]


@admin.register(Apartment)
class ApartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "house", "street_address", "is_active")
    list_filter = ("house", "is_active")
    search_fields = ("code", "street_address")


@admin.register(Residency)
class ResidencyAdmin(admin.ModelAdmin):
    list_display = ("member", "apartment", "start_date", "end_date", "is_current")
    list_filter = ("apartment__house",)
    search_fields = ("member__first_name", "member__last_name", "apartment__code")
    raw_id_fields = ("member", "apartment")
