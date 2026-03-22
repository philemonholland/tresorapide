from django.db import models
from django.core.exceptions import ValidationError
from core.models import TimeStampedModel, ArchivableModel, NonDestructiveModel


class BonStatus(models.TextChoices):
    DRAFT = "DRAFT", "Brouillon"
    OCR_PENDING = "OCR_PENDING", "OCR en cours"
    READY_FOR_REVIEW = "READY_FOR_REVIEW", "Prêt pour révision"
    READY_FOR_VALIDATION = "READY_FOR_VALIDATION", "Prêt pour validation"
    VALIDATED = "VALIDATED", "Validé"
    EXPORTED_PDF = "EXPORTED_PDF", "PDF exporté"
    EMAILED = "EMAILED", "Envoyé au comptable"
    REIMBURSED = "REIMBURSED", "Remboursé"
    VOID = "VOID", "Annulé"


class OcrStatus(models.TextChoices):
    NOT_REQUESTED = "NOT_REQUESTED", "Non demandé"
    PENDING = "PENDING", "En attente"
    EXTRACTED = "EXTRACTED", "Extrait"
    CORRECTED = "CORRECTED", "Corrigé"
    FAILED = "FAILED", "Échoué"


def receipt_upload_to(instance, filename):
    bon = instance.bon_de_commande
    safe_name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return f"receipts/{bon.budget_year.year}/{bon.number}/{safe_name}"


