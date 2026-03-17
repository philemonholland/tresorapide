"""Reimbursement and receipt archive models."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from budget.models import BudgetCategory, BudgetYear
from core.models import ArchivableModel, NonDestructiveModel
from members.models import Apartment, Member, Residency


def generate_reimbursement_reference() -> str:
    """Return a short unique reference suitable for PDFs and archive exports."""
    return f"RB-{uuid4().hex[:10].upper()}"


def receipt_upload_to(instance: ReceiptFile, filename: str) -> str:
    """Build a deterministic upload path for receipt files."""
    safe_name = Path(filename).name
    return f"receipts/{instance.reimbursement.reference_code}/{safe_name}"


class ReimbursementStatus(models.TextChoices):
    """Workflow states for the reimbursement lifecycle."""

    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    REJECTED = "rejected", "Rejected"
    VOID = "void", "Void"


class Reimbursement(ArchivableModel, NonDestructiveModel):
    """A reimbursement claim with immutable snapshot fields for archival output."""

    reference_code = models.CharField(
        max_length=20,
        unique=True,
        default=generate_reimbursement_reference,
        editable=False,
    )
    requested_by_member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name="reimbursements",
    )
    apartment = models.ForeignKey(
        Apartment,
        on_delete=models.PROTECT,
        related_name="reimbursements",
        blank=True,
        null=True,
    )
    budget_year = models.ForeignKey(
        BudgetYear,
        on_delete=models.PROTECT,
        related_name="reimbursements",
    )
    budget_category = models.ForeignKey(
        BudgetCategory,
        on_delete=models.PROTECT,
        related_name="reimbursements",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_reimbursements",
        blank=True,
        null=True,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="approved_reimbursements",
        blank=True,
        null=True,
    )
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="paid_reimbursements",
        blank=True,
        null=True,
    )
    receipt_signed_by_member = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name="signed_reimbursements",
        blank=True,
        null=True,
    )
    signature_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="signature_verified_reimbursements",
        blank=True,
        null=True,
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    expense_date = models.DateField()
    amount_requested = models.DecimalField(max_digits=10, decimal_places=2)
    amount_approved = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=20,
        choices=ReimbursementStatus.choices,
        default=ReimbursementStatus.DRAFT,
    )
    submitted_at = models.DateTimeField(blank=True, null=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    voided_at = models.DateTimeField(blank=True, null=True)
    signed_receipt_received_at = models.DateTimeField(blank=True, null=True)
    signature_verified_at = models.DateTimeField(blank=True, null=True)
    void_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    member_name_snapshot = models.CharField(max_length=255)
    member_email_snapshot = models.EmailField(blank=True)
    apartment_code_snapshot = models.CharField(max_length=50, blank=True)
    apartment_display_snapshot = models.CharField(max_length=255, blank=True)
    residency_start_snapshot = models.DateField(blank=True, null=True)
    residency_end_snapshot = models.DateField(blank=True, null=True)
    budget_year_label_snapshot = models.CharField(max_length=50)
    budget_category_code_snapshot = models.CharField(max_length=50)
    budget_category_name_snapshot = models.CharField(max_length=150)
    receipt_signer_name_snapshot = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-expense_date", "-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "expense_date"]),
            models.Index(fields=["reference_code"]),
        ]

    FINALIZED_SOURCE_LOCK_STATUSES = frozenset(
        {
            ReimbursementStatus.APPROVED,
            ReimbursementStatus.PAID,
            ReimbursementStatus.VOID,
        }
    )

    def clean(self) -> None:
        """Validate reimbursement consistency and archival requirements."""
        super().clean()
        if self.amount_requested <= 0:
            raise ValidationError(
                {"amount_requested": "Requested amount must be greater than zero."}
            )
        if self.amount_approved is not None and self.amount_approved < 0:
            raise ValidationError(
                {"amount_approved": "Approved amount cannot be negative."}
            )
        if (
            self.amount_approved is not None
            and self.amount_approved > self.amount_requested
        ):
            raise ValidationError(
                {
                    "amount_approved": (
                        "Approved amount cannot exceed the requested amount."
                    )
                }
            )
        if self.budget_category.budget_year_id != self.budget_year_id:
            raise ValidationError(
                {
                    "budget_category": (
                        "Budget category must belong to the selected budget year."
                    )
                }
            )
        if self.status == ReimbursementStatus.VOID and not self.void_reason.strip():
            raise ValidationError(
                {"void_reason": "A void reason is required when voiding a reimbursement."}
            )
        if self._finalized_source_fields_changed():
            raise ValidationError(
                "Finalized reimbursements cannot change purchaser, apartment, budget, "
                "or expense date source fields."
            )

    def refresh_snapshot_fields(self) -> None:
        """Capture current relational labels into immutable reporting fields."""
        self.member_name_snapshot = self.requested_by_member.display_name
        self.member_email_snapshot = self.requested_by_member.email
        self.budget_year_label_snapshot = self.budget_year.label
        self.budget_category_code_snapshot = self.budget_category.code
        self.budget_category_name_snapshot = self.budget_category.name

        if self.apartment is None:
            self.apartment_code_snapshot = ""
            self.apartment_display_snapshot = ""
        else:
            self.apartment_code_snapshot = self.apartment.code
            self.apartment_display_snapshot = self.apartment.display_name

        matching_residency = (
            Residency.objects.active_on(self.expense_date)
            .filter(member=self.requested_by_member)
            .filter(apartment=self.apartment if self.apartment_id else None)
            .order_by("-start_date")
            .first()
        )
        if matching_residency is None and self.apartment_id is None:
            matching_residency = (
                Residency.objects.active_on(self.expense_date)
                .filter(member=self.requested_by_member)
                .order_by("-start_date")
                .first()
            )

        if matching_residency is None:
            self.residency_start_snapshot = None
            self.residency_end_snapshot = None
        else:
            self.residency_start_snapshot = matching_residency.start_date
            self.residency_end_snapshot = matching_residency.end_date

    def _source_fields_changed(self) -> bool:
        """Return whether snapshot source foreign keys changed since last save."""
        if self.pk is None:
            return True
        original = (
            Reimbursement.objects.filter(pk=self.pk)
            .values(
                "requested_by_member_id",
                "apartment_id",
                "budget_year_id",
                "budget_category_id",
                "expense_date",
            )
            .first()
        )
        if original is None:
            return True
        return any(
            original[field_name] != getattr(self, field_name)
            for field_name in (
                "requested_by_member_id",
                "apartment_id",
                "budget_year_id",
                "budget_category_id",
                "expense_date",
            )
        )

    def _finalized_source_fields_changed(self) -> bool:
        """Return whether a persisted finalized record changed snapshot source fields."""

        if self.pk is None:
            return False
        original = (
            Reimbursement.objects.filter(pk=self.pk)
            .values(
                "status",
                "requested_by_member_id",
                "apartment_id",
                "budget_year_id",
                "budget_category_id",
                "expense_date",
            )
            .first()
        )
        if original is None:
            return False
        if original["status"] not in self.FINALIZED_SOURCE_LOCK_STATUSES:
            return False
        return any(
            original[field_name] != getattr(self, field_name)
            for field_name in (
                "requested_by_member_id",
                "apartment_id",
                "budget_year_id",
                "budget_category_id",
                "expense_date",
            )
        )

    def save(self, *args: object, **kwargs: object) -> None:
        """Validate the record and update snapshots when source relations change."""
        if self._source_fields_changed():
            self.refresh_snapshot_fields()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return a stable label for admin and archive displays."""
        return f"{self.reference_code} · {self.member_name_snapshot} · {self.amount_requested}"


