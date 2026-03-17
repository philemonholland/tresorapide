from __future__ import annotations

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from audits.models import AuditLogEntry


class AuditLogEntryTests(TestCase):
    def test_audit_log_snapshots_actor_role(self) -> None:
        actor = User.objects.create_user(
            username="auditor",
            password="StrongPassw0rd!",
            role=User.Role.ADMIN,
        )

        entry = AuditLogEntry.objects.create(
            actor=actor,
            action="reimbursement.approved",
            target_app_label="reimbursements",
            target_model="reimbursement",
            target_object_id="123",
            summary="Approved reimbursement RB-123",
        )

        self.assertEqual(entry.actor_role_snapshot, User.Role.ADMIN)

    def test_audit_log_cannot_be_deleted(self) -> None:
        entry = AuditLogEntry.objects.create(
            action="member.updated",
            target_app_label="members",
            target_model="member",
            target_object_id="12",
            summary="Updated member profile",
        )

        with self.assertRaises(ValidationError):
            entry.delete()


class AuditViewTests(TestCase):
    def setUp(self) -> None:
        self.viewer = User.objects.create_user(
            username="audit-viewer",
            password="StrongPassw0rd!",
            role=User.Role.VIEWER,
        )
        self.treasurer = User.objects.create_user(
            username="audit-treasurer",
            password="StrongPassw0rd!",
            role=User.Role.TREASURER,
        )
        self.entry = AuditLogEntry.objects.create(
            actor=self.treasurer,
            action="reimbursement.voided",
            target_app_label="reimbursements",
            target_model="reimbursement",
            target_object_id="RB-123",
            summary="Voided reimbursement RB-123",
            payload={"reason": "Duplicate"},
        )

    def test_audit_log_requires_treasurer_role(self) -> None:
        self.client.force_login(self.viewer)

        forbidden_response = self.client.get(reverse("audits:list"))

        self.assertEqual(forbidden_response.status_code, 403)

        self.client.force_login(self.treasurer)
        allowed_response = self.client.get(reverse("audits:list"))

        self.assertEqual(allowed_response.status_code, 200)
        self.assertContains(allowed_response, self.entry.summary)

    def test_audit_detail_is_read_only(self) -> None:
        self.client.force_login(self.treasurer)

        response = self.client.get(reverse("audits:detail", args=[self.entry.pk]))
        post_response = self.client.post(reverse("audits:detail", args=[self.entry.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Duplicate")
        self.assertEqual(post_response.status_code, 405)