class Merchant(models.Model):
    """Known merchant/vendor. Prevents duplicates across bons."""
    name = models.CharField("Nom du marchand", max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Marchand"
        verbose_name_plural = "Marchands"
        ordering = ["name"]

    def __str__(self):
        return self.name


class BonDeCommande(TimeStampedModel, NonDestructiveModel):
    """
    Purchase order document. Central entity for the treasurer's workflow.
    Each bon aggregates receipts/invoices and goes through an approval workflow.
    """
    house = models.ForeignKey(
        "houses.House", on_delete=models.PROTECT, related_name="bons_de_commande"
    )
    budget_year = models.ForeignKey(
        "budget.BudgetYear", on_delete=models.PROTECT, related_name="bons_de_commande"
    )
    number = models.CharField(
        max_length=20, unique=True,
        help_text="Format HHYYNNNN (digital) ou numéro papier (ex : 16011)"
    )
    purchase_date = models.DateField()
    entered_date = models.DateField(auto_now_add=True)
    short_description = models.CharField(max_length=255)
    merchant_name = models.CharField(max_length=200, blank=True)
    supplier_name = models.CharField(max_length=200, blank=True)
    work_or_delivery_location = models.CharField(max_length=255, blank=True)
    claimant_address = models.CharField(max_length=255, blank=True)
    claimant_phone = models.CharField(max_length=50, blank=True)

    sub_budget = models.ForeignKey(
        "budget.SubBudget", on_delete=models.PROTECT, related_name="bons_de_commande"
    )
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tps = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Taxe fédérale (TPS)"
    )
    tvq = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Taxe provinciale (TVQ)"
    )
    total = models.DecimalField(max_digits=10, decimal_places=2)

    # People involved
    purchaser_member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT,
        related_name="purchased_bons"
    )
    purchaser_apartment = models.ForeignKey(
        "members.Apartment", on_delete=models.PROTECT,
        null=True, blank=True, related_name="purchaser_bons"
    )
    approver_member = models.ForeignKey(
        "members.Member", on_delete=models.PROTECT,
        null=True, blank=True, related_name="approved_bons"
    )
    approver_apartment = models.ForeignKey(
        "members.Apartment", on_delete=models.PROTECT,
        null=True, blank=True, related_name="approver_bons"
    )
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="created_bons"
    )
    validated_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="validated_bons"
    )
    signature_verified = models.BooleanField(default=False)
    signature_verified_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="signature_verified_bons"
    )

    status = models.CharField(
        max_length=25, choices=BonStatus.choices, default=BonStatus.DRAFT
    )

    # Snapshot fields — frozen at validation time
    purchaser_name_snapshot = models.CharField(max_length=255, blank=True)
    purchaser_unit_snapshot = models.CharField(max_length=50, blank=True)
    purchaser_phone_snapshot = models.CharField(max_length=50, blank=True)
    approver_name_snapshot = models.CharField(max_length=255, blank=True)
    approver_unit_snapshot = models.CharField(max_length=50, blank=True)
    budget_year_label_snapshot = models.CharField(max_length=50, blank=True)
    sub_budget_name_snapshot = models.CharField(max_length=150, blank=True)

    # Timestamps
    submitted_at = models.DateTimeField(blank=True, null=True)
    validated_at = models.DateTimeField(blank=True, null=True)
    exported_at = models.DateTimeField(blank=True, null=True)
    emailed_at = models.DateTimeField(blank=True, null=True)
    reimbursed_at = models.DateTimeField(blank=True, null=True)
    voided_at = models.DateTimeField(blank=True, null=True)
    void_reason = models.TextField(blank=True)
    historical_member_unmatched = models.BooleanField(default=False)
    is_scan_session = models.BooleanField(
        default=False,
        help_text="True for temporary upload containers (hidden from normal views)"
    )
    is_paper_bc = models.BooleanField(
        default=False,
        help_text="True if this bon was digitized from a paper bon de commande"
    )
    paper_bc_number = models.CharField(
        max_length=20, blank=True,
        help_text="Original number from the paper bon de commande (e.g., 16011)"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-purchase_date", "-created_at", "-id"]
        verbose_name = "bon de commande"
        verbose_name_plural = "bons de commande"
        indexes = [
            models.Index(fields=["status", "purchase_date"]),
            models.Index(fields=["number"]),
            models.Index(fields=["house", "budget_year"]),
        ]

    def clean(self):
        if self.total is not None and self.total < 0:
            raise ValidationError("Le total ne peut pas être négatif.")
        if self.sub_budget and self.budget_year:
            if self.sub_budget.budget_year_id != self.budget_year_id:
                raise ValidationError("Le sous-budget doit appartenir à la même année budgétaire.")
        if self.approver_member and self.purchaser_member:
            if self.approver_member_id == self.purchaser_member_id:
                raise ValidationError("L'approbateur ne peut pas être la même personne que l'acheteur.")

    def refresh_snapshot_fields(self):
        """Capture current relational data into snapshot fields."""
        if self.purchaser_member:
            self.purchaser_name_snapshot = self.purchaser_member.display_name
            apt = self.purchaser_apartment
            self.purchaser_unit_snapshot = apt.code if apt else ""
            self.purchaser_phone_snapshot = self.purchaser_member.phone_number or ""
        if self.approver_member:
            self.approver_name_snapshot = self.approver_member.display_name
            apt = self.approver_apartment
            self.approver_unit_snapshot = apt.code if apt else ""
        else:
            self.approver_name_snapshot = ""
            self.approver_unit_snapshot = ""
        if self.budget_year:
            self.budget_year_label_snapshot = self.budget_year.label
        if self.sub_budget:
            self.sub_budget_name_snapshot = self.sub_budget.name

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"BC {self.number} · {self.purchaser_name_snapshot or '?'} · ${self.total}"

    @property
    def receipt_files_confirmed_count(self):
        """Count of receipts with confirmed extracted fields."""
        return self.receipt_files.filter(
            ocr_status__in=["CORRECTED"],
        ).count()

    @staticmethod
    def format_person_label(
        member=None,
        apartment=None,
        *,
        fallback_name: str = "",
        fallback_apartment: str = "",
    ) -> str:
        """Return a consistent apartment/name label for display."""
        name = member.display_name if member else (fallback_name or "").strip()
        apartment_code = apartment.code if apartment else (fallback_apartment or "").strip()
        if apartment_code and name:
            return f"{apartment_code} / {name}"
        return apartment_code or name or "—"

    @property
    def purchaser_display_label(self) -> str:
        """Display purchaser as 'apt / name' when possible."""
        return self.format_person_label(
            self.purchaser_member,
            self.purchaser_apartment,
            fallback_name=self.purchaser_name_snapshot,
            fallback_apartment=self.purchaser_unit_snapshot,
        )

    @property
    def approver_display_label(self) -> str:
        """Display explicit approver if one is stored on the bon."""
        return self.format_person_label(
            self.approver_member,
            self.approver_apartment,
            fallback_name=self.approver_name_snapshot,
            fallback_apartment=self.approver_unit_snapshot,
        )

    @property
    def validating_treasurer_display_label(self) -> str:
        """Display the validating treasurer, using apartment/member when available."""
        if not self.validated_by:
            return "—"
        member = getattr(self.validated_by, "member", None)
        apartment = member.current_apartment() if member else None
        fallback_name = (
            self.validated_by.get_full_name()
            or getattr(self.validated_by, "username", "")
        )
        return self.format_person_label(
            member,
            apartment,
            fallback_name=fallback_name,
        )

    @property
    def effective_validator_display_label(self) -> str:
        """Display approver, or the validating treasurer when no second signer exists."""
        explicit_label = self.approver_display_label
        return explicit_label if explicit_label != "—" else self.validating_treasurer_display_label

    @property
    def signer_roles_ambiguous(self) -> bool:
        """Whether any linked paper BC extraction flagged purchaser/validator ambiguity."""
        return self.receipt_files.filter(
            extracted_fields__final_document_type="paper_bc",
            extracted_fields__signer_roles_ambiguous_final=True,
        ).exists() or self.receipt_files.filter(
            extracted_fields__document_type_candidate="paper_bc",
            extracted_fields__signer_roles_ambiguous_candidate=True,
        ).exists()


class ReceiptFile(ArchivableModel, NonDestructiveModel):
    """An uploaded receipt or invoice file attached to a bon de commande."""
    bon_de_commande = models.ForeignKey(
        BonDeCommande, on_delete=models.PROTECT, related_name="receipt_files"
    )
    file = models.FileField(upload_to=receipt_upload_to, max_length=255)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    file_size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    sha256_checksum = models.CharField(max_length=64, blank=True)
    uploaded_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="uploaded_receipts"
    )
    ocr_status = models.CharField(
        max_length=20, choices=OcrStatus.choices, default=OcrStatus.NOT_REQUESTED
    )
    ocr_raw_text = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def _calculate_sha256_checksum(self):
        import hashlib
        sha256 = hashlib.sha256()
        self.file.seek(0)
        for chunk in iter(lambda: self.file.read(8192), b""):
            sha256.update(chunk)
        self.file.seek(0)
        return sha256.hexdigest()

    def save(self, *args, **kwargs):
        if self.file and not self.original_filename:
            self.original_filename = self.file.name
        if self.file:
            try:
                self.file_size_bytes = self.file.size
            except (OSError, AttributeError):
                pass
            if not self.sha256_checksum:
                try:
                    self.sha256_checksum = self._calculate_sha256_checksum()
                except (OSError, AttributeError):
                    pass
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.bon_de_commande.number} · {self.original_filename}"


