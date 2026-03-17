"""URL routes for server-rendered foundation pages."""
from __future__ import annotations

from django.urls import path

from .views import AdminSetupView, HomeView

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("setup/", AdminSetupView.as_view(), name="admin-setup"),
]
