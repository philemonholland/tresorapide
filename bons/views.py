from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View
from django.views.generic.edit import FormView
from django.contrib import messages
from django.template.response import TemplateResponse
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import json
import unicodedata

from accounts.access import RoleRequiredMixin, TreasurerRequiredMixin, check_house_permission
from .models import (
    BonDeCommande, BonStatus, ReceiptFile, ReceiptExtractedFields, OcrStatus,
)
from .forms import (
    BonDeCommandeForm, ReceiptUploadForm, BonValidateForm,
    MultiReceiptUploadForm, OcrReviewForm, BonSearchForm,
)
from .services import generate_bon_number

MONEY_EPSILON = Decimal("0.01")
STANDARD_TPS_RATE = Decimal("0.05")
STANDARD_TVQ_RATE = Decimal("0.09975")


def _normalize_name(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    name = name.strip().lower()
    # Remove accents: é→e, è→e, ê→e, etc.
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return " ".join(name.split())


def _names_match(ocr_name: str, member_name: str, threshold: float = 0.80) -> bool:
    """
    Fuzzy case-insensitive + accent-insensitive name comparison.
    Returns True if names are similar enough (default ≥80% similarity).
    Also returns True if one name is a substring of the other.
    """
    if not ocr_name or not member_name:
        return False
    a = _normalize_name(ocr_name)
    b = _normalize_name(member_name)
    if not a or not b:
        return False
    # Exact or substring match
    if a in b or b in a:
        return True
    # Fuzzy ratio
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _get_house_members_queryset(house):
    """Return active members currently residing in the given house."""
    from members.models import Member, Residency

    house_member_ids = Residency.objects.filter(
        apartment__house=house, end_date__isnull=True,
    ).values_list("member_id", flat=True)
    return Member.objects.filter(
        pk__in=house_member_ids, is_active=True,
    ).order_by("last_name", "first_name")


def _match_member_by_name(house, signer_name: str):
    """Match a signer name against active house members, accent-insensitively."""
    if not signer_name or signer_name.upper() == "ILLISIBLE":
        return None
    for member in _get_house_members_queryset(house):
        if _names_match(signer_name, member.display_name):
            return member
    return None


def _filter_by_house(qs, user):
    """Les non-gestionnaires ne voient que les données de leur maison."""
    if not user.is_gestionnaire:
        return qs.filter(house=user.house)
    return qs


class BonListView(RoleRequiredMixin, ListView):
    model = BonDeCommande
    template_name = "bons/list.html"
    context_object_name = "bons"
    paginate_by = 50

    def get_queryset(self):
        qs = _filter_by_house(
            super().get_queryset(), self.request.user
        ).select_related(
            "budget_year", "sub_budget", "purchaser_member"
        ).filter(is_scan_session=False)

        status = self.request.GET.get("status")
        if status and status in BonStatus.values:
            qs = qs.filter(status=status)

        budget_year = self.request.GET.get("budget_year")
        if budget_year:
            qs = qs.filter(budget_year_id=budget_year)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_manage"] = (
            self.request.user.is_authenticated
            and self.request.user.can_manage_financials
        )
        ctx["status_choices"] = BonStatus.choices
        ctx["current_status"] = self.request.GET.get("status", "")
        ctx["current_budget_year"] = self.request.GET.get("budget_year", "")
        ctx["budget_years"] = BudgetYear.objects.filter(is_active=True)
        return ctx


class BonDetailView(RoleRequiredMixin, DetailView):
    model = BonDeCommande
    template_name = "bons/detail.html"
    context_object_name = "bon"

    def get_queryset(self):
        return _filter_by_house(
            super().get_queryset(), self.request.user
        ).select_related(
            "house", "budget_year", "sub_budget",
            "purchaser_member", "purchaser_apartment",
            "approver_member", "approver_apartment",
            "created_by", "validated_by", "validated_by__member",
        )

    def get_context_data(self, **kwargs):
        from .models import DuplicateFlag
        from audits.models import AuditLogEntry

        ctx = super().get_context_data(**kwargs)
        bon = self.object
        ctx["receipts"] = bon.receipt_files.all()
        ctx["can_manage"] = (
            self.request.user.is_authenticated
            and self.request.user.can_manage_financials
        )
        ctx["upload_form"] = ReceiptUploadForm()
        ctx["export_ready"] = _bon_is_export_ready(bon)
        ctx["signer_roles_ambiguous"] = bon.signer_roles_ambiguous

        # Duplicate flags for any receipt on this bon
        receipt_ids = list(bon.receipt_files.values_list("pk", flat=True))
        ctx["duplicate_flags"] = list(
            DuplicateFlag.objects.actionable().filter(
                receipt_file_id__in=receipt_ids,
            )
            .select_related(
                "receipt_file", "suspected_duplicate_receipt",
                "suspected_duplicate_receipt__bon_de_commande",
            )
        )

        # Audit log
        ctx["audit_entries"] = AuditLogEntry.objects.filter(
            target_app_label="bons",
            target_model="bondecommande",
            target_object_id=str(bon.pk),
        ).select_related("actor")[:20]

        # Amount mismatch warning (paper BC vs invoices)
        if bon.is_paper_bc:
            for receipt in bon.receipt_files.all():
                warning = _get_mismatch_warning(receipt)
                if warning:
                    ctx["mismatch_warning"] = warning
                    break
            # Persistent flag fallback when OCR raw data doesn't trigger a warning
            # but amounts were flagged as unverified during finalization
            if "mismatch_warning" not in ctx and bon.invoice_amounts_unverified:
                ctx["mismatch_warning"] = {
                    "bc_total": f"{bon.total:.2f}" if bon.total else "?",
                    "invoice_total": "N/A",
                    "bc_number": bon.paper_bc_number or bon.number,
                    "unverifiable": True,
                }

        return ctx


class BonCreateView(TreasurerRequiredMixin, View):
    """Redirect bon creation to the OCR upload workflow."""

    def get(self, request):
        return redirect(reverse("bons:upload"))

    def post(self, request):
        return redirect(reverse("bons:upload"))


class BonCreateManualView(TreasurerRequiredMixin, CreateView):
    """Fallback: create a bon entirely manually (no OCR)."""
    model = BonDeCommande
    form_class = BonDeCommandeForm
    template_name = "bons/form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["house"] = self.request.user.house
        return kwargs

    def form_valid(self, form):
        bon = form.instance
        house = self.request.user.house
        bon.house = house
        bon.created_by = self.request.user
        bon.number = generate_bon_number(house, form.cleaned_data["budget_year"].year)
        bon.status = BonStatus.DRAFT
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["is_create"] = True
        return ctx

    def get_success_url(self):
        return reverse("bons:detail", kwargs={"pk": self.object.pk})


class BonUpdateView(TreasurerRequiredMixin, UpdateView):
    model = BonDeCommande
    form_class = BonDeCommandeForm
    template_name = "bons/form.html"

    FINANCIAL_FIELDS = {"subtotal", "tps", "tvq", "total"}

    def get_queryset(self):
        qs = super().get_queryset()
        qs = _filter_by_house(qs, self.request.user)
        return qs.filter(status__in=[BonStatus.DRAFT, BonStatus.READY_FOR_REVIEW, BonStatus.READY_FOR_VALIDATION])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["house"] = self.object.house
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["is_create"] = False
        return ctx

    def form_valid(self, form):
        from audits.models import AuditLogEntry
        from .ocr_service import DuplicateDetectionService

        bon = self.object
        old_values = {}
        changed_fields = form.changed_data
        financial_changed = False

        for field in changed_fields:
            old_val = getattr(bon, field, None)
            if hasattr(old_val, "pk"):
                old_values[field] = str(old_val)
            else:
                old_values[field] = str(old_val) if old_val is not None else ""
            if field in self.FINANCIAL_FIELDS:
                financial_changed = True

        response = super().form_valid(form)

        # Audit trail
        if changed_fields:
            new_values = {}
            for field in changed_fields:
                new_val = getattr(self.object, field, None)
                if hasattr(new_val, "pk"):
                    new_values[field] = str(new_val)
                else:
                    new_values[field] = str(new_val) if new_val is not None else ""

            AuditLogEntry.objects.create(
                actor=self.request.user,
                action="bon.edited",
                target_app_label="bons",
                target_model="bondecommande",
                target_object_id=str(bon.pk),
                summary=f"Champs modifiés : {', '.join(changed_fields)}",
                payload={"old": old_values, "new": new_values},
                ip_address=self.request.META.get("REMOTE_ADDR"),
            )

        # Re-run duplicate detection if financial fields changed
        if financial_changed:
            for receipt in bon.receipt_files.filter(
                ocr_status__in=[OcrStatus.EXTRACTED, OcrStatus.CORRECTED]
            ):
                DuplicateDetectionService.check_and_flag_duplicates(receipt, bon.house)

        return response

    def get_success_url(self):
        return reverse("bons:detail", kwargs={"pk": self.object.pk})