class ReceiptOcrResult(TimeStampedModel):
    """Raw OCR output from processing a receipt file."""
    receipt_file = models.ForeignKey(
        ReceiptFile, on_delete=models.PROTECT, related_name="ocr_results"
    )
    engine_name = models.CharField(
        max_length=50, help_text="ex : 'paddleocr', 'tesseract'"
    )
    engine_version = models.CharField(max_length=50, blank=True)
    raw_text = models.TextField()
    raw_json = models.JSONField(null=True, blank=True)
    confidence_overall = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-processed_at"]

    def __str__(self):
        return f"OCR ({self.engine_name}) for {self.receipt_file}"


class ReceiptExtractedFields(TimeStampedModel):
    """
    Parsed candidate fields from OCR, plus treasurer-confirmed final values.
    One-to-one with ReceiptFile.
    """
    receipt_file = models.OneToOneField(
        ReceiptFile, on_delete=models.PROTECT, related_name="extracted_fields"
    )
    # AI candidate values
    document_type_candidate = models.CharField(
        max_length=20, blank=True,
        help_text="paper_bc, invoice, or receipt"
    )
    bc_number_candidate = models.CharField(
        max_length=20, blank=True,
        help_text="BC number from paper bon de commande"
    )
    associated_bc_number_candidate = models.CharField(
        max_length=20, blank=True,
        help_text="BC number this invoice is associated with"
    )
    supplier_name_candidate = models.CharField(max_length=200, blank=True)
    supplier_address_candidate = models.CharField(max_length=300, blank=True)
    expense_member_name_candidate = models.CharField(
        max_length=200, blank=True,
        help_text="Nom de la personne ayant effectué la dépense (signataire du BC papier)"
    )
    expense_apartment_candidate = models.CharField(
        max_length=10, blank=True,
        help_text="Appartement de la personne ayant effectué la dépense"
    )
    validator_member_name_candidate = models.CharField(
        max_length=200, blank=True,
        help_text="Nom du 2e signataire qui a validé le BC papier"
    )
    validator_apartment_candidate = models.CharField(
        max_length=10, blank=True,
        help_text="Appartement du 2e signataire"
    )
    signer_roles_ambiguous_candidate = models.BooleanField(
        default=False,
        help_text="True si les deux signatures sont ambiguës et doivent être validées manuellement",
    )
    member_name_candidate = models.CharField(max_length=200, blank=True)
    apartment_number_candidate = models.CharField(max_length=10, blank=True)
    merchant_candidate = models.CharField(max_length=200, blank=True)
    merchant_address_candidate = models.CharField(max_length=300, blank=True)
    purchase_date_candidate = models.DateField(null=True, blank=True)
    subtotal_candidate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tps_candidate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tvq_candidate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_candidate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # Treasurer-confirmed final values
    final_document_type = models.CharField(max_length=20, blank=True)
    final_bc_number = models.CharField(max_length=20, blank=True)
    final_associated_bc_number = models.CharField(max_length=20, blank=True)
    final_supplier_name = models.CharField(max_length=200, blank=True)
    final_supplier_address = models.CharField(max_length=300, blank=True)
    final_expense_member_name = models.CharField(max_length=200, blank=True)
    final_expense_apartment = models.CharField(max_length=10, blank=True)
    final_validator_member_name = models.CharField(max_length=200, blank=True)
    final_validator_apartment = models.CharField(max_length=10, blank=True)
    signer_roles_ambiguous_final = models.BooleanField(default=False)
    final_member_name = models.CharField(max_length=200, blank=True)
    final_apartment_number = models.CharField(max_length=10, blank=True)
    final_merchant = models.CharField(max_length=200, blank=True)
    final_merchant_address = models.CharField(max_length=300, blank=True)
    final_purchase_date = models.DateField(null=True, blank=True)
    final_subtotal = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    final_tps = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    final_tvq = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    final_total = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # Summary
    summary_candidate = models.CharField(max_length=255, blank=True)
    final_summary = models.CharField(max_length=255, blank=True)
    sub_budget = models.ForeignKey(
        "budget.SubBudget", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="receipt_fields",
        verbose_name="Sous-budget",
    )
    confirmed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="confirmed_extractions"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "champs extraits de reçus"

    def __str__(self):
        return f"Fields for {self.receipt_file}"


