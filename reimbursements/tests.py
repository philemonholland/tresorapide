from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from budget.models import BudgetCategory, BudgetYear
from members.models import Apartment, Member, Residency
from reimbursements.models import ReceiptFile, Reimbursement, ReimbursementStatus
from reimbursements.pdf import build_reimbursement_package_context
from reimbursements.services import FinalValidationInput, ReimbursementWorkflowService


class ReimbursementModelTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="treasurer",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
        )
        self.member = Member.objects.create(
            first_name="Taylor",
            last_name="Brooks",
            preferred_name="Tay",
            email="tay@example.com",
        )
        self.apartment = Apartment.objects.create(
            code="A-12",
            street_address="12 Example Street",
        )
        Residency.objects.create(
            member=self.member,
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

    def test_reimbursement_snapshots_do_not_drift_after_related_data_changes(self) -> None:
        reimbursement = Reimbursement.objects.create(
            requested_by_member=self.member,
            apartment=self.apartment,
            budget_year=self.budget_year,
            budget_category=self.budget_category,
            created_by=self.user,
            title="Cleaning supplies",
            expense_date=date(2025, 2, 15),
            amount_requested=Decimal("42.35"),
        )

        self.member.preferred_name = "Changed"
        self.member.last_name = "Member"
        self.member.save()
        self.apartment.code = "NEW-CODE"
        self.apartment.street_address = "99 Updated Avenue"
        self.apartment.save()

        reimbursement.notes = "Reviewed later."
        reimbursement.save()
        reimbursement.refresh_from_db()

        self.assertEqual(reimbursement.member_name_snapshot, "Tay Brooks")
        self.assertEqual(reimbursement.apartment_code_snapshot, "A-12")
        self.assertEqual(
            reimbursement.apartment_display_snapshot,
            "A-12 — 12 Example Street",
        )
        self.assertEqual(reimbursement.residency_start_snapshot, date(2025, 1, 1))
        self.assertIsNone(reimbursement.residency_end_snapshot)

    def test_reimbursement_rejects_budget_category_from_other_year(self) -> None:
        other_year = BudgetYear.objects.create(
            label="FY2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        other_category = BudgetCategory.objects.create(
            budget_year=other_year,
            code="travel",
            name="Travel",
        )

        reimbursement = Reimbursement(
            requested_by_member=self.member,
            apartment=self.apartment,
            budget_year=self.budget_year,
            budget_category=other_category,
            title="Conference bus",
            expense_date=date(2025, 4, 1),
            amount_requested=Decimal("10.00"),
        )

        with self.assertRaises(ValidationError):
            reimbursement.full_clean()

    def test_reimbursement_cannot_be_deleted(self) -> None:
        reimbursement = Reimbursement.objects.create(
            requested_by_member=self.member,
            apartment=self.apartment,
            budget_year=self.budget_year,
            budget_category=self.budget_category,
            title="Paper towels",
            expense_date=date(2025, 2, 16),
            amount_requested=Decimal("9.99"),
        )

        with self.assertRaises(ValidationError):
            reimbursement.delete()

    def test_void_status_requires_reason(self) -> None:
        reimbursement = Reimbursement(
            requested_by_member=self.member,
            apartment=self.apartment,
            budget_year=self.budget_year,
            budget_category=self.budget_category,
            title="Mistake claim",
            expense_date=date(2025, 2, 16),
            amount_requested=Decimal("9.99"),
            status=ReimbursementStatus.VOID,
        )

        with self.assertRaises(ValidationError):
            reimbursement.full_clean()


class ReceiptFileTests(TestCase):
    @override_settings(MEDIA_ROOT="")
    def test_receipt_file_defaults_original_filename(self) -> None:
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                member = Member.objects.create(first_name="Jules", last_name="Ray")
                apartment = Apartment.objects.create(code="R-1")
                Residency.objects.create(
                    member=member,
                    apartment=apartment,
                    start_date=date(2025, 1, 1),
                )
                budget_year = BudgetYear.objects.create(
                    label="FY2025",
                    start_date=date(2025, 1, 1),
                    end_date=date(2025, 12, 31),
                )
                category = BudgetCategory.objects.create(
                    budget_year=budget_year,
                    code="food",
                    name="Food",
                )
                reimbursement = Reimbursement.objects.create(
                    requested_by_member=member,
                    apartment=apartment,
                    budget_year=budget_year,
                    budget_category=category,
                    title="Groceries",
                    expense_date=date(2025, 3, 1),
                    amount_requested=Decimal("24.00"),
                )

                receipt_file = ReceiptFile.objects.create(
                    reimbursement=reimbursement,
                    file=SimpleUploadedFile(
                        "receipt.pdf",
                        b"pdf-data",
                        content_type="application/pdf",
                    ),
                )

                self.assertEqual(receipt_file.original_filename, "receipt.pdf")
                self.assertTrue(
                    Path(receipt_file.file.path).exists(),
                    "Uploaded receipt file should exist in the media directory.",
                )

    def test_receipt_file_cannot_be_deleted(self) -> None:
        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                member = Member.objects.create(first_name="Jules", last_name="Ray")
                apartment = Apartment.objects.create(code="R-2")
                Residency.objects.create(
                    member=member,
                    apartment=apartment,
                    start_date=date(2025, 1, 1),
                )
                budget_year = BudgetYear.objects.create(
                    label="FY2025",
                    start_date=date(2025, 1, 1),
                    end_date=date(2025, 12, 31),
                )
                category = BudgetCategory.objects.create(
                    budget_year=budget_year,
                    code="ops",
                    name="Operations",
                )
                reimbursement = Reimbursement.objects.create(
                    requested_by_member=member,
                    apartment=apartment,
                    budget_year=budget_year,
                    budget_category=category,
                    title="Soap",
                    expense_date=date(2025, 3, 2),
                    amount_requested=Decimal("3.50"),
                )
                receipt_file = ReceiptFile.objects.create(
                    reimbursement=reimbursement,
                    file=SimpleUploadedFile("receipt.txt", b"content"),
                )

                with self.assertRaises(ValidationError):
                    receipt_file.delete()


class ReimbursementTransparencyViewTests(TestCase):
    def setUp(self) -> None:
        self.media_root = TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.media_root.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.media_root.cleanup)

        self.viewer = User.objects.create_user(
            username="transparency-viewer",
            password="StrongPassw0rd!",
            role=User.Role.VIEWER,
        )
        self.treasurer = User.objects.create_user(
            username="transparency-treasurer",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
        )
        self.member = Member.objects.create(first_name="Taylor", last_name="Brooks")
        self.apartment = Apartment.objects.create(code="R-10")
        Residency.objects.create(
            member=self.member,
            apartment=self.apartment,
            start_date=date(2025, 1, 1),
        )
        self.budget_year = BudgetYear.objects.create(
            label="FY2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        self.category = BudgetCategory.objects.create(
            budget_year=self.budget_year,
            code="ops",
            name="Operations",
            planned_amount=Decimal("250.00"),
        )
        self.approved = Reimbursement.objects.create(
            requested_by_member=self.member,
            apartment=self.apartment,
            budget_year=self.budget_year,
            budget_category=self.category,
            created_by=self.viewer,
            title="Approved receipt",
            expense_date=date(2025, 3, 1),
            amount_requested=Decimal("42.00"),
            amount_approved=Decimal("40.00"),
            status=ReimbursementStatus.APPROVED,
        )
        self.approved_receipt = ReceiptFile.objects.create(
            reimbursement=self.approved,
            uploaded_by=self.viewer,
            file=SimpleUploadedFile(
                "approved.pdf",
                b"approved-pdf",
                content_type="application/pdf",
            ),
        )
        self.submitted = Reimbursement.objects.create(
            requested_by_member=self.member,
            apartment=self.apartment,
            budget_year=self.budget_year,
            budget_category=self.category,
            created_by=self.viewer,
            title="Submitted receipt",
            expense_date=date(2025, 3, 2),
            amount_requested=Decimal("18.00"),
            status=ReimbursementStatus.SUBMITTED,
        )
        self.submitted_receipt = ReceiptFile.objects.create(
            reimbursement=self.submitted,
            uploaded_by=self.viewer,
            file=SimpleUploadedFile("submitted.pdf", b"submitted-pdf"),
        )
        self.archived_submitted = Reimbursement.objects.create(
            requested_by_member=self.member,
            apartment=self.apartment,
            budget_year=self.budget_year,
            budget_category=self.category,
            created_by=self.viewer,
            title="Archived submitted receipt",
            expense_date=date(2025, 3, 3),
            amount_requested=Decimal("12.00"),
            status=ReimbursementStatus.SUBMITTED,
        )
        self.archived_submitted.archive(reason="Withdrawn after filing")
        self.archived_submitted.save(
            update_fields=["archived_at", "archive_reason", "updated_at"]
        )

    def test_viewer_transparency_hides_active_submitted_and_shows_archived_history(self) -> None:
        self.client.force_login(self.viewer)

        list_response = self.client.get(reverse("reimbursements:list"))
        archive_response = self.client.get(reverse("reimbursements:archive"))

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.approved.reference_code)
        self.assertNotContains(list_response, self.submitted.reference_code)
        self.assertContains(list_response, self.archived_submitted.reference_code)
        self.assertEqual(archive_response.status_code, 200)
        self.assertContains(archive_response, self.archived_submitted.reference_code)

    def test_viewer_cannot_open_or_download_active_submitted_reimbursement(self) -> None:
        self.client.force_login(self.viewer)

        detail_response = self.client.get(
            reverse("reimbursements:detail", args=[self.submitted.pk])
        )
        download_response = self.client.get(
            reverse("reimbursements:receipt-download", args=[self.submitted_receipt.pk])
        )

        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(download_response.status_code, 404)

    def test_viewer_can_download_visible_receipt(self) -> None:
        self.client.force_login(self.viewer)

        response = self.client.get(
            reverse("reimbursements:receipt-download", args=[self.approved_receipt.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("approved.pdf", response.headers["Content-Disposition"])
        response.close()

    def test_treasurer_can_review_active_submitted_reimbursement_and_views_are_read_only(
        self,
    ) -> None:
        self.client.force_login(self.treasurer)

        detail_response = self.client.get(
            reverse("reimbursements:detail", args=[self.submitted.pk])
        )
        post_response = self.client.post(reverse("reimbursements:list"))

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, self.submitted.reference_code)
        self.assertEqual(post_response.status_code, 405)


class ReimbursementWorkflowViewTests(TestCase):
    def setUp(self) -> None:
        self.media_root = TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.media_root.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.media_root.cleanup)

        self.workflow_service = ReimbursementWorkflowService()
        self.viewer = User.objects.create_user(
            username="workflow-viewer",
            password="StrongPassw0rd!",
            role=User.Role.VIEWER,
        )
        self.treasurer = User.objects.create_user(
            username="workflow-treasurer",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
        )
        self.purchaser = Member.objects.create(
            first_name="Taylor",
            last_name="Brooks",
            preferred_name="Tay",
            email="tay@example.com",
        )
        self.approver = Member.objects.create(
            first_name="Jordan",
            last_name="Miles",
            email="jordan@example.com",
        )
        self.treasurer_member = Member.objects.create(
            first_name="Avery",
            last_name="Stone",
            email="avery@example.com",
        )
        self.apartment = Apartment.objects.create(
            code="T-1",
            street_address="1 Treasurer Lane",
        )
        Residency.objects.create(
            member=self.purchaser,
            apartment=self.apartment,
            start_date=date(2025, 1, 1),
        )
        Residency.objects.create(
            member=self.approver,
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
        self.category = BudgetCategory.objects.create(
            budget_year=self.budget_year,
            code="OPS",
            name="Operations",
            planned_amount=Decimal("500.00"),
        )

    def _create_reimbursement(self, *, status: str = ReimbursementStatus.DRAFT) -> Reimbursement:
        return Reimbursement.objects.create(
            requested_by_member=self.purchaser,
            apartment=self.apartment,
            budget_year=self.budget_year,
            budget_category=self.category,
            created_by=self.treasurer,
            title="Community supplies",
            description="Paint and rollers for shared space.",
            expense_date=date(2025, 3, 12),
            amount_requested=Decimal("85.50"),
            status=status,
        )

    def _upload_receipt(self, reimbursement: Reimbursement, filename: str = "receipt.pdf") -> ReceiptFile:
        return ReceiptFile.objects.create(
            reimbursement=reimbursement,
            uploaded_by=self.treasurer,
            file=SimpleUploadedFile(
                filename,
                b"receipt-binary-data",
                content_type="application/pdf",
            ),
        )

    def _approve_reimbursement(self, reimbursement: Reimbursement) -> Reimbursement:
        self._upload_receipt(reimbursement)
        self.workflow_service.finalize_validation(
            reimbursement,
            actor=self.treasurer,
            validation_input=FinalValidationInput(
                approver_member=self.approver,
                treasurer_member=self.treasurer_member,
                signed_receipt_received=True,
                signature_verified=True,
                approved_amount=Decimal("80.00"),
                note="Approved from tests.",
            ),
        )
        reimbursement.refresh_from_db()
        return reimbursement

    def test_viewer_cannot_access_treasurer_management_routes(self) -> None:
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("reimbursements:create"))

        self.assertEqual(response.status_code, 403)

    def test_treasurer_can_create_and_edit_reimbursement(self) -> None:
        self.client.force_login(self.treasurer)

        create_response = self.client.post(
            reverse("reimbursements:create"),
            {
                "requested_by_member": self.purchaser.pk,
                "apartment": self.apartment.pk,
                "budget_year": self.budget_year.pk,
                "budget_category": self.category.pk,
                "title": "Bulk groceries",
                "description": "House pantry restock.",
                "expense_date": "2025-03-18",
                "amount_requested": "120.45",
                "status": ReimbursementStatus.DRAFT,
                "notes": "Enter manually from paper receipt.",
            },
        )
        reimbursement = Reimbursement.objects.get(title="Bulk groceries")
        update_response = self.client.post(
            reverse("reimbursements:edit", args=[reimbursement.pk]),
            {
                "requested_by_member": self.purchaser.pk,
                "apartment": self.apartment.pk,
                "budget_year": self.budget_year.pk,
                "budget_category": self.category.pk,
                "title": "Bulk groceries",
                "description": "Updated description.",
                "expense_date": "2025-03-18",
                "amount_requested": "125.00",
                "status": ReimbursementStatus.SUBMITTED,
                "notes": "Ready for treasurer review.",
            },
        )
        reimbursement.refresh_from_db()

        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(update_response.status_code, 302)
        self.assertEqual(reimbursement.amount_requested, Decimal("125.00"))
        self.assertEqual(reimbursement.status, ReimbursementStatus.SUBMITTED)

    def test_treasurer_can_complete_basic_reimbursement_flow(self) -> None:
        self.client.force_login(self.treasurer)

        create_response = self.client.post(
            reverse("reimbursements:create"),
            {
                "requested_by_member": self.purchaser.pk,
                "apartment": self.apartment.pk,
                "budget_year": self.budget_year.pk,
                "budget_category": self.category.pk,
                "title": "Shared pantry restock",
                "description": "Staples for the common kitchen.",
                "expense_date": "2025-03-19",
                "amount_requested": "95.00",
                "status": ReimbursementStatus.DRAFT,
                "notes": "Created from paper receipt.",
            },
        )
        reimbursement = Reimbursement.objects.get(title="Shared pantry restock")
        submit_response = self.client.post(
            reverse("reimbursements:edit", args=[reimbursement.pk]),
            {
                "requested_by_member": self.purchaser.pk,
                "apartment": self.apartment.pk,
                "budget_year": self.budget_year.pk,
                "budget_category": self.category.pk,
                "title": "Shared pantry restock",
                "description": "Staples for the common kitchen.",
                "expense_date": "2025-03-19",
                "amount_requested": "95.00",
                "status": ReimbursementStatus.SUBMITTED,
                "notes": "Submitted for approval.",
            },
        )
        upload_response = self.client.post(
            reverse("reimbursements:receipt-upload", args=[reimbursement.pk]),
            {
                "file": SimpleUploadedFile(
                    "basic-flow.pdf",
                    b"basic-flow-receipt",
                    content_type="application/pdf",
                )
            },
        )
        finalize_response = self.client.post(
            reverse("reimbursements:finalize", args=[reimbursement.pk]),
            {
                "approver_member": self.approver.pk,
                "treasurer_member": self.treasurer_member.pk,
                "signed_receipt_received": "on",
                "signature_verified": "on",
                "approved_amount": "90.00",
                "note": "Validated from acceptance flow.",
            },
        )
        detail_response = self.client.get(reverse("reimbursements:detail", args=[reimbursement.pk]))
        reimbursement.refresh_from_db()
        self.category.refresh_from_db()
        self.budget_year.refresh_from_db()

        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(submit_response.status_code, 302)
        self.assertEqual(upload_response.status_code, 302)
        self.assertEqual(finalize_response.status_code, 302)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(reimbursement.status, ReimbursementStatus.APPROVED)
        self.assertEqual(reimbursement.amount_approved, Decimal("90.00"))
        self.assertEqual(reimbursement.receipt_signed_by_member, self.approver)
        self.assertEqual(self.category.approved_reimbursement_total, Decimal("90.00"))
        self.assertEqual(self.budget_year.approved_reimbursement_total, Decimal("90.00"))
        self.assertEqual(reimbursement.receipt_files.filter(archived_at__isnull=True).count(), 1)
        self.assertContains(detail_response, reimbursement.reference_code)
        self.assertContains(detail_response, "Approved")

    def test_treasurer_can_upload_finalize_and_void_reimbursement(self) -> None:
        reimbursement = self._create_reimbursement(status=ReimbursementStatus.SUBMITTED)
        self.client.force_login(self.treasurer)

        upload_response = self.client.post(
            reverse("reimbursements:receipt-upload", args=[reimbursement.pk]),
            {
                "file": SimpleUploadedFile(
                    "workflow.pdf",
                    b"workflow-receipt",
                    content_type="application/pdf",
                )
            },
        )
        finalize_response = self.client.post(
            reverse("reimbursements:finalize", args=[reimbursement.pk]),
            {
                "approver_member": self.approver.pk,
                "treasurer_member": self.treasurer_member.pk,
                "signed_receipt_received": "on",
                "signature_verified": "on",
                "approved_amount": "80.00",
                "note": "Validated from UI.",
            },
        )
        void_response = self.client.post(
            reverse("reimbursements:void", args=[reimbursement.pk]),
            {"reason": "Void after duplicate entry discovered."},
        )
        reimbursement.refresh_from_db()

        self.assertEqual(upload_response.status_code, 302)
        self.assertEqual(finalize_response.status_code, 302)
        self.assertEqual(void_response.status_code, 302)
        self.assertEqual(reimbursement.status, ReimbursementStatus.VOID)
        self.assertEqual(reimbursement.amount_approved, Decimal("80.00"))
        self.assertEqual(reimbursement.void_reason, "Void after duplicate entry discovered.")

    def test_treasurer_can_archive_receipt_from_detail_workflow(self) -> None:
        reimbursement = self._create_reimbursement(status=ReimbursementStatus.SUBMITTED)
        receipt = self._upload_receipt(reimbursement, filename="archive-me.pdf")
        self.client.force_login(self.treasurer)

        response = self.client.post(
            reverse("reimbursements:receipt-archive", args=[receipt.pk]),
            {
                f"receipt-{receipt.pk}-reason": "Superseded by corrected scan.",
            },
        )
        receipt.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(receipt.archived_at)
        self.assertEqual(receipt.archive_reason, "Superseded by corrected scan.")

    def test_pdf_context_uses_snapshot_fields_and_download_route_returns_pdf(self) -> None:
        reimbursement = self._create_reimbursement(status=ReimbursementStatus.SUBMITTED)
        self._approve_reimbursement(reimbursement)
        self.client.force_login(self.treasurer)

        self.purchaser.preferred_name = "Changed"
        self.purchaser.last_name = "Member"
        self.purchaser.save()
        self.apartment.code = "NEW-CODE"
        self.apartment.street_address = "99 Updated Avenue"
        self.apartment.save()

        context = build_reimbursement_package_context(reimbursement)
        response = self.client.get(reverse("reimbursements:pdf-download", args=[reimbursement.pk]))

        self.assertEqual(context["member_name"], "Tay Brooks")
        self.assertEqual(context["apartment_display"], "T-1 — 1 Treasurer Lane")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn(reimbursement.reference_code.lower(), response["Content-Disposition"])
