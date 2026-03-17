"""Read-only transparency query helpers for reimbursements."""
from __future__ import annotations

from django.db.models import Count, Q, QuerySet
from django.urls import NoReverseMatch, reverse

from accounts.access import user_has_minimum_role
from accounts.models import User

from .models import Reimbursement, ReimbursementStatus

VIEWER_HIDDEN_STATUSES = frozenset(
    {
        ReimbursementStatus.DRAFT,
        ReimbursementStatus.SUBMITTED,
    }
)


def viewer_visible_reimbursement_q(prefix: str = "") -> Q:
    """Return the queryset predicate for viewer-visible reimbursements."""

    return ~Q(
        **{
            f"{prefix}status__in": tuple(VIEWER_HIDDEN_STATUSES),
            f"{prefix}archived_at__isnull": True,
        }
    )


def can_user_review_internal_reimbursements(user: object) -> bool:
    """Return whether the user can inspect active internal workflow states."""

    return user_has_minimum_role(user, User.Role.TREASURER)


def can_user_view_reimbursement(user: object, reimbursement: Reimbursement) -> bool:
    """Return whether the user may view the reimbursement in read-only surfaces."""

    if can_user_review_internal_reimbursements(user):
        return True
    if not user_has_minimum_role(user, User.Role.VIEWER):
        return False
    return not (
        reimbursement.status in VIEWER_HIDDEN_STATUSES
        and reimbursement.archived_at is None
    )


def visible_reimbursements_for_user(
    user: object,
    *,
    with_receipt_counts: bool = True,
) -> QuerySet[Reimbursement]:
    """Return a role-filtered reimbursement queryset for transparency pages."""

    queryset: QuerySet[Reimbursement] = Reimbursement.objects.select_related(
        "requested_by_member",
        "apartment",
        "budget_year",
        "budget_category",
        "created_by",
        "approved_by",
        "paid_by",
        "signature_verified_by",
    )
    if with_receipt_counts:
        queryset = queryset.annotate(
            active_receipt_count=Count(
                "receipt_files",
                filter=Q(receipt_files__archived_at__isnull=True),
                distinct=True,
            ),
            archived_receipt_count=Count(
                "receipt_files",
                filter=Q(receipt_files__archived_at__isnull=False),
                distinct=True,
            ),
        )
    if can_user_review_internal_reimbursements(user):
        return queryset
    if user_has_minimum_role(user, User.Role.VIEWER):
        return queryset.filter(viewer_visible_reimbursement_q())
    return queryset.none()


def transparency_visibility_note(user: object) -> str:
    """Return a concise description of the user's transparency boundary."""

    if can_user_review_internal_reimbursements(user):
        return (
            "Treasurer and admin users can review every reimbursement, including "
            "draft and submitted claims."
        )
    if user_has_minimum_role(user, User.Role.VIEWER):
        return (
            "Viewer access is read-only and hides active draft or submitted claims "
            "until they are finalized or archived."
        )
    return ""


def optional_pdf_download_url(reimbursement: Reimbursement) -> str | None:
    """Return a PDF link when a compatible download route is available."""

    attempts = (
        {"args": [reimbursement.pk], "kwargs": {}},
        {"args": [reimbursement.reference_code], "kwargs": {}},
        {"args": [], "kwargs": {"pk": reimbursement.pk}},
        {"args": [], "kwargs": {"reference_code": reimbursement.reference_code}},
        {"args": [], "kwargs": {"reimbursement_id": reimbursement.pk}},
    )
    for attempt in attempts:
        try:
            return reverse(
                "reimbursements:pdf-download",
                args=attempt["args"],
                kwargs=attempt["kwargs"],
            )
        except NoReverseMatch:
            continue
    return None