class BonSwapSignersView(TreasurerRequiredMixin, View):
    """Swap purchaser and approver roles on an existing bon in one click."""

    def post(self, request, pk):
        bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user),
            pk=pk,
        )
        if not bon.approver_member_id:
            if bon.approver_is_external:
                messages.error(
                    request,
                    "Le validateur est un fournisseur externe — l'échange de rôles n'est pas possible.",
                )
            else:
                messages.error(request, "Aucun 2e signataire à échanger pour ce bon.")
            return redirect(reverse("bons:detail", kwargs={"pk": bon.pk}))

        (
            bon.purchaser_member_id,
            bon.approver_member_id,
        ) = (
            bon.approver_member_id,
            bon.purchaser_member_id,
        )
        (
            bon.purchaser_apartment_id,
            bon.approver_apartment_id,
        ) = (
            bon.approver_apartment_id,
            bon.purchaser_apartment_id,
        )
        bon.refresh_snapshot_fields()
        bon.save()
        bon.expenses.update(spent_by_label=bon.purchaser_display_label)
        messages.warning(
            request,
            "Les rôles acheteur / validateur ont été échangés. Veuillez confirmer l'attribution.",
        )
        return redirect(reverse("bons:detail", kwargs={"pk": bon.pk}))


class BonValidateView(TreasurerRequiredMixin, FormView):
    template_name = "bons/validate_confirm.html"
    form_class = BonValidateForm

    def dispatch(self, request, *args, **kwargs):
        self.bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user),
            pk=self.kwargs["pk"],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["bon"] = self.bon
        # Show how many bons still need validation in the same house/year
        pending_count = (
            BonDeCommande.objects
            .filter(
                house=self.bon.house,
                budget_year=self.bon.budget_year,
                is_scan_session=False,
                status__in=[BonStatus.READY_FOR_VALIDATION, BonStatus.READY_FOR_REVIEW],
            )
            .exclude(pk=self.bon.pk)
            .count()
        )
        ctx["pending_validation_count"] = pending_count

        # Amount mismatch warning (paper BC vs invoices)
        if self.bon.is_paper_bc:
            for receipt in self.bon.receipt_files.all():
                warning = _get_mismatch_warning(receipt)
                if warning:
                    ctx["mismatch_warning"] = warning
                    break
            if "mismatch_warning" not in ctx and self.bon.invoice_amounts_unverified:
                ctx["mismatch_warning"] = {
                    "bc_total": f"{self.bon.total:.2f}" if self.bon.total else "?",
                    "invoice_total": "N/A",
                    "bc_number": self.bon.paper_bc_number or self.bon.number,
                    "unverifiable": True,
                }

        return ctx

    def form_valid(self, form):
        bon = self.bon
        # Prevent double-validation and duplicate expense entries
        if bon.status not in (BonStatus.READY_FOR_VALIDATION, BonStatus.READY_FOR_REVIEW):
            messages.error(self.request, "Ce bon ne peut plus être validé (statut actuel : {}).".format(
                bon.get_status_display()
            ))
            return redirect(self.get_success_url())

        bon.status = BonStatus.VALIDATED
        bon.validated_by = self.request.user
        bon.validated_at = timezone.now()
        bon.refresh_snapshot_fields()
        bon.save()

        # Create expense entry in the budget
        from budget.models import Expense, ExpenseSourceType
        if bon.sub_budget and bon.total:
            Expense.objects.create(
                budget_year=bon.budget_year,
                sub_budget=bon.sub_budget,
                bon_de_commande=bon,
                entry_date=bon.purchase_date or timezone.now().date(),
                description=bon.short_description or f"BC {bon.number}",
                bon_number=bon.number,
                supplier_name=bon.supplier_name or bon.merchant_name or "",
                spent_by_label=bon.purchaser_display_label,
                amount=bon.total,
                source_type=ExpenseSourceType.BON_DE_COMMANDE,
                entered_by=self.request.user,
            )

        messages.success(self.request, f"Le bon {bon.number} a été validé et ajouté à la grille de dépenses.")

        # Redirect to the next bon awaiting validation in the same house/year
        next_bon = (
            BonDeCommande.objects
            .filter(
                house=bon.house,
                budget_year=bon.budget_year,
                is_scan_session=False,
                status__in=[BonStatus.READY_FOR_VALIDATION, BonStatus.READY_FOR_REVIEW],
            )
            .order_by("created_at", "pk")
            .first()
        )
        if next_bon:
            messages.info(
                self.request,
                f"Prochain bon à valider : BC {next_bon.number}."
            )
            return redirect(reverse("bons:detail", kwargs={"pk": next_bon.pk}))

        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse("bons:list")


class ReceiptUploadToExistingView(TreasurerRequiredMixin, View):
    """Upload a receipt to an existing bon and run GPT Vision OCR."""

    def post(self, request, bon_pk):
        bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user),
            pk=bon_pk,
        )
        form = ReceiptUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Erreur lors du téléversement du fichier.")
            return redirect(reverse("bons:detail", kwargs={"pk": bon.pk}))

        uploaded = form.cleaned_data["file"]
        receipt = ReceiptFile.objects.create(
            bon_de_commande=bon,
            file=uploaded,
            original_filename=uploaded.name,
            content_type=getattr(uploaded, "content_type", ""),
            uploaded_by=request.user,
        )

        # Run GPT Vision OCR (same as scan session flow)
        from .ocr_service import ReceiptOcrService
        if ReceiptOcrService.is_available():
            _, err = ReceiptOcrService.process_receipts_batch([receipt], house=bon.house)
            if err:
                messages.warning(request, err)
        else:
            messages.warning(
                request,
                "L'analyse automatique n'est pas disponible. "
                "Vérifiez les données manuellement.",
            )

        messages.success(request, "Reçu téléversé et analysé.")
        return redirect(
            reverse("bons:receipt-review", kwargs={
                "bon_pk": bon.pk, "receipt_pk": receipt.pk,
            })
        )


