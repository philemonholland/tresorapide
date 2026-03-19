from django.contrib import admin
from .models import House


@admin.register(House)
class HouseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "account_number", "treasurer_member", "correspondent_member", "is_active")
    search_fields = ("code", "name", "account_number")
    list_filter = ("is_active",)
    raw_id_fields = ("treasurer_member", "correspondent_member")
