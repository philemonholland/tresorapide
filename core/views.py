"""Views for dashboard, setup, and API foundation pages."""
from __future__ import annotations

from typing import Any

from django.db import connections, models
from django.db.models import Count, Q
from django.db.utils import DatabaseError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import AdminRequiredMixin, user_has_minimum_role
from accounts.models import User
from core.device import (
    apply_site_mode_preference,
    handheld_capture_enabled_for_request,
    handheld_capture_enabled_for_user,
)


class HomeView(TemplateView):
    """Render the landing page or authenticated transparency dashboard."""

    template_name = "core/home.html"

    def get(self, request, *args: object, **kwargs: object):
        apply_site_mode_preference(request)
        if handheld_capture_enabled_for_request(request):
            return redirect("bons:mobile-capture")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["needs_initial_superuser"] = User.objects.count() == 0
        context["first_superuser_command"] = (
            'docker compose --env-file ".env" exec web python manage.py createsuperuser'
        )
        context["show_dashboard"] = user_has_minimum_role(user, User.Role.VIEWER)
        context["show_mobile_capture_link"] = handheld_capture_enabled_for_user(user)
        if not context["show_dashboard"]:
            return context

        from bons.models import BonDeCommande
        from budget.models import BudgetYear, Expense
        from members.models import Apartment, Member

        house = getattr(user, "house", None)

        budget_qs = BudgetYear.objects.select_related("house").order_by("-year")
        bon_qs = BonDeCommande.objects.select_related("house", "sub_budget").order_by("-purchase_date")
        expense_qs = Expense.objects.all()

        if house and not user.is_gestionnaire:
            budget_qs = budget_qs.filter(house=house)
            bon_qs = bon_qs.filter(house=house)
            expense_qs = expense_qs.filter(budget_year__house=house)

        # Current (most recent open) budget year for the hero card
        current_by = budget_qs.filter(is_closed=False).first()
        context["current_budget_year"] = current_by

        # Simple stats
        total_budget = current_by.annual_budget_total if current_by else 0
        total_spent = (
            expense_qs.filter(budget_year=current_by).aggregate(
                total=models.Sum("amount")
            )["total"]
            or 0
        ) if current_by else 0
        context["total_budget"] = total_budget
        context["total_remaining"] = total_budget - total_spent
        context["bon_count"] = bon_qs.filter(
            budget_year=current_by
        ).count() if current_by else bon_qs.count()
        context["member_count"] = Member.objects.filter(is_active=True).count()

        # Admin / Gestionnaire summary — shown below treasurer view
        if user_has_minimum_role(user, User.Role.ADMIN):
            context["admin_summary"] = {
                "user_count": User.objects.count(),
                "active_user_count": User.objects.filter(is_active=True).count(),
                "member_count": Member.objects.count(),
                "apartment_count": Apartment.objects.count(),
                "setup_url": reverse("admin-setup"),
            }

        return context


class AdminSetupView(AdminRequiredMixin, TemplateView):
    """Show a read-only setup and user-management hub for app admins."""

    template_name = "core/admin_setup.html"

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        context = super().get_context_data(**kwargs)

        from audits.models import AuditLogEntry
        from budget.models import BudgetYear
        from members.models import Member

        context["role_counts"] = [
            {
                "label": label,
                "count": User.objects.filter(role=value, is_active=True).count(),
            }
            for value, label in User.Role.choices
        ]
        context["setup_checks"] = [
            {
                "label": "Administrateurs actifs",
                "count": User.objects.filter(role=User.Role.ADMIN, is_active=True).count(),
                "hint": "Les administrateurs peuvent accéder au hub de configuration et consulter l'historique financier.",
            },
            {
                "label": "Staff / superutilisateurs",
                "count": User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).count(),
                "hint": "L'accès staff est nécessaire pour les écrans de gestion Django admin.",
            },
            {
                "label": "Membres configurés",
                "count": Member.objects.count(),
                "hint": "Les membres et appartements supportent l'historique des bons de commande.",
            },
            {
                "label": "Années budgétaires configurées",
                "count": BudgetYear.objects.count(),
                "hint": "Les années budgétaires permettent le suivi prévu vs réalisé.",
            },
            {
                "label": "Entrées d'audit enregistrées",
                "count": AuditLogEntry.objects.count(),
                "hint": "L'historique d'audit devrait croître au fur et à mesure des actions du trésorier.",
            },
        ]
        context["latest_users"] = User.objects.order_by("-date_joined", "-id")[:12]
        context["management_links"] = [
            {
                "label": "Comptes utilisateurs",
                "url": reverse("admin:accounts_user_changelist"),
                "description": "Créer des comptes, attribuer des rôles et gérer l'accès staff.",
            },
            {
                "label": "Membres et appartements",
                "url": reverse("admin:members_member_changelist"),
                "description": "Gérer les fiches de membres et résidences de la coopérative.",
            },
            {
                "label": "Configuration budgétaire",
                "url": reverse("admin:budget_budgetyear_changelist"),
                "description": "Configurer les budgets annuels et les montants prévus des sous-budgets.",
            },
            {
                "label": "Bons de commande",
                "url": reverse("admin:bons_bondecommande_changelist"),
                "description": "Consulter les bons de commande, reçus et instantanés.",
            },
            {
                "label": "Journal d'audit",
                "url": reverse("admin:audits_auditlogentry_changelist"),
                "description": "Ouvrir l'historique d'audit en ajout seulement dans Django admin.",
            },
            {
                "label": "Index Django admin",
                "url": reverse("admin:index"),
                "description": "Accéder à l'interface complète de Django admin.",
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
        return Response({"status": "ok", "service": "tresorapide"})


class ReadinessView(APIView):
    """Confirm the app can answer requests and reach its configured database."""

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
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
