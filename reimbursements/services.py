"""Centralized reimbursement workflow and validation services."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from audits.services import create_audit_log_entry
from budget.services import BudgetRollupService
from members.models import Member
from reimbursements.models import ReceiptFile, Reimbursement, ReimbursementStatus


FINALIZABLE_STATUSES = frozenset({ReimbursementStatus.SUBMITTED})


@dataclass(frozen=True)
class FinalValidationInput:
    """Explicit confirmation payload required for final reimbursement validation."""

    approver_member: Member
    signed_receipt_received: bool
    signature_verified: bool
    approved_amount: Decimal | None = None
    treasurer_member: Member | None = None
    note: str = ""


class ReimbursementWorkflowService:
    """Own reimbursement validation, audit logging, and budget side effects."""

    def __init__(self, budget_rollup_service: BudgetRollupService | None = None) -> None:
        self.budget_rollup_service = budget_rollup_service or BudgetRollupService()

    @transaction.atomic
    def finalize_validation(
        self,
        reimbursement: Reimbursement,
        *,
        actor: User,
        validation_input: FinalValidationInput,
    ) -> Reimbursement:
        """Finalize a submitted reimbursement after enforcing approval rules."""

        self._validate_finalization_request(
            reimbursement=reimbursement,
            actor=actor,
            validation_input=validation_input,
        )
        approved_amount = validation_input.approved_amount or reimbursement.amount_requested
        approval_timestamp = timezone.now()

        reimbursement.receipt_signed_by_member = validation_input.approver_member
        reimbursement.receipt_signer_name_snapshot = (
            validation_input.approver_member.display_name
        )
        reimbursement.signed_receipt_received_at = approval_timestamp
        reimbursement.signature_verified_at = approval_timestamp
        reimbursement.signature_verified_by = actor
        reimbursement.approved_by = actor
        reimbursement.amount_approved = approved_amount
        reimbursement.status = ReimbursementStatus.APPROVED
        reimbursement.approved_at = approval_timestamp
        reimbursement.notes = self._append_note(reimbursement.notes, validation_input.note)
        reimbursement.save()

        self.budget_rollup_service.sync_for_reimbursement(reimbursement)
        create_audit_log_entry(
            actor=actor,
            action="reimbursement.validated",
            target=reimbursement,
            summary=(
                f"Validated reimbursement {reimbursement.reference_code} "
                f"for {reimbursement.member_name_snapshot}."
            ),
            payload={
                "status_from": ReimbursementStatus.SUBMITTED,
                "status_to": ReimbursementStatus.APPROVED,
                "amount_approved": str(approved_amount),
                "receipt_signer_member_id": validation_input.approver_member.pk,
                "receipt_signer_name_snapshot": reimbursement.receipt_signer_name_snapshot,
                "treasurer_member_id": (
                    validation_input.treasurer_member.pk
                    if validation_input.treasurer_member is not None
                    else None
                ),
                "receipt_file_ids": list(
                    reimbursement.receipt_files.filter(archived_at__isnull=True).values_list(
                        "id", flat=True
                    )
                ),
            },
        )
        return reimbursement

    @transaction.atomic
    def void_reimbursement(
        self,
        reimbursement: Reimbursement,
        *,
        actor: User,
        reason: str,
    ) -> Reimbursement:
        """Void a reimbursement and refresh dependent budget totals."""

        if not actor.can_manage_financials:
            raise ValidationError(
                {"approved_by": "Only treasurer or admin users can void reimbursements."}
            )
        if not reason.strip():
            raise ValidationError({"void_reason": "A void reason is required."})
        if reimbursement.status == ReimbursementStatus.VOID:
            raise ValidationError({"status": "This reimbursement has already been voided."})

        previous_status = reimbursement.status
        reimbursement.status = ReimbursementStatus.VOID
        reimbursement.void_reason = reason.strip()
        reimbursement.voided_at = timezone.now()
        reimbursement.save()

        self.budget_rollup_service.sync_for_reimbursement(reimbursement)
        create_audit_log_entry(
            actor=actor,
            action="reimbursement.voided",
            target=reimbursement,
            summary=f"Voided reimbursement {reimbursement.reference_code}.",
            payload={
                "status_from": previous_status,
                "status_to": ReimbursementStatus.VOID,
                "void_reason": reimbursement.void_reason,
                "amount_approved": (
                    str(reimbursement.amount_approved)
                    if reimbursement.amount_approved is not None
                    else None
                ),
            },
        )
        return reimbursement

    @transaction.atomic
    def archive_receipt(
        self,
        receipt_file: ReceiptFile,
        *,
        actor: User | None,
        reason: str,
    ) -> ReceiptFile:
        """Archive a receipt file while preserving its original evidence record."""

        if not reason.strip():
            raise ValidationError({"archive_reason": "An archive reason is required."})
        if receipt_file.is_archived:
            raise ValidationError({"archived_at": "This receipt file is already archived."})
        if not receipt_file.original_filename or not receipt_file.sha256_checksum:
            raise ValidationError(
                {
                    "file": (
                        "Receipt files must retain the original filename and checksum "
                        "before archival."
                    )
                }
            )

        active_receipt_count = receipt_file.reimbursement.receipt_files.filter(
            archived_at__isnull=True
        ).count()
        if (
            receipt_file.reimbursement.status
            in {ReimbursementStatus.APPROVED, ReimbursementStatus.PAID}
            and active_receipt_count <= 1
        ):
            raise ValidationError(
                {
                    "archived_at": (
                        "Cannot archive the last active receipt for a finalized reimbursement."
                    )
                }
            )

        receipt_file.archive(reason=reason.strip())
        receipt_file.save(update_fields=["archived_at", "archive_reason", "updated_at"])
        create_audit_log_entry(
            actor=actor,
            action="reimbursement.receipt_archived",
            target=receipt_file,
            summary=(
                f"Archived receipt {receipt_file.original_filename} "
                f"for {receipt_file.reimbursement.reference_code}."
            ),
            payload={
                "reimbursement_id": receipt_file.reimbursement_id,
                "sha256_checksum": receipt_file.sha256_checksum,
                "archive_reason": receipt_file.archive_reason,
            },
        )
        return receipt_file

    def _validate_finalization_request(
        self,
        *,
        reimbursement: Reimbursement,
        actor: User,
        validation_input: FinalValidationInput,
    ) -> None:
        """Raise a validation error when final validation requirements are not met."""

        if reimbursement.status not in FINALIZABLE_STATUSES:
            raise ValidationError(
                {"status": "Only submitted reimbursements can be finally validated."}
            )
        if not actor.can_manage_financials:
            raise ValidationError(
                {
                    "approved_by": (
                        "Only treasurer or admin users can perform final validation."
                    )
                }
            )
        if validation_input.approver_member.pk == reimbursement.requested_by_member_id:
            raise ValidationError(
                {"receipt_signed_by_member": "Purchaser and approver must be different members."}
            )
        if (
            validation_input.treasurer_member is not None
            and validation_input.approver_member.pk == validation_input.treasurer_member.pk
        ):
            raise ValidationError(
                {
                    "receipt_signed_by_member": (
                        "The receipt approver must be different from the treasurer's "
                        "member identity."
                    )
                }
            )
        if not validation_input.signed_receipt_received:
            raise ValidationError(
                {
                    "signed_receipt_received_at": (
                        "A signed receipt is required before final validation."
                    )
                }
            )
        if not validation_input.signature_verified:
            raise ValidationError(
                {
                    "signature_verified_at": (
                        "Signature verification must be explicitly confirmed before "
                        "final validation."
                    )
                }
            )

        approved_amount = validation_input.approved_amount or reimbursement.amount_requested
        if approved_amount <= 0:
            raise ValidationError(
                {"amount_approved": "Approved amount must be greater than zero."}
            )
        if approved_amount > reimbursement.amount_requested:
            raise ValidationError(
                {"amount_approved": "Approved amount cannot exceed the requested amount."}
            )

        active_receipts = reimbursement.receipt_files.filter(archived_at__isnull=True)
        if not active_receipts.exists():
            raise ValidationError(
                {
                    "receipt_files": (
                        "At least one non-archived receipt file is required before "
                        "final validation."
                    )
                }
            )

    def _append_note(self, existing_note: str, new_note: str) -> str:
        """Append a new note without erasing existing reimbursement notes."""

        cleaned_note = new_note.strip()
        if not cleaned_note:
            return existing_note
        if not existing_note.strip():
            return cleaned_note
        return f"{existing_note.rstrip()}\n\n{cleaned_note}"
