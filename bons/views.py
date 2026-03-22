from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View
from django.views.generic.edit import FormView
from django.contrib import messages
from django.template.response import TemplateResponse
from collections import defaultdict
import json

from accounts.access import RoleRequiredMixin, TreasurerRequiredMixin, check_house_permission
from .models import (
    BonDeCommande, BonStatus, ReceiptFile, ReceiptExtractedFields, OcrStatus,
)
from .forms import (
    BonDeCommandeForm, ReceiptUploadForm, BonValidateForm,
    MultiReceiptUploadForm, OcrReviewForm, BonSearchForm,
)
from .services import generate_bon_number


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
            "approver_member", "created_by", "validated_by",
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["receipts"] = self.object.receipt_files.all()
        ctx["can_manage"] = (
            self.request.user.is_authenticated
            and self.request.user.can_manage_financials
        )
        ctx["upload_form"] = ReceiptUploadForm()
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

    def get_success_url(self):
        return reverse("bons:detail", kwargs={"pk": self.object.pk})


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
                supplier_name=bon.merchant_name or "",
                spent_by_label=(
                    f"{bon.purchaser_member.display_name}"
                    if bon.purchaser_member else bon.purchaser_name_snapshot or "—"
                ),
                amount=bon.total,
                source_type=ExpenseSourceType.BON_DE_COMMANDE,
                entered_by=self.request.user,
            )

        messages.success(self.request, f"Le bon {bon.number} a été validé.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse("bons:detail", kwargs={"pk": self.bon.pk})


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
            _, err = ReceiptOcrService.process_receipts_batch([receipt])
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
        from members.models import Member, Residency
        from budget.models import SubBudget

        initial = {}
        matched_member_name = ""
        name_mismatch = False

        try:
            ef = receipt.extracted_fields
            apt_code = ef.final_apartment_number or ef.apartment_number_candidate
            member_name_raw = ef.final_member_name or ef.member_name_candidate

            member = None
            if apt_code:
                _, member = _match_member_for_apartment(bon.house, apt_code)
                if member:
                    matched_member_name = member.display_name
                    initial["matched_member_id"] = member.pk
                    initial["purchaser_member"] = member.pk

            name_unreadable = not member_name_raw or member_name_raw.upper() == "ILLISIBLE"
            if member and member_name_raw and not name_unreadable:
                if member_name_raw.lower().strip() not in member.display_name.lower():
                    name_mismatch = True

            initial.update({
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

        house_member_ids = Residency.objects.filter(
            apartment__house=bon.house, end_date__isnull=True,
        ).values_list("member_id", flat=True)
        form.fields["purchaser_member"].queryset = Member.objects.filter(
            pk__in=house_member_ids, is_active=True,
        ).order_by("last_name", "first_name")
        form.fields["sub_budget"].queryset = SubBudget.objects.filter(
            budget_year=bon.budget_year, is_active=True,
        ).order_by("sort_order", "trace_code")

        return {
            "bon": bon,
            "receipt": receipt,
            "form": form,
            "matched_member_name": matched_member_name,
            "name_mismatch": name_mismatch,
        }

    def get(self, request, bon_pk, receipt_pk):
        bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user), pk=bon_pk,
        )
        receipt = get_object_or_404(ReceiptFile, pk=receipt_pk, bon_de_commande=bon)
        ctx = self._get_context(request, bon, receipt)
        return TemplateResponse(request, self.template_name, ctx)

    def post(self, request, bon_pk, receipt_pk):
        from members.models import Member, Residency
        from budget.models import SubBudget

        bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user), pk=bon_pk,
        )
        receipt = get_object_or_404(ReceiptFile, pk=receipt_pk, bon_de_commande=bon)

        prefix = f"receipt_{receipt.pk}"
        form = OcrReviewForm(data=request.POST, prefix=prefix)
        house_member_ids = Residency.objects.filter(
            apartment__house=bon.house, end_date__isnull=True,
        ).values_list("member_id", flat=True)
        form.fields["purchaser_member"].queryset = Member.objects.filter(
            pk__in=house_member_ids, is_active=True,
        ).order_by("last_name", "first_name")
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
            _, err = ReceiptOcrService.process_receipts_batch(receipt_objs)
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
    """Check if paper BC total matches invoice totals."""
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
        bc_total = float(paper_bcs[0].get("total") or 0)
        invoice_total = sum(float(d.get("total") or 0) for d in invoices)

        if abs(bc_total - invoice_total) > 0.01:
            return {
                "bc_total": bc_total,
                "invoice_total": invoice_total,
                "bc_number": paper_bcs[0].get("bc_number", "?"),
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
        from members.models import Member, Residency
        from budget.models import SubBudget

        prefix = f"receipt_{receipt.pk}"
        initial = {}
        matched_member_name = ""
        name_mismatch = False
        doc_type = "receipt"

        try:
            ef = receipt.extracted_fields
            doc_type = ef.final_document_type or ef.document_type_candidate or "receipt"
            initial["document_type"] = doc_type
            initial["bc_number"] = ef.final_bc_number or ef.bc_number_candidate or ""
            initial["associated_bc_number"] = ef.final_associated_bc_number or ef.associated_bc_number_candidate or ""
            initial["supplier_name"] = ef.final_supplier_name or ef.supplier_name_candidate or ""
            initial["supplier_address"] = ef.final_supplier_address or ef.supplier_address_candidate or ""

            apt_code = ef.final_apartment_number or ef.apartment_number_candidate
            member_name_raw = ef.final_member_name or ef.member_name_candidate

            # Only do member matching for receipts
            if doc_type == "receipt":
                member = None
                if apt_code:
                    apartment, member = _match_member_for_apartment(bon.house, apt_code)
                    if member:
                        matched_member_name = member.display_name
                        initial["matched_member_id"] = member.pk
                        initial["purchaser_member"] = member.pk

                name_unreadable = not member_name_raw or member_name_raw.upper() == "ILLISIBLE"
                if member and member_name_raw and not name_unreadable:
                    if member_name_raw.lower().strip() not in member.display_name.lower():
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
        house_member_ids = Residency.objects.filter(
            apartment__house=bon.house, end_date__isnull=True
        ).values_list("member_id", flat=True)
        form.fields["purchaser_member"].queryset = Member.objects.filter(
            pk__in=house_member_ids, is_active=True
        ).order_by("last_name", "first_name")

        # Populate sub_budget choices
        form.fields["sub_budget"].queryset = SubBudget.objects.filter(
            budget_year=bon.budget_year, is_active=True
        ).order_by("sort_order", "trace_code")

        return form, matched_member_name, name_mismatch, doc_type

    def get(self, request, pk):
        bon = self._get_bon(request, pk)
        receipts, current, idx, total = self._get_receipt_and_index(bon, request)
        if not current:
            messages.warning(request, "Aucun reçu à vérifier.")
            return redirect(reverse("bons:list"))
        form, matched_name, name_mismatch, doc_type = self._build_form(current, bon)
        return self._render(request, bon, current, form, idx, total, matched_name, name_mismatch, doc_type)

    def post(self, request, pk):
        bon = self._get_bon(request, pk)
        receipts, current, idx, total = self._get_receipt_and_index(bon, request)
        if not current:
            return redirect(reverse("bons:list"))

        prefix = f"receipt_{current.pk}"
        form = OcrReviewForm(data=request.POST, prefix=prefix)
        # Populate querysets so validation works
        from budget.models import SubBudget
        from members.models import Member, Residency
        house_member_ids = Residency.objects.filter(
            apartment__house=bon.house, end_date__isnull=True
        ).values_list("member_id", flat=True)
        form.fields["purchaser_member"].queryset = Member.objects.filter(
            pk__in=house_member_ids, is_active=True
        ).order_by("last_name", "first_name")
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

        # --- Paper BC bons ---
        for bc_number, bc_receipt in paper_bc_receipts.items():
            matched_invoices = invoice_receipts.pop(bc_number, [])
            all_docs = [bc_receipt] + matched_invoices

            # Handle unique number constraint: reassign VOID bon number or add suffix
            bon_number = bc_number
            existing = BonDeCommande.objects.filter(number=bon_number).first()
            if existing:
                if existing.status == BonStatus.VOID:
                    # NonDestructiveModel prevents delete — reassign its number
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

        # Sum totals across all receipts
        from decimal import Decimal
        total_subtotal = Decimal("0")
        total_tps = Decimal("0")
        total_tvq = Decimal("0")
        total_total = Decimal("0")
        has_amounts = False

        for r in receipts:
            try:
                ef = r.extracted_fields
                if ef.final_total is not None:
                    total_total += ef.final_total
                    has_amounts = True
                if ef.final_subtotal is not None:
                    total_subtotal += ef.final_subtotal
                if ef.final_tps is not None:
                    total_tps += ef.final_tps
                if ef.final_tvq is not None:
                    total_tvq += ef.final_tvq
            except ReceiptExtractedFields.DoesNotExist:
                continue

        if has_amounts:
            update_fields["total"] = total_total
            if total_subtotal:
                update_fields["subtotal"] = total_subtotal
            if total_tps:
                update_fields["tps"] = total_tps
            if total_tvq:
                update_fields["tvq"] = total_tvq

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

    def _render(self, request, bon, receipt, form, idx, total, matched_member_name, name_mismatch=False, document_type="receipt"):
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
            "document_type": document_type,
            "mismatch_warning": mismatch_warning,
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

class BonExportPdfView(RoleRequiredMixin, View):
    """Export a bon de commande as PDF with attached receipts."""
    min_role = 10  # VIEWER — any logged-in user can download

    def get(self, request, pk):
        from django.http import HttpResponse
        from .pdf_service import generate_bon_pdf
        bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user), pk=pk
        )
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
        xlsx_bytes = generate_bon_xlsx(bon)
        response = HttpResponse(
            xlsx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="BC_{bon.number}.xlsx"'
        return response


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