class DuplicateFlagStatus(models.TextChoices):
    PENDING = "PENDING", "En attente"
    CONFIRMED_DUPLICATE = "CONFIRMED_DUPLICATE", "Doublon confirmé"
    DISMISSED = "DISMISSED", "Rejeté"


class DuplicateFlagQuerySet(models.QuerySet):
    """Query helpers for actionable duplicate warnings."""

    def actionable(self):
        return self.exclude(status=DuplicateFlagStatus.DISMISSED).exclude(
            models.Q(receipt_file__bon_de_commande__is_scan_session=True)
            | models.Q(suspected_duplicate_receipt__bon_de_commande__is_scan_session=True)
            | models.Q(receipt_file__bon_de_commande__status=BonStatus.VOID)
            | models.Q(suspected_duplicate_receipt__bon_de_commande__status=BonStatus.VOID)
        )


class DuplicateFlag(TimeStampedModel):
    """Tracks detected duplicate invoices/receipts for audit and export warnings."""

    objects = DuplicateFlagQuerySet.as_manager()

    receipt_file = models.ForeignKey(
        ReceiptFile, on_delete=models.PROTECT,
        related_name="duplicate_flags",
        help_text="The new receipt flagged as a possible duplicate",
    )
    suspected_duplicate_receipt = models.ForeignKey(
        ReceiptFile, on_delete=models.PROTECT,
        related_name="flagged_as_duplicate_of",
        help_text="The existing receipt this may be a duplicate of",
    )
    confidence = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        help_text="GPT confidence score (0.00–1.00) that these are the same purchase",
    )
    gpt_comparison_result = models.TextField(
        blank=True,
        help_text="Raw GPT response from image comparison",
    )
    status = models.CharField(
        max_length=25,
        choices=DuplicateFlagStatus.choices,
        default=DuplicateFlagStatus.PENDING,
    )
    flagged_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="resolved_duplicate_flags",
    )

    class Meta:
        verbose_name = "signalement de doublon"
        verbose_name_plural = "signalements de doublons"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["receipt_file"]),
        ]

    def __str__(self):
        return (
            f"Duplicate flag: receipt #{self.receipt_file_id} "
            f"↔ #{self.suspected_duplicate_receipt_id} "
            f"({self.get_status_display()})"
        )

    @property
    def confidence_percent(self):
        if self.confidence is None:
            return None
        return self.confidence * 100