class ReceiptReviewSingleView(TreasurerRequiredMixin, View):
    """Review GPT-extracted data for a single receipt on an existing bon."""

    template_name = "bons/receipt_review_single.html"

    def _get_context(self, request, bon, receipt):
        from budget.models import SubBudget

        initial = {}
        matched_member_name = ""
        name_mismatch = False
        expense_member_mismatch = False
        validator_member_mismatch = False
        doc_type = "receipt"

        try:
            ef = receipt.extracted_fields
            doc_type = ef.final_document_type or ef.document_type_candidate or "receipt"
            apt_code = ef.final_apartment_number or ef.apartment_number_candidate
            member_name_raw = ef.final_member_name or ef.member_name_candidate

            apartment, member = _resolve_member_assignment(
                bon.house, apt_code, member_name_raw,
            )
            if apartment:
                apt_code = apartment.code
            if member:
                matched_member_name = member.display_name
                initial["matched_member_id"] = member.pk
                initial["purchaser_member"] = member.pk

            name_unreadable = not member_name_raw or member_name_raw.upper() == "ILLISIBLE"
            if member and member_name_raw and not name_unreadable:
                if not _names_match(member_name_raw, member.display_name):
                    name_mismatch = True

            initial.update({
                "document_type": doc_type,
                "bc_number": ef.final_bc_number or ef.bc_number_candidate or "",
                "associated_bc_number": ef.final_associated_bc_number or ef.associated_bc_number_candidate or "",
                "supplier_name": ef.final_supplier_name or ef.supplier_name_candidate or "",
                "supplier_address": ef.final_supplier_address or ef.supplier_address_candidate or "",
                "member_name_raw": member_name_raw if (member and not name_mismatch and not name_unreadable) else "",
                "apartment_number": apt_code,
                "merchant_name": ef.final_merchant or ef.merchant_candidate,
                "purchase_date": ef.final_purchase_date or ef.purchase_date_candidate,
                "subtotal": ef.final_subtotal if ef.final_subtotal is not None else ef.subtotal_candidate,
                "tps": ef.final_tps if ef.final_tps is not None else ef.tps_candidate,
                "tvq": ef.final_tvq if ef.final_tvq is not None else ef.tvq_candidate,
                "total": ef.final_total if ef.final_total is not None else ef.total_candidate,
                "summary": ef.final_summary or ef.summary_candidate or "",
            })

            # Expense member fields (paper BC)
            if doc_type == "paper_bc":
                signer_initials, expense_member_mismatch, validator_member_mismatch, _, _ = (
                    _paper_bc_signer_initials(bon.house, ef)
                )
                initial.update(signer_initials)

            # Pre-select existing bon's sub_budget if not set in extracted data
            if ef.sub_budget_id:
                initial["sub_budget"] = ef.sub_budget_id
            elif bon.sub_budget_id:
                initial["sub_budget"] = bon.sub_budget_id
        except ReceiptExtractedFields.DoesNotExist:
            if bon.sub_budget_id:
                initial["sub_budget"] = bon.sub_budget_id

        prefix = f"receipt_{receipt.pk}"
        form = OcrReviewForm(prefix=prefix, initial=initial)

        members_qs = _get_house_members_queryset(bon.house)
        form.fields["purchaser_member"].queryset = members_qs
        form.fields["expense_member"].queryset = members_qs
        form.fields["validator_member"].queryset = members_qs
        form.fields["sub_budget"].queryset = SubBudget.objects.filter(
            budget_year=bon.budget_year, is_active=True,
        ).order_by("sort_order", "trace_code")

        mismatch_warning = _get_mismatch_warning(receipt)

        return {
            "bon": bon,
            "receipt": receipt,
            "form": form,
            "matched_member_name": matched_member_name,
            "name_mismatch": name_mismatch,
            "expense_member_mismatch": expense_member_mismatch,
            "validator_member_mismatch": validator_member_mismatch,
            "validator_is_external": bool(initial.get("validator_is_external")),
            "document_type": doc_type,
            "mismatch_warning": mismatch_warning,
            "signer_roles_ambiguous": bool(initial.get("signer_roles_ambiguous")),
        }

    def get(self, request, bon_pk, receipt_pk):
        bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user), pk=bon_pk,
        )
        receipt = get_object_or_404(ReceiptFile, pk=receipt_pk, bon_de_commande=bon)
        ctx = self._get_context(request, bon, receipt)
        return TemplateResponse(request, self.template_name, ctx)

    def post(self, request, bon_pk, receipt_pk):
        from budget.models import SubBudget

        bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user), pk=bon_pk,
        )
        receipt = get_object_or_404(ReceiptFile, pk=receipt_pk, bon_de_commande=bon)

        prefix = f"receipt_{receipt.pk}"
        form = OcrReviewForm(data=request.POST, prefix=prefix)
        members_qs = _get_house_members_queryset(bon.house)
        form.fields["purchaser_member"].queryset = members_qs
        form.fields["expense_member"].queryset = members_qs
        form.fields["validator_member"].queryset = members_qs
        form.fields["sub_budget"].queryset = SubBudget.objects.filter(
            budget_year=bon.budget_year, is_active=True,
        ).order_by("sort_order", "trace_code")

        if not form.is_valid():
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
            ctx = self._get_context(request, bon, receipt)
            ctx["form"] = form
            return TemplateResponse(request, self.template_name, ctx)

        # Save confirmed fields
        selected_member = form.cleaned_data.get("purchaser_member")
        ef, _ = ReceiptExtractedFields.objects.get_or_create(receipt_file=receipt)
        ef.final_document_type = form.cleaned_data.get("document_type") or ""
        ef.final_bc_number = form.cleaned_data.get("bc_number") or ""
        ef.final_associated_bc_number = form.cleaned_data.get("associated_bc_number") or ""
        ef.final_supplier_name = form.cleaned_data.get("supplier_name") or ""
        ef.final_supplier_address = form.cleaned_data.get("supplier_address") or ""
        _save_paper_bc_extracted_fields(ef, form, bon.house)
        ef.final_member_name = selected_member.display_name if selected_member else ""
        ef.final_apartment_number = form.cleaned_data.get("apartment_number") or ""
        ef.final_merchant = form.cleaned_data.get("merchant_name") or ""
        ef.final_purchase_date = form.cleaned_data.get("purchase_date")
        ef.final_subtotal = form.cleaned_data.get("subtotal")
        ef.final_tps = form.cleaned_data.get("tps")
        ef.final_tvq = form.cleaned_data.get("tvq")
        ef.final_total = form.cleaned_data.get("total")
        ef.final_summary = form.cleaned_data.get("summary") or ""
        ef.sub_budget = form.cleaned_data.get("sub_budget")
        ef.confirmed_by = request.user
        ef.confirmed_at = timezone.now()
        ef.save()

        if receipt.ocr_status in (OcrStatus.EXTRACTED, OcrStatus.NOT_REQUESTED, OcrStatus.FAILED):
            receipt.ocr_status = OcrStatus.CORRECTED
            receipt.save(update_fields=["ocr_status"])

        # Auto-save merchant
        merchant_name = (form.cleaned_data.get("merchant_name") or "").strip()
        if merchant_name:
            from .models import Merchant
            Merchant.objects.get_or_create(name=merchant_name)

        if ef.final_document_type == "paper_bc":
            OcrReviewView()._sync_existing_bon_paper_bc_data(bon)

        messages.success(request, "Données du reçu confirmées.")
        return redirect(reverse("bons:detail", kwargs={"pk": bon.pk}))


# ---------------------------------------------------------------------------
# OCR Workflow — Step 1: Upload receipts
# ---------------------------------------------------------------------------

def _match_member_for_apartment(house, apartment_code):
    """Look up current member from apartment code in the house."""
    from members.models import Apartment, Residency
    apartment = Apartment.objects.filter(
        house=house, code=apartment_code, is_active=True
    ).first()
    if not apartment:
        return None, None
    residency = Residency.objects.filter(
        apartment=apartment, end_date__isnull=True
    ).select_related("member").first()
    if residency:
        return apartment, residency.member
    return apartment, None


def _resolve_member_assignment(house, apartment_code: str, member_name: str):
    """Resolve a member using apartment first, but let a strong name match override a bad apartment."""
    apartment = None
    member = None
    if apartment_code:
        apartment, member = _match_member_for_apartment(house, apartment_code)
    if member_name and member_name.upper() != "ILLISIBLE":
        matched_by_name = _match_member_by_name(house, member_name)
        if matched_by_name and (not member or not _names_match(member_name, member.display_name)):
            member = matched_by_name
            apartment = matched_by_name.current_apartment() or apartment
    elif not member and member_name:
        member = _match_member_by_name(house, member_name)
        if member:
            apartment = member.current_apartment() or apartment
    return apartment, member


def _resolve_signer_assignment(house, apartment_code: str, signer_name: str):
    """Backward-compatible wrapper for signer assignment."""
    return _resolve_member_assignment(house, apartment_code, signer_name)