class ReceiptFileOcrStatus(models.TextChoices):
    """OCR processing states for future automation work."""

    NOT_REQUESTED = "not_requested", "Not requested"
    PENDING = "pending", "Pending"
    EXTRACTED = "extracted", "Extracted"
    CORRECTED = "corrected", "Corrected"
    FAILED = "failed", "Failed"


class ReceiptFile(ArchivableModel, NonDestructiveModel):
    """A file attached to a reimbursement, with room for future OCR metadata."""

    reimbursement = models.ForeignKey(
        Reimbursement,
        on_delete=models.PROTECT,
        related_name="receipt_files",
    )
    file = models.FileField(upload_to=receipt_upload_to, max_length=255)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    file_size_bytes = models.PositiveBigIntegerField(blank=True, null=True)
    sha256_checksum = models.CharField(max_length=64, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="uploaded_receipt_files",
        blank=True,
        null=True,
    )
    ocr_status = models.CharField(
        max_length=20,
        choices=ReceiptFileOcrStatus.choices,
        default=ReceiptFileOcrStatus.NOT_REQUESTED,
    )
    ocr_raw_text = models.TextField(blank=True)
    ocr_corrected_fields = models.JSONField(blank=True, null=True)

    class Meta:
        ordering = ["created_at", "id"]

    def _calculate_sha256_checksum(self) -> str:
        """Return a SHA256 checksum for the current file contents."""

        if not self.file:
            return ""
        digest = hashlib.sha256()
        self.file.open("rb")
        file_object = self.file.file
        original_position = file_object.tell()
        file_object.seek(0)
        for chunk in self.file.chunks():
            digest.update(chunk)
        file_object.seek(original_position)
        return digest.hexdigest()

    def save(self, *args: object, **kwargs: object) -> None:
        """Capture basic file metadata before persisting."""
        if not self.original_filename and self.file:
            self.original_filename = Path(self.file.name).name
        if self.file:
            self.file_size_bytes = self.file.size
            uploaded_file = getattr(self.file, "file", None)
            content_type = getattr(uploaded_file, "content_type", "")
            if content_type:
                self.content_type = content_type
            self.sha256_checksum = self._calculate_sha256_checksum()
        self.full_clean()
        super().save(*args, **kwargs)
        if self.file:
            self.file.close()

    def __str__(self) -> str:
        """Return a receipt label for admin usage."""
        return f"{self.reimbursement.reference_code} · {self.original_filename}"

# Create your models here.
