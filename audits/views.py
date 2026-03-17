"""Read-only audit browsing views."""
from __future__ import annotations

import json

from django.db.models import Q
from django.views.generic import DetailView, ListView

from accounts.access import TreasurerRequiredMixin
from audits.models import AuditLogEntry


class AuditLogListView(TreasurerRequiredMixin, ListView):
    """Browse append-only audit history."""

    model = AuditLogEntry
    template_name = "audits/list.html"
    context_object_name = "audit_entries"
    paginate_by = 50

    def get_queryset(self):
        """Filter audit history by app, action, or search text."""

        queryset = AuditLogEntry.objects.select_related("actor").order_by("-created_at", "-id")
        action = self.request.GET.get("action", "").strip()
        app_label = self.request.GET.get("app", "").strip()
        search_term = self.request.GET.get("q", "").strip()
        if action:
            queryset = queryset.filter(action=action)
        if app_label:
            queryset = queryset.filter(target_app_label=app_label)
        if search_term:
            queryset = queryset.filter(
                Q(summary__icontains=search_term)
                | Q(target_object_id__icontains=search_term)
                | Q(actor__username__icontains=search_term)
            )
        return queryset

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Add filter options for common audit browsing tasks."""

        context = super().get_context_data(**kwargs)
        context.update(
            {
                "available_actions": (
                    AuditLogEntry.objects.order_by()
                    .values_list("action", flat=True)
                    .distinct()
                ),
                "available_apps": (
                    AuditLogEntry.objects.order_by()
                    .values_list("target_app_label", flat=True)
                    .distinct()
                ),
                "current_action": self.request.GET.get("action", "").strip(),
                "current_app": self.request.GET.get("app", "").strip(),
                "search_term": self.request.GET.get("q", "").strip(),
            }
        )
        return context


class AuditLogDetailView(TreasurerRequiredMixin, DetailView):
    """Inspect a single audit entry in read-only form."""

    model = AuditLogEntry
    template_name = "audits/detail.html"
    context_object_name = "audit_entry"
    queryset = AuditLogEntry.objects.select_related("actor")

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Pretty-print JSON payloads for easier review."""

        context = super().get_context_data(**kwargs)
        context["payload_json"] = json.dumps(
            self.object.payload,
            indent=2,
            sort_keys=True,
        )
        return context