def _money(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(MONEY_EPSILON)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _normalize_document_amounts(subtotal=None, tps=None, tvq=None, total=None):
    """Normalize one document's financials and derive missing taxes/totals when possible.

    Québec tax rates used for derivation:
      TPS = 5 %  (federal GST)
      TVQ = 9.975 %  (provincial QST)
      Combined factor = 1 + 0.05 + 0.09975 = 1.14975
    """
    subtotal = _money(subtotal)
    tps = _money(tps)
    tvq = _money(tvq)
    total = _money(total)

    # Case: subtotal + taxes known → derive total
    if subtotal is not None and total is None and tps is not None and tvq is not None:
        total = (subtotal + tps + tvq).quantize(MONEY_EPSILON)

    # Case: total + taxes known → derive subtotal
    if total is not None and subtotal is None and tps is not None and tvq is not None:
        subtotal = (total - tps - tvq).quantize(MONEY_EPSILON)

    # Case: only total known → derive subtotal and taxes from standard rates
    if total is not None and subtotal is None and tps is None and tvq is None:
        combined = Decimal("1") + STANDARD_TPS_RATE + STANDARD_TVQ_RATE
        subtotal = (total / combined).quantize(MONEY_EPSILON)
        tps = (subtotal * STANDARD_TPS_RATE).quantize(MONEY_EPSILON)
        tvq = (total - subtotal - tps).quantize(MONEY_EPSILON)

    # Case: subtotal + total known but no taxes → derive taxes
    if total is not None and subtotal is not None:
        if tps is None and tvq is None and total >= subtotal:
            tps = (subtotal * STANDARD_TPS_RATE).quantize(MONEY_EPSILON)
            tvq = (total - subtotal - tps).quantize(MONEY_EPSILON)
            if tvq < Decimal("0.00"):
                tps = None
                tvq = None
        elif tps is None and tvq is not None:
            derived_tps = (total - subtotal - tvq).quantize(MONEY_EPSILON)
            if derived_tps >= Decimal("0.00"):
                tps = derived_tps
        elif tvq is None and tps is not None:
            derived_tvq = (total - subtotal - tps).quantize(MONEY_EPSILON)
            if derived_tvq >= Decimal("0.00"):
                tvq = derived_tvq

    # Case: subtotal + taxes known → derive total (re-check after derivation)
    if total is None and subtotal is not None and tps is not None and tvq is not None:
        total = (subtotal + tps + tvq).quantize(MONEY_EPSILON)

    return {
        "subtotal": subtotal,
        "tps": tps,
        "tvq": tvq,
        "total": total,
    }


def _extracted_document_type(ef) -> str:
    return (ef.final_document_type or ef.document_type_candidate or "receipt").strip()


def _extracted_document_amounts(ef):
    return _normalize_document_amounts(
        ef.final_subtotal if ef.final_subtotal is not None else ef.subtotal_candidate,
        ef.final_tps if ef.final_tps is not None else ef.tps_candidate,
        ef.final_tvq if ef.final_tvq is not None else ef.tvq_candidate,
        ef.final_total if ef.final_total is not None else ef.total_candidate,
    )


def _safe_extracted_fields(receipt):
    """Return the receipt's ReceiptExtractedFields or None if missing."""
    try:
        return receipt.extracted_fields
    except ReceiptExtractedFields.DoesNotExist:
        return None


def _aggregate_extracted_amounts(extracted_fields, *, prefer_invoice_totals=False):
    """Aggregate totals from extracted fields, optionally preferring invoices over the paper BC."""
    documents = []
    paper_bc_documents = []
    invoice_documents = []

    for ef in extracted_fields:
        doc = {
            "document_type": _extracted_document_type(ef),
            **_extracted_document_amounts(ef),
        }
        documents.append(doc)
        if doc["document_type"] == "paper_bc":
            paper_bc_documents.append(doc)
        elif doc["document_type"] == "invoice":
            invoice_documents.append(doc)

    chosen_documents = documents
    using_invoices = False
    if prefer_invoice_totals:
        if invoice_documents and all(doc["total"] is not None for doc in invoice_documents):
            chosen_documents = invoice_documents
            using_invoices = True
        elif paper_bc_documents:
            chosen_documents = paper_bc_documents

    subtotal = Decimal("0.00")
    tps = Decimal("0.00")
    tvq = Decimal("0.00")
    total = Decimal("0.00")
    has_any_total = False
    all_subtotals_known = bool(chosen_documents)
    all_tps_known = bool(chosen_documents)
    all_tvq_known = bool(chosen_documents)
    all_totals_known = bool(chosen_documents)

    for doc in chosen_documents:
        if doc["total"] is None:
            all_totals_known = False
        else:
            total += doc["total"]
            has_any_total = True

        if doc["subtotal"] is None:
            all_subtotals_known = False
        else:
            subtotal += doc["subtotal"]

        if doc["tps"] is None:
            all_tps_known = False
        else:
            tps += doc["tps"]

        if doc["tvq"] is None:
            all_tvq_known = False
        else:
            tvq += doc["tvq"]

    return {
        "documents": chosen_documents,
        "using_invoices": using_invoices,
        "subtotal": subtotal.quantize(MONEY_EPSILON) if all_subtotals_known else None,
        "tps": tps.quantize(MONEY_EPSILON) if all_tps_known else None,
        "tvq": tvq.quantize(MONEY_EPSILON) if all_tvq_known else None,
        "total": total.quantize(MONEY_EPSILON) if has_any_total and all_totals_known else None,
    }


def _aggregate_receipt_amounts(receipts, *, prefer_invoice_totals=False):
    extracted_fields = []
    for receipt in receipts:
        try:
            extracted_fields.append(receipt.extracted_fields)
        except ReceiptExtractedFields.DoesNotExist:
            continue
    return _aggregate_extracted_amounts(
        extracted_fields,
        prefer_invoice_totals=prefer_invoice_totals,
    )


def _aggregate_json_document_amounts(documents, *, prefer_invoice_totals=False):
    normalized_documents = []
    paper_bc_documents = []
    invoice_documents = []

    for doc in documents:
        normalized = {
            "document_type": (doc.get("document_type") or "receipt").strip(),
            **_normalize_document_amounts(
                doc.get("subtotal"),
                doc.get("tps"),
                doc.get("tvq"),
                doc.get("total"),
            ),
        }
        normalized_documents.append(normalized)
        if normalized["document_type"] == "paper_bc":
            paper_bc_documents.append(normalized)
        elif normalized["document_type"] == "invoice":
            invoice_documents.append(normalized)

    chosen_documents = normalized_documents
    if prefer_invoice_totals:
        if invoice_documents and all(doc["total"] is not None for doc in invoice_documents):
            chosen_documents = invoice_documents
        elif paper_bc_documents:
            chosen_documents = paper_bc_documents

    subtotal = Decimal("0.00")
    tps = Decimal("0.00")
    tvq = Decimal("0.00")
    total = Decimal("0.00")
    all_subtotals_known = bool(chosen_documents)
    all_tps_known = bool(chosen_documents)
    all_tvq_known = bool(chosen_documents)
    all_totals_known = bool(chosen_documents)

    for doc in chosen_documents:
        if doc["subtotal"] is None:
            all_subtotals_known = False
        else:
            subtotal += doc["subtotal"]
        if doc["tps"] is None:
            all_tps_known = False
        else:
            tps += doc["tps"]
        if doc["tvq"] is None:
            all_tvq_known = False
        else:
            tvq += doc["tvq"]
        if doc["total"] is None:
            all_totals_known = False
        else:
            total += doc["total"]

    return {
        "subtotal": subtotal.quantize(MONEY_EPSILON) if all_subtotals_known else None,
        "tps": tps.quantize(MONEY_EPSILON) if all_tps_known else None,
        "tvq": tvq.quantize(MONEY_EPSILON) if all_tvq_known else None,
        "total": total.quantize(MONEY_EPSILON) if all_totals_known else None,
    }


def _paper_bc_signer_initials(house, ef):
    """Build review-form initials and mismatch flags for paper BC signer fields.

    When the validator name doesn't resolve to a coop member, they are treated
    as an external supplier (fournisseur) — no mismatch is flagged, and the
    initial dict gets ``validator_is_external = True``.
    """
    purchaser_name = ef.final_expense_member_name or ef.expense_member_name_candidate or ""
    purchaser_apt = ef.final_expense_apartment or ef.expense_apartment_candidate or ""
    validator_name = ef.final_validator_member_name or ef.validator_member_name_candidate or ""
    validator_apt = ef.final_validator_apartment or ef.validator_apartment_candidate or ""
    roles_ambiguous = (
        ef.signer_roles_ambiguous_final
        or ef.signer_roles_ambiguous_candidate
    )

    initial = {
        "expense_member_name": purchaser_name,
        "expense_apartment": purchaser_apt,
        "validator_member_name": validator_name,
        "validator_apartment": validator_apt,
        "signer_roles_ambiguous": roles_ambiguous,
        "validator_is_external": False,
    }
    purchaser_mismatch = False
    validator_mismatch = False

    purchaser_apartment, purchaser_member = _resolve_signer_assignment(
        house, purchaser_apt, purchaser_name,
    )
    if purchaser_apartment:
        initial["expense_apartment"] = purchaser_apartment.code
    if purchaser_member:
        initial["expense_member"] = purchaser_member.pk
        if purchaser_name and purchaser_name.upper() != "ILLISIBLE":
            purchaser_mismatch = not _names_match(purchaser_name, purchaser_member.display_name)
    elif purchaser_name and purchaser_name.upper() != "ILLISIBLE":
        purchaser_mismatch = True

    validator_apartment, validator_member = _resolve_signer_assignment(
        house, validator_apt, validator_name,
    )
    if validator_apartment:
        initial["validator_apartment"] = validator_apartment.code
    if validator_member:
        initial["validator_member"] = validator_member.pk
        if validator_name and validator_name.upper() != "ILLISIBLE":
            validator_mismatch = not _names_match(validator_name, validator_member.display_name)
    elif validator_name and validator_name.upper() != "ILLISIBLE":
        # Validator name exists but doesn't match any member → external supplier
        initial["validator_is_external"] = True

    if purchaser_member and validator_member and purchaser_member.pk == validator_member.pk:
        validator_mismatch = True

    return initial, purchaser_mismatch, validator_mismatch, purchaser_apartment, validator_apartment


def _save_paper_bc_extracted_fields(ef, form, house=None):
    """Persist normalized purchaser/validator signer fields from review form."""
    expense_member = form.cleaned_data.get("expense_member")
    validator_member = form.cleaned_data.get("validator_member")
    expense_apartment = form.cleaned_data.get("expense_apartment") or ""
    validator_apartment = form.cleaned_data.get("validator_apartment") or ""

    if expense_member:
        member_apartment = expense_member.current_apartment()
        if member_apartment and (house is None or member_apartment.house_id == house.id):
            expense_apartment = member_apartment.code
    if validator_member:
        member_apartment = validator_member.current_apartment()
        if member_apartment and (house is None or member_apartment.house_id == house.id):
            validator_apartment = member_apartment.code

    ef.final_expense_member_name = (
        expense_member.display_name if expense_member else (form.cleaned_data.get("expense_member_name") or "")
    )
    ef.final_expense_apartment = expense_apartment
    ef.final_validator_member_name = (
        validator_member.display_name if validator_member else (form.cleaned_data.get("validator_member_name") or "")
    )
    ef.final_validator_apartment = validator_apartment
    ef.signer_roles_ambiguous_final = bool(form.cleaned_data.get("signer_roles_ambiguous"))


class ReceiptUploadWizardView(TreasurerRequiredMixin, FormView):
    """Step 1: Upload receipt files → run AI analysis → create bons grouped by member."""
    template_name = "bons/upload.html"
    form_class = MultiReceiptUploadForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["house"] = self.request.user.house
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .ocr_service import ReceiptOcrService
        ctx["ocr_available"] = ReceiptOcrService.is_available()
        return ctx

    def form_valid(self, form):
        user = self.request.user
        house = user.house
        budget_year = form.cleaned_data["budget_year"]
        files = form.cleaned_data["files"]

        if not files:
            messages.error(self.request, "Veuillez sélectionner au moins un fichier.")
            return self.form_invalid(form)

        from budget.models import SubBudget
        default_sub = SubBudget.objects.filter(
            budget_year=budget_year, is_active=True
        ).order_by("sort_order", "trace_code").first()

        # Create a temporary DRAFT bon to hold all uploaded receipts
        temp_bon = BonDeCommande()
        temp_bon.house = house
        temp_bon.budget_year = budget_year
        temp_bon.number = generate_bon_number(house, budget_year.year)
        temp_bon.purchase_date = timezone.now().date()
        temp_bon.short_description = "(en cours de création)"
        temp_bon.total = 0
        temp_bon.sub_budget = default_sub
        # Use user's own member or a placeholder
        if user.member_id:
            temp_bon.purchaser_member = user.member
        else:
            from members.models import Member
            temp_bon.purchaser_member = Member.objects.filter(is_active=True).first()
        temp_bon.created_by = user
        temp_bon.status = BonStatus.DRAFT
        temp_bon.is_scan_session = True
        temp_bon.entered_date = timezone.now().date()
        super(BonDeCommande, temp_bon).save()

        from .ocr_service import ReceiptOcrService
        ocr_available = ReceiptOcrService.is_available()
        errors = []
        receipt_objs = []

        for f in files:
            receipt = ReceiptFile.objects.create(
                bon_de_commande=temp_bon,
                file=f,
                original_filename=f.name,
                content_type=getattr(f, "content_type", ""),
                uploaded_by=user,
            )
            receipt_objs.append(receipt)

        # Single batch API call for all receipts
        if ocr_available and receipt_objs:
            _, err = ReceiptOcrService.process_receipts_batch(receipt_objs, house=house)
            if err:
                errors.append(err)

        BonDeCommande.objects.filter(pk=temp_bon.pk).update(
            status=BonStatus.READY_FOR_REVIEW
        )

        if errors:
            for err in errors:
                messages.warning(self.request, err)
        if not ocr_available:
            messages.warning(
                self.request,
                "L'analyse automatique n'est pas disponible. Saisissez les informations manuellement.",
            )

        messages.success(
            self.request,
            f"{len(files)} reçu(s) téléversé(s). Vérifiez les données extraites.",
        )
        # Redirect to review first receipt
        return redirect(reverse("bons:review", kwargs={"pk": temp_bon.pk}) + "?idx=0")


# ---------------------------------------------------------------------------
# OCR Workflow — Step 2: Review receipts one at a time
# ---------------------------------------------------------------------------

def _get_mismatch_warning(receipt):
    """Check if paper BC total matches invoice totals.

    Returns a warning dict when:
    - BC and invoice totals differ
    - Invoices exist but amounts couldn't be extracted (unverifiable)
    """
    try:
        all_docs = json.loads(receipt.ocr_raw_text or "[]")
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(all_docs, list):
        return None

    paper_bcs = [d for d in all_docs if d.get("document_type") == "paper_bc"]
    invoices = [d for d in all_docs if d.get("document_type") == "invoice"]

    if not paper_bcs or not invoices:
        return None

    try:
        paper_amounts = _aggregate_json_document_amounts(paper_bcs)
        invoice_amounts = _aggregate_json_document_amounts(
            invoices,
            prefer_invoice_totals=True,
        )
        bc_total = paper_amounts["total"]
        invoice_total = invoice_amounts["total"]
        bc_number = paper_bcs[0].get("bc_number", "?")

        if bc_total is None:
            return None

        # Invoice amounts couldn't be extracted → warn that verification failed
        if invoice_total is None:
            return {
                "bc_total": f"{bc_total:.2f}",
                "invoice_total": "N/A",
                "bc_number": bc_number,
                "unverifiable": True,
            }

        comparable_invoice_values = [invoice_total]
        if invoice_amounts["subtotal"] is not None:
            comparable_invoice_values.append(invoice_amounts["subtotal"])
        if invoice_amounts["subtotal"] is not None and invoice_amounts["tps"] is not None:
            comparable_invoice_values.append(
                (invoice_amounts["subtotal"] + invoice_amounts["tps"]).quantize(MONEY_EPSILON)
            )
        if invoice_amounts["subtotal"] is not None and invoice_amounts["tvq"] is not None:
            comparable_invoice_values.append(
                (invoice_amounts["subtotal"] + invoice_amounts["tvq"]).quantize(MONEY_EPSILON)
            )

        if not any(abs(bc_total - value) <= MONEY_EPSILON for value in comparable_invoice_values):
            return {
                "bc_total": f"{bc_total:.2f}",
                "invoice_total": f"{invoice_total:.2f}",
                "bc_number": bc_number,
            }
    except (ValueError, TypeError):
        return None
    return None


class OcrReviewView(TreasurerRequiredMixin, View):
    """Step 2: Review extracted data one receipt at a time with prev/next."""
    template_name = "bons/review.html"

    def _get_bon(self, request, pk):
        return get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user),
            pk=pk,
            status__in=[BonStatus.DRAFT, BonStatus.READY_FOR_REVIEW],
        )

    def _get_receipt_and_index(self, bon, request):
        """Return (receipts_list, current_receipt, current_index, total)."""
        receipts = list(bon.receipt_files.order_by("created_at", "pk"))
        total = len(receipts)
        try:
            idx = int(request.GET.get("idx", request.POST.get("idx", 0)))
        except (ValueError, TypeError):
            idx = 0
        idx = max(0, min(idx, total - 1)) if total > 0 else 0
        current = receipts[idx] if receipts else None
        return receipts, current, idx, total

    def _build_form(self, receipt, bon, post_data=None):
        """Build OcrReviewForm for a single receipt, pre-filled from AI data."""
        from budget.models import SubBudget

        prefix = f"receipt_{receipt.pk}"
        initial = {}
        matched_member_name = ""
        name_mismatch = False
        expense_member_mismatch = False
        validator_member_mismatch = False
        doc_type = "receipt"

        try:
            ef = receipt.extracted_fields
            doc_type = ef.final_document_type or ef.document_type_candidate or "receipt"
            initial["document_type"] = doc_type
            initial["bc_number"] = ef.final_bc_number or ef.bc_number_candidate or ""
            initial["associated_bc_number"] = ef.final_associated_bc_number or ef.associated_bc_number_candidate or ""
            initial["supplier_name"] = ef.final_supplier_name or ef.supplier_name_candidate or ""
            initial["supplier_address"] = ef.final_supplier_address or ef.supplier_address_candidate or ""

            if doc_type == "paper_bc":
                signer_initials, expense_member_mismatch, validator_member_mismatch, _, _ = (
                    _paper_bc_signer_initials(bon.house, ef)
                )
                initial.update(signer_initials)

            apt_code = ef.final_apartment_number or ef.apartment_number_candidate
            member_name_raw = ef.final_member_name or ef.member_name_candidate

            # Only do member matching for receipts
            if doc_type == "receipt":
                apartment, member = _resolve_member_assignment(
                    bon.house, apt_code, member_name_raw,
                )
                if apartment:
                    apt_code = apartment.code
                if member:
                    matched_member_name = member.display_name
                    initial["matched_member_id"] = member.pk
                    initial["purchaser_member"] = member.pk

                name_unreadable = not member_name_raw or member_name_raw.upper() == "ILLISIBLE"
                if member and member_name_raw and not name_unreadable:
                    if not _names_match(member_name_raw, member.display_name):
                        name_mismatch = True

                initial["member_name_raw"] = member_name_raw if (member and not name_mismatch and not name_unreadable) else ""

            initial.update({
                "apartment_number": apt_code,
                "merchant_name": ef.final_merchant or ef.merchant_candidate,
                "purchase_date": ef.final_purchase_date or ef.purchase_date_candidate,
                "subtotal": ef.final_subtotal if ef.final_subtotal is not None else ef.subtotal_candidate,
                "tps": ef.final_tps if ef.final_tps is not None else ef.tps_candidate,
                "tvq": ef.final_tvq if ef.final_tvq is not None else ef.tvq_candidate,
                "total": ef.final_total if ef.final_total is not None else ef.total_candidate,
                "summary": ef.final_summary or ef.summary_candidate or "",
            })
        except ReceiptExtractedFields.DoesNotExist:
            initial["document_type"] = "receipt"

        form = OcrReviewForm(data=post_data, prefix=prefix, initial=initial)

        # Populate member choices: all active members with residency in this house
        members_qs = _get_house_members_queryset(bon.house)
        form.fields["purchaser_member"].queryset = members_qs
        form.fields["expense_member"].queryset = members_qs
        form.fields["validator_member"].queryset = members_qs

        # Populate sub_budget choices
        form.fields["sub_budget"].queryset = SubBudget.objects.filter(
            budget_year=bon.budget_year, is_active=True
        ).order_by("sort_order", "trace_code")

        return (
            form,
            matched_member_name,
            name_mismatch,
            doc_type,
            expense_member_mismatch,
            validator_member_mismatch,
            bool(initial.get("validator_is_external")),
        )

    def get(self, request, pk):
        bon = self._get_bon(request, pk)
        receipts, current, idx, total = self._get_receipt_and_index(bon, request)
        if not current:
            messages.warning(request, "Aucun reçu à vérifier.")
            return redirect(reverse("bons:list"))
        (
            form,
            matched_name,
            name_mismatch,
            doc_type,
            expense_mismatch,
            validator_mismatch,
            validator_external,
        ) = self._build_form(current, bon)
        return self._render(
            request, bon, current, form, idx, total, matched_name, name_mismatch,
            doc_type, expense_mismatch, validator_mismatch, validator_external,
        )

    def post(self, request, pk):
        bon = self._get_bon(request, pk)
        receipts, current, idx, total = self._get_receipt_and_index(bon, request)
        if not current:
            return redirect(reverse("bons:list"))

        prefix = f"receipt_{current.pk}"
        form = OcrReviewForm(data=request.POST, prefix=prefix)
        # Populate querysets so validation works
        from budget.models import SubBudget
        members_qs = _get_house_members_queryset(bon.house)
        form.fields["purchaser_member"].queryset = members_qs
        form.fields["expense_member"].queryset = members_qs
        form.fields["validator_member"].queryset = members_qs
        form.fields["sub_budget"].queryset = SubBudget.objects.filter(
            budget_year=bon.budget_year, is_active=True
        ).order_by("sort_order", "trace_code")

        if not form.is_valid():
            doc_type = request.POST.get(f"{prefix}-document_type", "receipt")
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
            return self._render(request, bon, current, form, idx, total, "", False, doc_type)

        # Save confirmed values
        selected_member = form.cleaned_data.get("purchaser_member")
        ef, _ = ReceiptExtractedFields.objects.get_or_create(receipt_file=current)
        ef.final_document_type = form.cleaned_data.get("document_type") or ""
        ef.final_bc_number = form.cleaned_data.get("bc_number") or ""
        ef.final_associated_bc_number = form.cleaned_data.get("associated_bc_number") or ""
        ef.final_supplier_name = form.cleaned_data.get("supplier_name") or ""
        ef.final_supplier_address = form.cleaned_data.get("supplier_address") or ""
        _save_paper_bc_extracted_fields(ef, form, bon.house)
        ef.final_member_name = selected_member.display_name if selected_member else ""
        ef.final_apartment_number = form.cleaned_data.get("apartment_number") or ""
        ef.final_merchant = form.cleaned_data.get("merchant_name") or ""
        ef.final_purchase_date = form.cleaned_data.get("purchase_date")
        ef.final_subtotal = form.cleaned_data.get("subtotal")
        ef.final_tps = form.cleaned_data.get("tps")
        ef.final_tvq = form.cleaned_data.get("tvq")
        ef.final_total = form.cleaned_data.get("total")
        ef.final_summary = form.cleaned_data.get("summary") or ""
        ef.sub_budget = form.cleaned_data.get("sub_budget")
        ef.confirmed_by = request.user
        ef.confirmed_at = timezone.now()
        ef.save()

        # Auto-save merchant to Merchant table (dedup by name)
        merchant_name = (form.cleaned_data.get("merchant_name") or "").strip()
        if merchant_name:
            from .models import Merchant
            Merchant.objects.get_or_create(name=merchant_name)

        if current.ocr_status in (OcrStatus.EXTRACTED, OcrStatus.NOT_REQUESTED, OcrStatus.FAILED):
            current.ocr_status = OcrStatus.CORRECTED
            current.save(update_fields=["ocr_status"])

        # If there are more receipts, go to next
        if idx + 1 < total:
            return redirect(
                reverse("bons:review", kwargs={"pk": bon.pk}) + f"?idx={idx + 1}"
            )

        # All receipts reviewed — finalize: group by member and create bons
        return self._finalize_bons(request, bon)

    def _finalize_bons(self, request, scan_session):
        """Group reviewed receipts, create NEW bons, archive scan session.

        Paper BCs are grouped by BC number with their associated invoices.
        Regular receipts are grouped by apartment (member).
        """
        receipts = list(scan_session.receipt_files.order_by("created_at", "pk"))
        house = scan_session.house

        # Separate documents by type
        paper_bc_receipts = {}   # bc_number -> receipt
        invoice_receipts = {}    # associated_bc_number -> [receipts]
        regular_receipts = []

        for receipt in receipts:
            try:
                ef = receipt.extracted_fields
                doc_type = ef.final_document_type or ef.document_type_candidate or "receipt"
            except ReceiptExtractedFields.DoesNotExist:
                doc_type = "receipt"
                ef = None

            if doc_type == "paper_bc" and ef:
                bc_num = ef.final_bc_number or ef.bc_number_candidate
                if bc_num:
                    paper_bc_receipts[bc_num] = receipt
                else:
                    regular_receipts.append(receipt)
            elif doc_type == "invoice" and ef:
                assoc = ef.final_associated_bc_number or ef.associated_bc_number_candidate
                if assoc:
                    invoice_receipts.setdefault(assoc, []).append(receipt)
                else:
                    regular_receipts.append(receipt)
            else:
                regular_receipts.append(receipt)

        created_bons = []
        duplicate_warnings = []

        # --- Paper BC bons ---
        for bc_number, bc_receipt in paper_bc_receipts.items():
            matched_invoices = invoice_receipts.pop(bc_number, [])
            all_docs = [bc_receipt] + matched_invoices

            # ── Paper BC duplicate detection ──
            existing_paper_bc = (
                BonDeCommande.objects
                .filter(
                    house=house,
                    is_paper_bc=True,
                    paper_bc_number=bc_number,
                )
                .exclude(status=BonStatus.VOID)
                .first()
            )

            if existing_paper_bc:
                new_total = _aggregate_receipt_amounts(
                    all_docs,
                    prefer_invoice_totals=True,
                )["total"]

                if new_total is not None and existing_paper_bc.total == new_total:
                    # Same BC number AND same total → already added
                    messages.error(
                        request,
                        f"Le BC papier n°{bc_number} ({new_total} $) a déjà été "
                        f"enregistré (BC {existing_paper_bc.number}). "
                        f"Ce doublon n'a pas été créé."
                    )
                    continue  # skip creating this bon
                else:
                    # Same BC number, different total → warn but create
                    duplicate_warnings.append({
                        "bc_number": bc_number,
                        "existing_bon": existing_paper_bc,
                        "existing_total": existing_paper_bc.total,
                        "new_total": new_total,
                    })
                    messages.warning(
                        request,
                        f"⚠️ Le BC papier n°{bc_number} existe déjà "
                        f"(ancien total : {existing_paper_bc.total} $, "
                        f"nouveau total : {new_total} $). "
                        f"Veuillez vérifier les montants."
                    )

            # Handle unique number constraint
            bon_number = bc_number
            existing = BonDeCommande.objects.filter(number=bon_number).first()
            if existing:
                if existing.status == BonStatus.VOID:
                    BonDeCommande.objects.filter(pk=existing.pk).update(
                        number=f"_VOID_{existing.pk}_{bon_number}"
                    )
                else:
                    suffix = 2
                    while BonDeCommande.objects.filter(number=f"{bc_number}-{suffix}").exists():
                        suffix += 1
                    bon_number = f"{bc_number}-{suffix}"

            bon = BonDeCommande()
            bon.house = house
            bon.budget_year = scan_session.budget_year
            bon.number = bon_number
            bon.purchase_date = timezone.now().date()
            bon.short_description = "(en cours de création)"
            bon.total = 0
            bon.sub_budget = scan_session.sub_budget
            bon.purchaser_member = scan_session.purchaser_member
            bon.created_by = scan_session.created_by
            bon.status = BonStatus.READY_FOR_VALIDATION
            bon.is_scan_session = False
            bon.is_paper_bc = True
            bon.paper_bc_number = bc_number
            bon.entered_date = timezone.now().date()
            super(BonDeCommande, bon).save()

            for r in all_docs:
                r.bon_de_commande = bon
                r.save(update_fields=["bon_de_commande_id"])

            self._fill_bon_from_receipts(bon, all_docs, is_paper_bc=True)

            # Run digital invoice duplicate detection on each receipt
            from .ocr_service import DuplicateDetectionService
            for r in all_docs:
                DuplicateDetectionService.check_and_flag_duplicates(r, house)

            created_bons.append(bon)

        # --- Orphan invoices (no matching paper BC) → treat as regular ---
        for assoc_bc, inv_list in invoice_receipts.items():
            regular_receipts.extend(inv_list)

        # --- Regular receipt bons (group by apartment) ---
        apt_groups = defaultdict(list)
        for receipt in regular_receipts:
            try:
                ef = receipt.extracted_fields
                apt = ef.final_apartment_number or "unknown"
            except ReceiptExtractedFields.DoesNotExist:
                apt = "unknown"
            apt_groups[apt].append(receipt)

        for apt_code, group_receipts in apt_groups.items():
            bon = BonDeCommande()
            bon.house = house
            bon.budget_year = scan_session.budget_year
            bon.number = generate_bon_number(house, scan_session.budget_year.year)
            bon.purchase_date = timezone.now().date()
            bon.short_description = "(en cours de création)"
            bon.total = 0
            bon.sub_budget = scan_session.sub_budget
            bon.purchaser_member = scan_session.purchaser_member
            bon.created_by = scan_session.created_by
            bon.status = BonStatus.READY_FOR_VALIDATION
            bon.is_scan_session = False
            bon.entered_date = timezone.now().date()
            super(BonDeCommande, bon).save()

            for r in group_receipts:
                r.bon_de_commande = bon
                r.save(update_fields=["bon_de_commande_id"])

            self._fill_bon_from_receipts(bon, group_receipts)

            # Run duplicate detection on each receipt
            from .ocr_service import DuplicateDetectionService
            for r in group_receipts:
                DuplicateDetectionService.check_and_flag_duplicates(r, house)

            created_bons.append(bon)

        # Archive the scan session
        BonDeCommande.objects.filter(pk=scan_session.pk).update(
            status=BonStatus.VOID,
            void_reason="Session de scan terminée — bons créés",
            voided_at=timezone.now(),
        )

        if len(created_bons) == 0:
            messages.warning(request, "Aucun bon de commande créé.")
            return redirect(reverse("bons:list"))
        elif len(created_bons) == 1:
            messages.success(request, "Bon de commande créé. Vérifiez les détails avant validation.")
            return redirect(reverse("bons:detail", kwargs={"pk": created_bons[0].pk}))
        else:
            bon_numbers = ", ".join(b.number for b in created_bons)
            messages.success(
                request,
                f"{len(created_bons)} bons de commande créés ({bon_numbers}). "
                f"Vérifiez chacun avant validation.",
            )
            return redirect(reverse("bons:scan-complete") + "?bons=" + ",".join(str(b.pk) for b in created_bons))

    def _fill_bon_from_receipts(self, bon, receipts, is_paper_bc=False):
        """Pre-fill bon fields from its confirmed receipt data."""
        update_fields = {}
        first_ef = None
        for r in receipts:
            try:
                first_ef = r.extracted_fields
                break
            except ReceiptExtractedFields.DoesNotExist:
                continue

        if is_paper_bc:
            # Extract supplier info from the paper_bc document
            for r in receipts:
                try:
                    ef = r.extracted_fields
                    if (ef.final_document_type or ef.document_type_candidate) == "paper_bc":
                        if ef.final_supplier_name or ef.supplier_name_candidate:
                            update_fields["supplier_name"] = ef.final_supplier_name or ef.supplier_name_candidate
                        if ef.final_purchase_date or ef.purchase_date_candidate:
                            update_fields["purchase_date"] = ef.final_purchase_date or ef.purchase_date_candidate
                        if ef.sub_budget_id:
                            update_fields["sub_budget_id"] = ef.sub_budget_id
                        purchaser_name = ef.final_expense_member_name or ef.expense_member_name_candidate
                        purchaser_apt_code = ef.final_expense_apartment or ef.expense_apartment_candidate
                        validator_name = ef.final_validator_member_name or ef.validator_member_name_candidate
                        validator_apt_code = ef.final_validator_apartment or ef.validator_apartment_candidate

                        purchaser_apartment, purchaser_member = _resolve_signer_assignment(
                            bon.house, purchaser_apt_code, purchaser_name,
                        )
                        if purchaser_apartment:
                            update_fields["purchaser_apartment_id"] = purchaser_apartment.pk
                        if purchaser_member:
                            update_fields["purchaser_member_id"] = purchaser_member.pk

                        validator_apartment, validator_member = _resolve_signer_assignment(
                            bon.house, validator_apt_code, validator_name,
                        )
                        if validator_member and purchaser_member and validator_member.pk == purchaser_member.pk:
                            validator_member = None
                            validator_apartment = None
                        update_fields["approver_member_id"] = validator_member.pk if validator_member else None
                        update_fields["approver_apartment_id"] = validator_apartment.pk if validator_apartment else None

                        # External supplier: name exists but no member match
                        is_external = bool(
                            validator_name
                            and validator_name.upper() != "ILLISIBLE"
                            and not validator_member
                        )
                        update_fields["approver_is_external"] = is_external
                        if is_external:
                            update_fields["approver_name_snapshot"] = validator_name.strip()
                        break
                except ReceiptExtractedFields.DoesNotExist:
                    continue
        elif first_ef:
            if first_ef.final_merchant:
                update_fields["merchant_name"] = first_ef.final_merchant
            if first_ef.final_purchase_date:
                update_fields["purchase_date"] = first_ef.final_purchase_date
            if first_ef.sub_budget_id:
                update_fields["sub_budget_id"] = first_ef.sub_budget_id

        # Build short_description from receipt summaries
        summaries = []
        for r in receipts:
            try:
                ef = r.extracted_fields
                s = ef.final_summary or ef.summary_candidate
                if s:
                    summaries.append(s)
            except ReceiptExtractedFields.DoesNotExist:
                continue
        if summaries:
            update_fields["short_description"] = "; ".join(summaries)[:255]

        aggregated_amounts = _aggregate_receipt_amounts(
            receipts,
            prefer_invoice_totals=is_paper_bc,
        )
        if aggregated_amounts["total"] is not None:
            update_fields["total"] = aggregated_amounts["total"]
        if aggregated_amounts["subtotal"] is not None:
            update_fields["subtotal"] = aggregated_amounts["subtotal"]
        if aggregated_amounts["tps"] is not None:
            update_fields["tps"] = aggregated_amounts["tps"]
        if aggregated_amounts["tvq"] is not None:
            update_fields["tvq"] = aggregated_amounts["tvq"]

        # Flag when paper BC has invoices but invoice amounts couldn't be extracted
        if is_paper_bc and not aggregated_amounts.get("using_invoices", True):
            has_invoices = any(
                _extracted_document_type(ef) == "invoice"
                for r in receipts
                for ef in [_safe_extracted_fields(r)]
                if ef is not None
            )
            if has_invoices:
                update_fields["invoice_amounts_unverified"] = True
            else:
                update_fields["invoice_amounts_unverified"] = False
        elif is_paper_bc:
            update_fields["invoice_amounts_unverified"] = False

        # Auto-match apartment → member from first receipt (skip for paper BC)
        if not is_paper_bc:
            apt_code = first_ef.final_apartment_number if first_ef else ""
            if apt_code:
                apartment, member = _match_member_for_apartment(bon.house, apt_code)
                if apartment:
                    update_fields["purchaser_apartment_id"] = apartment.pk
                if member:
                    update_fields["purchaser_member_id"] = member.pk

        if update_fields:
            BonDeCommande.objects.filter(pk=bon.pk).update(**update_fields)
            bon.refresh_from_db()

    def _sync_existing_bon_paper_bc_data(self, bon):
        """Apply reviewed paper BC metadata to an existing bon."""
        receipts = list(bon.receipt_files.order_by("created_at", "pk"))
        paper_bc_number = ""
        has_paper_bc = False
        for receipt in receipts:
            try:
                ef = receipt.extracted_fields
            except ReceiptExtractedFields.DoesNotExist:
                continue
            if (ef.final_document_type or ef.document_type_candidate) == "paper_bc":
                has_paper_bc = True
                paper_bc_number = ef.final_bc_number or ef.bc_number_candidate or ""
                break
        if not has_paper_bc:
            return
        self._fill_bon_from_receipts(bon, receipts, is_paper_bc=True)
        BonDeCommande.objects.filter(pk=bon.pk).update(
            is_paper_bc=True,
            paper_bc_number=paper_bc_number,
        )

    def _render(self, request, bon, receipt, form, idx, total, matched_member_name, name_mismatch=False, document_type="receipt", expense_member_mismatch=False, validator_member_mismatch=False, validator_is_external=False):
        mismatch_warning = _get_mismatch_warning(receipt)
        return TemplateResponse(request, self.template_name, {
            "bon": bon,
            "receipt": receipt,
            "form": form,
            "current_idx": idx,
            "total_receipts": total,
            "is_last": idx + 1 >= total,
            "matched_member_name": matched_member_name,
            "name_mismatch": name_mismatch,
            "expense_member_mismatch": expense_member_mismatch,
            "validator_member_mismatch": validator_member_mismatch,
            "validator_is_external": validator_is_external,
            "document_type": document_type,
            "mismatch_warning": mismatch_warning,
            "signer_roles_ambiguous": bool(form["signer_roles_ambiguous"].value()),
            "prev_url": (
                reverse("bons:review", kwargs={"pk": bon.pk}) + f"?idx={idx - 1}"
                if idx > 0 else None
            ),
        })


