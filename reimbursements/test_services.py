from __future__ import annotations

from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import User
from audits.models import AuditLogEntry
from budget.models import BudgetCategory, BudgetYear
from members.models import Apartment, Member, Residency
from reimbursements.models import ReceiptFile, Reimbursement, ReimbursementStatus
from reimbursements.services import FinalValidationInput, ReimbursementWorkflowService


class ReimbursementWorkflowServiceTests(TestCase):
    def setUp(self) -> None:
        self.workflow_service = ReimbursementWorkflowService()
        self.treasurer_user = User.objects.create_user(
            username="treasurer",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
        )
        self.viewer_user = User.objects.create_user(
            username="viewer",
            password="StrongPassw0rd!",
            role=User.Role.VIEWER,
        )
        self.purchaser_member = Member.objects.create(
            first_name="Taylor",
            last_name="Brooks",
            preferred_name="Tay",
            email="tay@example.com",
        )
        self.approver_member = Member.objects.create(
            first_name="Jordan",
            last_name="Miles",
            preferred_name="Jo",
            email="jo@example.com",
        )
        self.treasurer_member = Member.objects.create(
            first_name="Avery",
            last_name="Stone",
            email="avery@example.com",
        )
        self.apartment = Apartment.objects.create(
            code="A-12",
            street_address="12 Example Street",
        )
        Residency.objects.create(
            member=self.purchaser_member,
            apartment=self.apartment,
            start_date=date(2025, 1, 1),
        )
        Residency.objects.create(
            member=self.approver_member,
            apartment=self.apartment,
            start_date=date(2025, 1, 1),
        )
        Residency.objects.create(
            member=self.treasurer_member,
            apartment=self.apartment,
            start_date=date(2025, 1, 1),
        )
        self.budget_year = BudgetYear.objects.create(
            label="FY2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        self.budget_category = BudgetCategory.objects.create(
            budget_year=self.budget_year,
            code="house",
            name="House Supplies",
        )

    def _create_submitted_reimbursement(self) -> Reimbursement:
        return Reimbursement.objects.create(
            requested_by_member=self.purchaser_member,
            apartment=self.apartment,
            budget_year=self.budget_year,
            budget_category=self.budget_category,
            created_by=self.viewer_user,
            title="Cleaning supplies",
            expense_date=date(2025, 2, 15),
            amount_requested=Decimal("42.35"),
            status=ReimbursementStatus.SUBMITTED,
        )

    def _attach_receipt(self, reimbursement: Reimbursement, filename: str = "receipt.pdf") -> ReceiptFile:
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                receipt_file = ReceiptFile.objects.create(
                    reimbursement=reimbursement,
                    uploaded_by=self.viewer_user,
                    file=SimpleUploadedFile(
                        filename,
                        b"signed-receipt",
                        content_type="application/pdf",
                    ),
                )
                receipt_file.refresh_from_db()
                return receipt_file

    @override_settings(MEDIA_ROOT="")
    def test_finalize_validation_approves_reimbursement_updates_budget_and_logs_audit(
        self,
    ) -> None:
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                reimbursement = self._create_submitted_reimbursement()
                receipt_file = ReceiptFile.objects.create(
                    reimbursement=reimbursement,
                    uploaded_by=self.viewer_user,
                    file=SimpleUploadedFile(
                        "receipt.pdf",
                        b"signed-receipt",
                        content_type="application/pdf",
                    ),
                )

                self.workflow_service.finalize_validation(
                    reimbursement,
                    actor=self.treasurer_user,
                    validation_input=FinalValidationInput(
                        approver_member=self.approver_member,
                        treasurer_member=self.treasurer_member,
                        signed_receipt_received=True,
                        signature_verified=True,
                        approved_amount=Decimal("40.00"),
                        note="Validated against signed paper receipt.",
                    ),
                )

                reimbursement.refresh_from_db()
                self.budget_category.refresh_from_db()
                self.budget_year.refresh_from_db()
                receipt_file.refresh_from_db()

                self.assertEqual(reimbursement.status, ReimbursementStatus.APPROVED)
                self.assertEqual(reimbursement.amount_approved, Decimal("40.00"))
                self.assertEqual(reimbursement.approved_by, self.treasurer_user)
                self.assertEqual(
                    reimbursement.receipt_signed_by_member,
                    self.approver_member,
                )
                self.assertEqual(
                    reimbursement.receipt_signer_name_snapshot,
                    self.approver_member.display_name,
                )
                self.assertIsNotNone(reimbursement.signed_receipt_received_at)
                self.assertIsNotNone(reimbursement.signature_verified_at)
                self.assertEqual(reimbursement.signature_verified_by, self.treasurer_user)
                self.assertEqual(
                    self.budget_category.approved_reimbursement_total,
                    Decimal("40.00"),
                )
                self.assertEqual(
                    self.budget_year.approved_reimbursement_total,
                    Decimal("40.00"),
                )
                self.assertEqual(receipt_file.original_filename, "receipt.pdf")
                self.assertEqual(receipt_file.file_size_bytes, len(b"signed-receipt"))
                self.assertTrue(receipt_file.sha256_checksum)

                audit_entry = AuditLogEntry.objects.get(action="reimbursement.validated")
                self.assertEqual(audit_entry.actor, self.treasurer_user)
                self.assertEqual(
                    audit_entry.payload["receipt_signer_name_snapshot"],
                    self.approver_member.display_name,
                )
                self.assertEqual(audit_entry.payload["receipt_file_ids"], [receipt_file.id])

    @override_settings(MEDIA_ROOT="")
    def test_finalize_validation_preserves_historical_snapshots_on_reloaded_records(
        self,
    ) -> None:
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                reimbursement = self._create_submitted_reimbursement()
                original_member_name = reimbursement.member_name_snapshot
                original_apartment_code = reimbursement.apartment_code_snapshot
                original_apartment_display = reimbursement.apartment_display_snapshot
                original_budget_year_label = reimbursement.budget_year_label_snapshot
                original_budget_category_code = reimbursement.budget_category_code_snapshot
                original_budget_category_name = reimbursement.budget_category_name_snapshot
                original_residency_start = reimbursement.residency_start_snapshot

                self.purchaser_member.preferred_name = "Changed"
                self.purchaser_member.last_name = "Member"
                self.purchaser_member.save()
                self.apartment.code = "B-99"
                self.apartment.street_address = "99 Updated Avenue"
                self.apartment.save()
                self.budget_year.label = "FY2025 Revised"
                self.budget_year.save()
                self.budget_category.code = "clean"
                self.budget_category.name = "Cleaning"
                self.budget_category.save()

                receipt_file = ReceiptFile.objects.create(
                    reimbursement=reimbursement,
                    uploaded_by=self.viewer_user,
                    file=SimpleUploadedFile(
                        "receipt.pdf",
                        b"signed-receipt",
                        content_type="application/pdf",
                    ),
                )

                self.workflow_service.finalize_validation(
                    reimbursement,
                    actor=self.treasurer_user,
                    validation_input=FinalValidationInput(
                        approver_member=self.approver_member,
                        treasurer_member=self.treasurer_member,
                        signed_receipt_received=True,
                        signature_verified=True,
                        approved_amount=Decimal("42.35"),
                        note="Approved after unrelated member updates.",
                    ),
                )

                reloaded_reimbursement = Reimbursement.objects.get(pk=reimbursement.pk)
                reloaded_receipt = ReceiptFile.objects.get(pk=receipt_file.pk)
                reloaded_category = BudgetCategory.objects.get(pk=self.budget_category.pk)
                reloaded_year = BudgetYear.objects.get(pk=self.budget_year.pk)
                audit_entry = AuditLogEntry.objects.get(action="reimbursement.validated")

                self.assertEqual(
                    reloaded_reimbursement.member_name_snapshot,
                    original_member_name,
                )
                self.assertEqual(
                    reloaded_reimbursement.apartment_code_snapshot,
                    original_apartment_code,
                )
                self.assertEqual(
                    reloaded_reimbursement.apartment_display_snapshot,
                    original_apartment_display,
                )
                self.assertEqual(
                    reloaded_reimbursement.budget_year_label_snapshot,
                    original_budget_year_label,
                )
                self.assertEqual(
                    reloaded_reimbursement.budget_category_code_snapshot,
                    original_budget_category_code,
                )
                self.assertEqual(
                    reloaded_reimbursement.budget_category_name_snapshot,
                    original_budget_category_name,
                )
                self.assertEqual(
                    reloaded_reimbursement.residency_start_snapshot,
                    original_residency_start,
                )
                self.assertEqual(reloaded_reimbursement.status, ReimbursementStatus.APPROVED)
                self.assertEqual(
                    reloaded_category.approved_reimbursement_total,
                    Decimal("42.35"),
                )
                self.assertEqual(
                    reloaded_year.approved_reimbursement_total,
                    Decimal("42.35"),
                )
                self.assertEqual(reloaded_receipt.original_filename, "receipt.pdf")
                self.assertTrue(reloaded_receipt.sha256_checksum)
                self.assertEqual(
                    audit_entry.payload["receipt_signer_name_snapshot"],
                    self.approver_member.display_name,
                )

    def test_finalize_validation_rejects_same_member_as_purchaser_and_approver(self) -> None:
        reimbursement = self._create_submitted_reimbursement()

        with self.assertRaises(ValidationError):
            self.workflow_service.finalize_validation(
                reimbursement,
                actor=self.treasurer_user,
                validation_input=FinalValidationInput(
                    approver_member=self.purchaser_member,
                    signed_receipt_received=True,
                    signature_verified=True,
                ),
            )

    def test_finalize_validation_rejects_treasurer_member_as_receipt_approver(self) -> None:
        reimbursement = self._create_submitted_reimbursement()

        with self.assertRaises(ValidationError):
            self.workflow_service.finalize_validation(
                reimbursement,
                actor=self.treasurer_user,
                validation_input=FinalValidationInput(
                    approver_member=self.treasurer_member,
                    treasurer_member=self.treasurer_member,
                    signed_receipt_received=True,
                    signature_verified=True,
                ),
            )

    def test_finalize_validation_requires_signed_receipt_and_signature_confirmation(
        self,
    ) -> None:
        reimbursement = self._create_submitted_reimbursement()

        with self.assertRaises(ValidationError):
            self.workflow_service.finalize_validation(
                reimbursement,
                actor=self.treasurer_user,
                validation_input=FinalValidationInput(
                    approver_member=self.approver_member,
                    signed_receipt_received=False,
                    signature_verified=False,
                ),
            )

    @override_settings(MEDIA_ROOT="")
    def test_finalize_validation_requires_non_archived_receipt_file(self) -> None:
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                reimbursement = self._create_submitted_reimbursement()
                receipt_file = ReceiptFile.objects.create(
                    reimbursement=reimbursement,
                    uploaded_by=self.viewer_user,
                    file=SimpleUploadedFile("receipt.pdf", b"signed-receipt"),
                )
                receipt_file.archive(reason="Superseded by corrected upload.")
                receipt_file.save(update_fields=["archived_at", "archive_reason", "updated_at"])

                with self.assertRaises(ValidationError):
                    self.workflow_service.finalize_validation(
                        reimbursement,
                        actor=self.treasurer_user,
                        validation_input=FinalValidationInput(
                            approver_member=self.approver_member,
                            signed_receipt_received=True,
                            signature_verified=True,
                        ),
                    )

    @override_settings(MEDIA_ROOT="")
    def test_void_reimbursement_reverses_budget_rollups_and_logs_audit(self) -> None:
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                reimbursement = self._create_submitted_reimbursement()
                ReceiptFile.objects.create(
                    reimbursement=reimbursement,
                    uploaded_by=self.viewer_user,
                    file=SimpleUploadedFile("receipt.pdf", b"signed-receipt"),
                )
                self.workflow_service.finalize_validation(
                    reimbursement,
                    actor=self.treasurer_user,
                    validation_input=FinalValidationInput(
                        approver_member=self.approver_member,
                        treasurer_member=self.treasurer_member,
                        signed_receipt_received=True,
                        signature_verified=True,
                        approved_amount=Decimal("35.00"),
                    ),
                )

                self.workflow_service.void_reimbursement(
                    reimbursement,
                    actor=self.treasurer_user,
                    reason="Duplicate submission",
                )

                reimbursement.refresh_from_db()
                self.budget_category.refresh_from_db()
                self.budget_year.refresh_from_db()

                self.assertEqual(reimbursement.status, ReimbursementStatus.VOID)
                self.assertEqual(reimbursement.void_reason, "Duplicate submission")
                self.assertEqual(
                    self.budget_category.approved_reimbursement_total,
                    Decimal("0.00"),
                )
                self.assertEqual(
                    self.budget_year.approved_reimbursement_total,
                    Decimal("0.00"),
                )
                self.assertTrue(
                    AuditLogEntry.objects.filter(action="reimbursement.voided").exists()
                )

    @override_settings(MEDIA_ROOT="")
    def test_finalized_reimbursement_rejects_source_field_changes(self) -> None:
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                reimbursement = self._create_submitted_reimbursement()
                ReceiptFile.objects.create(
                    reimbursement=reimbursement,
                    uploaded_by=self.viewer_user,
                    file=SimpleUploadedFile("receipt.pdf", b"signed-receipt"),
                )
                self.workflow_service.finalize_validation(
                    reimbursement,
                    actor=self.treasurer_user,
                    validation_input=FinalValidationInput(
                        approver_member=self.approver_member,
                        treasurer_member=self.treasurer_member,
                        signed_receipt_received=True,
                        signature_verified=True,
                    ),
                )
                other_apartment = Apartment.objects.create(code="B-24")

                reimbursement.apartment = other_apartment

                with self.assertRaises(ValidationError):
                    reimbursement.save()

                reimbursement.refresh_from_db()
                reimbursement.budget_category = BudgetCategory.objects.create(
                    budget_year=self.budget_year,
                    code="ops",
                    name="Operations",
                )

                with self.assertRaises(ValidationError):
                    reimbursement.save()

    @override_settings(MEDIA_ROOT="")
    def test_archive_receipt_rejects_last_active_receipt_for_finalized_reimbursement(
        self,
    ) -> None:
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                reimbursement = self._create_submitted_reimbursement()
                receipt_file = ReceiptFile.objects.create(
                    reimbursement=reimbursement,
                    uploaded_by=self.viewer_user,
                    file=SimpleUploadedFile("receipt.pdf", b"signed-receipt"),
                )
                self.workflow_service.finalize_validation(
                    reimbursement,
                    actor=self.treasurer_user,
                    validation_input=FinalValidationInput(
                        approver_member=self.approver_member,
                        treasurer_member=self.treasurer_member,
                        signed_receipt_received=True,
                        signature_verified=True,
                    ),
                )

                with self.assertRaises(ValidationError):
                    self.workflow_service.archive_receipt(
                        receipt_file,
                        actor=self.treasurer_user,
                        reason="Attempted cleanup",
                    )
