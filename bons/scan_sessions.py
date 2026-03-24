from __future__ import annotations

from django.utils import timezone

from budget.models import BudgetYear, SubBudget
from members.models import Member

from .models import BonDeCommande, BonStatus, ReceiptFile
from .services import generate_bon_number


def active_budget_years_for_house(house):
    return BudgetYear.objects.filter(
        house=house,
        is_active=True,
    ).order_by("-year")


def default_budget_year_for_house(house):
    budget_years = active_budget_years_for_house(house)
    current_year = timezone.now().year
    return budget_years.filter(year=current_year).first() or budget_years.first()


def default_sub_budget_for_budget_year(budget_year):
    return SubBudget.objects.filter(
        budget_year=budget_year,
        is_active=True,
    ).order_by("sort_order", "trace_code").first()


def default_purchaser_member_for_user(user):
    if user.member_id:
        return user.member
    return Member.objects.filter(is_active=True).first()


def create_scan_session(*, user, budget_year):
    sub_budget = default_sub_budget_for_budget_year(budget_year)
    if sub_budget is None:
        raise ValueError(
            "Aucun sous-budget actif n'est disponible pour l'année budgétaire sélectionnée."
        )
    purchaser_member = default_purchaser_member_for_user(user)
    if purchaser_member is None:
        raise ValueError(
            "Aucun membre actif n'est disponible pour initialiser la session de scan."
        )

    scan_session = BonDeCommande(
        house=user.house,
        budget_year=budget_year,
        number=generate_bon_number(user.house, budget_year.year),
        purchase_date=timezone.now().date(),
        short_description="(en cours de création)",
        total=0,
        sub_budget=sub_budget,
        purchaser_member=purchaser_member,
        created_by=user,
        status=BonStatus.DRAFT,
        is_scan_session=True,
        entered_date=timezone.now().date(),
    )
    super(BonDeCommande, scan_session).save()
    return scan_session


def add_receipts_to_scan_session(scan_session, files, *, uploaded_by):
    receipts = []
    for uploaded_file in files:
        receipts.append(
            ReceiptFile.objects.create(
                bon_de_commande=scan_session,
                file=uploaded_file,
                original_filename=uploaded_file.name,
                content_type=getattr(uploaded_file, "content_type", ""),
                uploaded_by=uploaded_by,
            )
        )
    return receipts
