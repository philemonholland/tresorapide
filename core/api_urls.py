"""API routes for lightweight bootstrap endpoints."""
from __future__ import annotations

from django.urls import path

from .views import HealthcheckView, ReadinessView

urlpatterns = [
    path("health/", HealthcheckView.as_view(), name="api-health"),
    path("ready/", ReadinessView.as_view(), name="api-ready"),
]
