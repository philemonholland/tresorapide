from decimal import Decimal
from django.db.models import Sum, Q

from .models import BudgetYear, SubBudget, Expense


class BudgetCalculationService:
    """Reproduce the Excel budget formulas in service-layer code."""

    @staticmethod
    def base_values(budget_year):
        budget_total = budget_year.annual_budget_total
        snow_budget = budget_year.snow_budget
        imprevues = budget_total * budget_year.imprevues_rate
        budget_minus_imprevues = budget_total - imprevues
        expenses_to_date = Expense.objects.filter(
            budget_year=budget_year
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        return {
            "budget_total": budget_total,
            "snow_budget": snow_budget,
            "imprevues": imprevues,
            "budget_minus_imprevues": budget_minus_imprevues,
            "expenses_to_date": expenses_to_date,
        }

    @staticmethod
    def repair_totals(budget_year):
        planned = SubBudget.objects.filter(
            budget_year=budget_year, trace_code__in=[1, 2]
        ).aggregate(total=Sum("planned_amount"))["total"] or Decimal("0")
        used = Expense.objects.filter(
            budget_year=budget_year, sub_budget__trace_code__in=[1, 2]
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        return {
            "planned": planned,
            "used": used,
            "remaining": planned - used,
        }

    @staticmethod
    def imprevues_totals(budget_year):
        imprevues = budget_year.annual_budget_total * budget_year.imprevues_rate
        used = Expense.objects.filter(
            budget_year=budget_year, sub_budget__trace_code=0
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        return {
            "imprevues": imprevues,
            "used": used,
            "remaining": imprevues - used,
        }

    @staticmethod
    def available_money(budget_year):
        budget_total = budget_year.annual_budget_total
        imprevues = budget_total * budget_year.imprevues_rate
        budget_minus_imprevues = budget_total - imprevues
        imprevues_used = Expense.objects.filter(
            budget_year=budget_year, sub_budget__trace_code=0
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        total_used_non_imprevues = Expense.objects.filter(
            budget_year=budget_year
        ).exclude(sub_budget__trace_code=0).aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0")
        return {
            "available": budget_total - total_used_non_imprevues - imprevues_used,
            "available_minus_imprevues": budget_minus_imprevues - total_used_non_imprevues,
        }

    @staticmethod
    def category_summary(budget_year):
        """Per-sub-budget breakdown: planned, used, remaining."""
        sub_budgets = SubBudget.objects.filter(
            budget_year=budget_year, is_active=True
        ).order_by("sort_order", "trace_code")
        result = []
        for sb in sub_budgets:
            used = Expense.objects.filter(
                sub_budget=sb
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            result.append({
                "sub_budget": sb,
                "trace_code": sb.trace_code,
                "name": sb.name,
                "planned": sb.planned_amount,
                "used": used,
                "remaining": sb.planned_amount - used,
            })
        return result

    @staticmethod
    def running_balances(budget_year):
        """
        Compute running balance and balance-minus-imprevues for
        the expense ledger in deterministic order.
        """
        budget_total = budget_year.annual_budget_total
        imprevues = budget_total * budget_year.imprevues_rate
        budget_minus_imprevues = budget_total - imprevues

        expenses = Expense.objects.filter(
            budget_year=budget_year
        ).select_related("sub_budget").order_by("entry_date", "created_at", "id")

        result = []
        cumulative = Decimal("0")
        cumulative_non_imprevues = Decimal("0")
        for exp in expenses:
            cumulative += exp.amount
            if exp.sub_budget.trace_code != 0:
                cumulative_non_imprevues += exp.amount
            result.append({
                "expense": exp,
                "balance": budget_total - cumulative,
                "balance_minus_imprevues": budget_minus_imprevues - cumulative_non_imprevues,
            })
        return result

    @staticmethod
    def unbudgeted_available(budget_year):
        """Money not allocated to any specific sub-budget category."""
        budget_total = budget_year.annual_budget_total
        imprevues = budget_total * budget_year.imprevues_rate
        budget_minus_imprevues = budget_total - imprevues

        planned_non_contingency = SubBudget.objects.filter(
            budget_year=budget_year, is_active=True, is_contingency=False
        ).aggregate(total=Sum("planned_amount"))["total"] or Decimal("0")

        return {
            "unbudgeted_available": budget_total - planned_non_contingency - imprevues,
            "unbudgeted_available_minus_15": budget_minus_imprevues - planned_non_contingency,
        }