# ---------------------------------------------------------------------------
# OCR Workflow — Step 3: Complete bon details (edit pre-filled form)
# ---------------------------------------------------------------------------

class BonCompleteView(TreasurerRequiredMixin, UpdateView):
    """Step 3: Complete the bon with pre-filled OCR data."""
    model = BonDeCommande
    form_class = BonDeCommandeForm
    template_name = "bons/form.html"

    def get_queryset(self):
        qs = super().get_queryset()
        qs = _filter_by_house(qs, self.request.user)
        return qs.filter(status__in=[BonStatus.DRAFT, BonStatus.READY_FOR_REVIEW])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["house"] = self.object.house
        return kwargs

    def form_valid(self, form):
        bon = form.instance
        bon.status = BonStatus.READY_FOR_VALIDATION
        response = super().form_valid(form)
        messages.success(self.request, f"Le bon {bon.number} est prêt pour validation.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["is_create"] = False
        ctx["is_complete_step"] = True
        return ctx

    def get_success_url(self):
        return reverse("bons:detail", kwargs={"pk": self.object.pk})


# ---------------------------------------------------------------------------
# Export views: PDF and XLSX
# ---------------------------------------------------------------------------

def _bon_is_export_ready(bon):
    """A bon is export-ready when all its receipts have been reviewed (EXTRACTED or CORRECTED)."""
    receipts = bon.receipt_files.all()
    if not receipts.exists():
        return True  # no receipts = nothing to review
    return not receipts.exclude(
        ocr_status__in=[OcrStatus.EXTRACTED, OcrStatus.CORRECTED]
    ).exists()


class BonExportPdfView(RoleRequiredMixin, View):
    """Export a bon de commande as PDF with attached receipts."""
    min_role = 10  # VIEWER — any logged-in user can download

    def get(self, request, pk):
        from django.http import HttpResponse
        from .pdf_service import generate_bon_pdf
        bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user), pk=pk
        )
        if not _bon_is_export_ready(bon):
            messages.error(
                request,
                "L'exportation n'est pas disponible : tous les reçus doivent "
                "être révisés avant de pouvoir exporter ce bon de commande."
            )
            return redirect(reverse("bons:detail", kwargs={"pk": pk}))
        pdf_bytes = generate_bon_pdf(bon)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="BC_{bon.number}.pdf"'
        return response


