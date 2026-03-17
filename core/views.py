"""Views for dashboard, setup, and API foundation pages."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import connections
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.db.utils import DatabaseError
from django.http import HttpResponse
from django.urls import reverse
from django.views.generic import TemplateView
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import AdminRequiredMixin, user_has_minimum_role
from accounts.models import User
from audits.models import AuditLogEntry
from budget.models import BudgetYear
from members.models import Apartment, Member
from reimbursements.models import ReceiptFile, Reimbursement, ReimbursementStatus
from reimbursements.queries import (
    transparency_visibility_note,
    visible_reimbursements_for_user,
)

MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)
ZERO_MONEY = Value(Decimal("0.00"), output_field=MONEY_FIELD)


class HomeView(TemplateView):
    """Render the landing page or authenticated transparency dashboard."""

    template_name = "core/home.html"

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Add read-only dashboard context for authenticated users."""

        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["show_dashboard"] = user_has_minimum_role(user, User.Role.VIEWER)
        if not context["show_dashboard"]:
            return context

        visible_reimbursements = visible_reimbursements_for_user(user, with_receipt_counts=False)
        budget_years = (
            BudgetYear.objects.annotate(
                category_count=Count("categories", distinct=True),
                planned_total=Coalesce(
                    Sum("categories__planned_amount"),
                    ZERO_MONEY,
                    output_field=MONEY_FIELD,
                ),
            )
            .order_by("-start_date", "-id")
        )
        transparency_summary = visible_reimbursements.aggregate(
            reimbursement_count=Count("id"),
            archived_count=Count("id", filter=Q(archived_at__isnull=False)),
            void_count=Count("id", filter=Q(status=ReimbursementStatus.VOID)),
            approved_total=Coalesce(
                Sum(
                    "amount_approved",
                    filter=Q(
                        status__in=(
                            ReimbursementStatus.APPROVED,
                            ReimbursementStatus.PAID,
                        )
                    ),
                ),
                ZERO_MONEY,
                output_field=MONEY_FIELD,
            ),
            paid_total=Coalesce(
                Sum(
                    "amount_approved",
                    filter=Q(status=ReimbursementStatus.PAID),
                ),
                ZERO_MONEY,
                output_field=MONEY_FIELD,
            ),
        )
        context.update(
            {
                "budget_years": budget_years[:5],
                "budget_year_count": budget_years.count(),
                "transparency_summary": transparency_summary,
                "recent_reimbursements": visible_reimbursements.order_by(
                    "-expense_date",
                    "-created_at",
                    "-id",
                )[:6],
                "transparency_note": transparency_visibility_note(user),
            }
        )

        if user_has_minimum_role(user, User.Role.TREASURER):
            context["recent_audit_entries"] = AuditLogEntry.objects.select_related("actor")[:6]

        if user_has_minimum_role(user, User.Role.ADMIN):
            context["admin_summary"] = {
                "user_count": User.objects.count(),
                "active_user_count": User.objects.filter(is_active=True).count(),
                "staff_user_count": User.objects.filter(is_staff=True).count(),
                "member_count": Member.objects.count(),
                "apartment_count": Apartment.objects.count(),
                "receipt_count": ReceiptFile.objects.count(),
                "setup_url": reverse("admin-setup"),
            }

        return context


class AdminSetupView(AdminRequiredMixin, TemplateView):
    """Show a read-only setup and user-management hub for app admins."""

    template_name = "core/admin_setup.html"

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Provide setup summaries and admin links."""

        context = super().get_context_data(**kwargs)
        context["role_counts"] = [
            {
                "label": label,
                "count": User.objects.filter(role=value, is_active=True).count(),
            }
            for value, label in User.Role.choices
        ]
        context["setup_checks"] = [
            {
                "label": "Active app admins",
                "count": User.objects.filter(role=User.Role.ADMIN, is_active=True).count(),
                "hint": "App admins can open the setup hub and review financial history.",
            },
            {
                "label": "Staff/superusers",
                "count": User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).count(),
                "hint": "Staff access is needed for full Django admin management screens.",
            },
            {
                "label": "Members configured",
                "count": Member.objects.count(),
                "hint": "Members and apartments support reimbursement history snapshots.",
            },
            {
                "label": "Budget years configured",
                "count": BudgetYear.objects.count(),
                "hint": "Budget years drive planned-versus-used reporting.",
            },
            {
                "label": "Audit entries recorded",
                "count": AuditLogEntry.objects.count(),
                "hint": "Audit history should grow as treasurer actions are performed.",
            },
        ]
        context["latest_users"] = User.objects.order_by("-date_joined", "-id")[:12]
        context["management_links"] = [
            {
                "label": "User accounts",
                "url": reverse("admin:accounts_user_changelist"),
                "description": "Create accounts, set roles, and manage staff access.",
            },
            {
                "label": "Members and apartments",
                "url": reverse("admin:members_member_changelist"),
                "description": "Maintain co-op membership and residency records.",
            },
            {
                "label": "Budget setup",
                "url": reverse("admin:budget_budgetyear_changelist"),
                "description": "Configure yearly budgets and category planned amounts.",
            },
            {
                "label": "Reimbursement archive",
                "url": reverse("admin:reimbursements_reimbursement_changelist"),
                "description": "Review reimbursement records, archived receipts, and snapshots.",
            },
            {
                "label": "Audit log",
                "url": reverse("admin:audits_auditlogentry_changelist"),
                "description": "Open append-only audit history in Django admin.",
            },
            {
                "label": "Django admin index",
                "url": reverse("admin:index"),
                "description": "Jump to the full Django admin surface.",
            },
        ]
        context["can_access_django_admin"] = (
            self.request.user.is_staff or self.request.user.is_superuser
        )
        return context


class HealthcheckView(APIView):
    """Expose a tiny public API endpoint to verify the service is alive."""

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        """Return a deterministic health response."""
        return Response({"status": "ok", "service": "tresorapide"})


class ReadinessView(APIView):
    """Confirm the app can answer requests and reach its configured database."""

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        """Return a readiness response that includes database reachability."""
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except DatabaseError:
            return Response(
                {
                    "status": "error",
                    "service": "tresorapide",
                    "database": "unavailable",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "status": "ok",
                "service": "tresorapide",
                "database": "ok",
            }
        )
