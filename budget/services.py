"""Budget roll-up services for reimbursement reporting totals."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import QuerySet, Sum

from budget.models import BudgetCategory, BudgetYear
from reimbursements.models import Reimbursement, ReimbursementStatus


ZERO_AMOUNT = Decimal("0.00")


class BudgetRollupService:
    """Recalculate persisted reimbursement totals for category and year roll-ups."""

    tracked_approved_statuses = (
        ReimbursementStatus.APPROVED,
        ReimbursementStatus.PAID,
    )

    def sync_for_reimbursement(self, reimbursement: Reimbursement) -> None:
        """Refresh the category and budget year roll-ups touched by a reimbursement."""

        self.sync_category(reimbursement.budget_category)
        self.sync_year(reimbursement.budget_year)

    def sync_category(self, category: BudgetCategory) -> BudgetCategory:
        """Recalculate totals for a single budget category."""

        totals = self._aggregate_totals(
            Reimbursement.objects.filter(budget_category=category)
        )
        category.approved_reimbursement_total = totals["approved_reimbursement_total"]
        category.paid_reimbursement_total = totals["paid_reimbursement_total"]
        category.save(
            update_fields=[
                "approved_reimbursement_total",
                "paid_reimbursement_total",
                "updated_at",
            ]
        )
        return category

    def sync_year(self, budget_year: BudgetYear) -> BudgetYear:
        """Recalculate totals for a budget year across all categories."""

        totals = self._aggregate_totals(
            Reimbursement.objects.filter(budget_year=budget_year)
        )
        budget_year.approved_reimbursement_total = totals["approved_reimbursement_total"]
        budget_year.paid_reimbursement_total = totals["paid_reimbursement_total"]
        budget_year.save(
            update_fields=[
                "approved_reimbursement_total",
                "paid_reimbursement_total",
                "updated_at",
            ]
        )
        return budget_year

    def _aggregate_totals(
        self, reimbursements: QuerySet[Reimbursement]
    ) -> dict[str, Decimal]:
        """Aggregate approved and paid totals for a reimbursement queryset."""

        approved_total = reimbursements.filter(
            status__in=self.tracked_approved_statuses
        ).aggregate(total=Sum("amount_approved"))["total"] or ZERO_AMOUNT
        paid_total = reimbursements.filter(status=ReimbursementStatus.PAID).aggregate(
            total=Sum("amount_approved")
        )["total"] or ZERO_AMOUNT
        approved_total = approved_total.quantize(Decimal("0.01"))
        paid_total = paid_total.quantize(Decimal("0.01"))
        return {
            "approved_reimbursement_total": approved_total,
            "paid_reimbursement_total": paid_total,
        }