class BonExportXlsxView(RoleRequiredMixin, View):
    """Export a bon de commande as XLSX."""
    min_role = 10

    def get(self, request, pk):
        from django.http import HttpResponse
        from .pdf_service import generate_bon_xlsx
        bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user), pk=pk
        )
        if not _bon_is_export_ready(bon):
            messages.error(
                request,
                "L'exportation n'est pas disponible : tous les reçus doivent "
                "être révisés avant de pouvoir exporter ce bon de commande."
            )
            return redirect(reverse("bons:detail", kwargs={"pk": pk}))
        xlsx_bytes = generate_bon_xlsx(bon)
        response = HttpResponse(
            xlsx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="BC_{bon.number}.xlsx"'
        return response


# ---------------------------------------------------------------------------
# Duplicate flag resolution
# ---------------------------------------------------------------------------

class ResolveDuplicateFlagView(TreasurerRequiredMixin, View):
    """Resolve a duplicate flag: dismiss or confirm."""

    def post(self, request, flag_pk):
        from .models import DuplicateFlag, DuplicateFlagStatus

        flag = get_object_or_404(DuplicateFlag, pk=flag_pk)
        # Verify user has access to the bon
        bon = flag.receipt_file.bon_de_commande
        _filter_by_house(BonDeCommande.objects.filter(pk=bon.pk), request.user).get()

        action = request.POST.get("action")
        if action == "dismiss":
            flag.status = DuplicateFlagStatus.DISMISSED
            flag.resolved_at = timezone.now()
            flag.resolved_by = request.user
            flag.save(update_fields=["status", "resolved_at", "resolved_by"])
            messages.success(request, "Le signalement de doublon a été rejeté.")
        elif action == "confirm":
            flag.status = DuplicateFlagStatus.CONFIRMED_DUPLICATE
            flag.resolved_at = timezone.now()
            flag.resolved_by = request.user
            flag.save(update_fields=["status", "resolved_at", "resolved_by"])
            messages.warning(request, "Le doublon a été confirmé. Il sera indiqué dans les exportations.")
        else:
            messages.error(request, "Action invalide.")

        return redirect(reverse("bons:detail", kwargs={"pk": bon.pk}))


# ---------------------------------------------------------------------------
# Search view
# ---------------------------------------------------------------------------

class BonSearchView(RoleRequiredMixin, View):
    """Powerful search across all bons de commande. Available to all members."""
    min_role = 10  # VIEWER
    template_name = "bons/search.html"

    def get(self, request):
        from django.db.models import Q
        form = BonSearchForm(request.GET or None, user=request.user)
        results = None
        total_amount = None

        if request.GET and form.is_valid():
            qs = BonDeCommande.objects.select_related(
                "house", "budget_year", "sub_budget", "purchaser_member"
            ).filter(is_scan_session=False).order_by("-purchase_date", "-created_at")

            cd = form.cleaned_data

            # Free text search
            if cd.get("q"):
                q = cd["q"]
                qs = qs.filter(
                    Q(number__icontains=q)
                    | Q(short_description__icontains=q)
                    | Q(merchant_name__icontains=q)
                    | Q(supplier_name__icontains=q)
                    | Q(purchaser_name_snapshot__icontains=q)
                    | Q(notes__icontains=q)
                )

            if cd.get("house"):
                qs = qs.filter(house=cd["house"])
            if cd.get("budget_year"):
                qs = qs.filter(budget_year=cd["budget_year"])
            if cd.get("purchaser"):
                qs = qs.filter(purchaser_member=cd["purchaser"])
            if cd.get("merchant"):
                qs = qs.filter(merchant_name__icontains=cd["merchant"])
            if cd.get("amount_min") is not None:
                qs = qs.filter(total__gte=cd["amount_min"])
            if cd.get("amount_max") is not None:
                qs = qs.filter(total__lte=cd["amount_max"])
            if cd.get("date_from"):
                qs = qs.filter(purchase_date__gte=cd["date_from"])
            if cd.get("date_to"):
                qs = qs.filter(purchase_date__lte=cd["date_to"])
            if cd.get("status"):
                qs = qs.filter(status=cd["status"])

            results = qs[:200]
            from django.db.models import Sum
            agg = qs.aggregate(total_sum=Sum("total"))
            total_amount = agg["total_sum"]

        return TemplateResponse(request, self.template_name, {
            "form": form,
            "results": results,
            "total_amount": total_amount,
            "result_count": len(results) if results is not None else None,
        })


# ---------------------------------------------------------------------------
# Scan session: pending scans & completion summary
# ---------------------------------------------------------------------------

class PendingScanSessionsView(TreasurerRequiredMixin, ListView):
    """List scan sessions that have not been finalized yet."""
    template_name = "bons/pending_scans.html"
    context_object_name = "sessions"

    def get_queryset(self):
        qs = BonDeCommande.objects.filter(
            is_scan_session=True,
            status__in=[BonStatus.DRAFT, BonStatus.READY_FOR_REVIEW],
        ).select_related("budget_year", "house", "created_by")
        return _filter_by_house(qs, self.request.user)


class ScanCompleteView(TreasurerRequiredMixin, View):
    """Summary page after scan session creates multiple bons."""
    template_name = "bons/scan_complete.html"

    def get(self, request):
        bon_pks = request.GET.get("bons", "").split(",")
        bon_pks = [pk.strip() for pk in bon_pks if pk.strip().isdigit()]
        bons = BonDeCommande.objects.filter(
            pk__in=bon_pks, is_scan_session=False
        ).select_related("budget_year", "sub_budget", "purchaser_member")
        return TemplateResponse(request, self.template_name, {"bons": bons})


# ---------------------------------------------------------------------------
# Bon deletion (non-reimbursed only)
# ---------------------------------------------------------------------------

class BonDeleteView(TreasurerRequiredMixin, View):
    """Archive (soft-delete) a bon that has NOT been reimbursed."""

    def post(self, request, pk):
        bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user),
            pk=pk,
        )
        if bon.status == BonStatus.REIMBURSED:
            messages.error(request, "Impossible de supprimer un bon déjà remboursé.")
            return redirect(reverse("bons:detail", kwargs={"pk": pk}))

        # Delete associated expense if any
        from budget.models import Expense
        Expense.objects.filter(bon_de_commande=bon).delete()

        bon.status = BonStatus.VOID
        bon.void_reason = f"Supprimé par {request.user.get_username()}"
        bon.voided_at = timezone.now()
        super(BonDeCommande, bon).save()

        messages.success(request, f"Le bon {bon.number} a été supprimé.")
        return redirect(reverse("bons:list"))


# Needed for imports in urls.py — avoid circular import of BudgetYear
from budget.models import BudgetYear
