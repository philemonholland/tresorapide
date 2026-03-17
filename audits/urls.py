"""Read-only audit browsing routes."""
from __future__ import annotations

from django.urls import path

from audits.views import AuditLogDetailView, AuditLogListView

app_name = "audits"

urlpatterns = [
    path("", AuditLogListView.as_view(), name="list"),
    path("<int:pk>/", AuditLogDetailView.as_view(), name="detail"),
]
