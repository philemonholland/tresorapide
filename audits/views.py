"""Audit log views — read-only list and detail."""
from __future__ import annotations

from django.views.generic import DetailView, ListView

from accounts.access import TreasurerRequiredMixin
from audits.models import AuditLogEntry


class AuditLogListView(TreasurerRequiredMixin, ListView):
    model = AuditLogEntry
    template_name = "audits/list.html"
    context_object_name = "entries"
    paginate_by = 50


class AuditLogDetailView(TreasurerRequiredMixin, DetailView):
    model = AuditLogEntry
    template_name = "audits/detail.html"
    context_object_name = "entry"
