from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View
from django.views.generic.edit import FormView
from django.contrib import messages
from django.template.response import TemplateResponse
from collections import defaultdict

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


class BonListView(ListView):
    model = BonDeCommande
    template_name = "bons/list.html"
    context_object_name = "bons"
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().select_related(
            "budget_year", "sub_budget", "purchaser_member"
        )

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


class BonDetailView(DetailView):
    model = BonDeCommande
    template_name = "bons/detail.html"
    context_object_name = "bon"

    def get_queryset(self):
        return super().get_queryset().select_related(
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
        return qs.filter(status__in=[BonStatus.DRAFT, BonStatus.READY_FOR_REVIEW])

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
        bon.status = BonStatus.VALIDATED
        bon.validated_by = self.request.user
        bon.validated_at = timezone.now()
        bon.refresh_snapshot_fields()
        bon.save()
        messages.success(self.request, f"Le bon {bon.number} a été validé.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse("bons:detail", kwargs={"pk": self.bon.pk})


class ReceiptUploadToExistingView(TreasurerRequiredMixin, View):
    """Upload a receipt to an existing bon (from the detail page)."""

    def post(self, request, bon_pk):
        bon = get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user),
            pk=bon_pk,
        )
        form = ReceiptUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = form.cleaned_data["file"]
            ReceiptFile.objects.create(
                bon_de_commande=bon,
                file=uploaded,
                original_filename=uploaded.name,
                content_type=getattr(uploaded, "content_type", ""),
                uploaded_by=request.user,
            )
            messages.success(request, "Reçu téléversé avec succès.")
        else:
            messages.error(request, "Erreur lors du téléversement du fichier.")
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

        try:
            ef = receipt.extracted_fields
            apt_code = ef.final_apartment_number or ef.apartment_number_candidate
            member_name_raw = ef.final_member_name or ef.member_name_candidate

            # Auto-match: apartment → member from DB
            member = None
            if apt_code:
                apartment, member = _match_member_for_apartment(bon.house, apt_code)
                if member:
                    matched_member_name = member.display_name
                    initial["matched_member_id"] = member.pk
                    initial["purchaser_member"] = member.pk

            # Check name mismatch
            if member and member_name_raw and member_name_raw.upper() != "ILLISIBLE":
                if member_name_raw.lower().strip() not in member.display_name.lower():
                    name_mismatch = True

            initial.update({
                "member_name_raw": member_name_raw or "",
                "apartment_number": apt_code,
                "merchant_name": ef.final_merchant or ef.merchant_candidate,
                "purchase_date": ef.final_purchase_date or ef.purchase_date_candidate,
                "subtotal": ef.final_subtotal if ef.final_subtotal is not None else ef.subtotal_candidate,
                "tps": ef.final_tps if ef.final_tps is not None else ef.tps_candidate,
                "tvq": ef.final_tvq if ef.final_tvq is not None else ef.tvq_candidate,
                "total": ef.final_total if ef.final_total is not None else ef.total_candidate,
            })
        except ReceiptExtractedFields.DoesNotExist:
            pass

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
        ).order_by("name")

        return form, matched_member_name, name_mismatch

    def get(self, request, pk):
        bon = self._get_bon(request, pk)
        receipts, current, idx, total = self._get_receipt_and_index(bon, request)
        if not current:
            messages.warning(request, "Aucun reçu à vérifier.")
            return redirect(reverse("bons:list"))
        form, matched_name, name_mismatch = self._build_form(current, bon)
        return self._render(request, bon, current, form, idx, total, matched_name, name_mismatch)

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
        ).order_by("name")

        if not form.is_valid():
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
            return self._render(request, bon, current, form, idx, total, "", False)

        # Save confirmed values
        selected_member = form.cleaned_data.get("purchaser_member")
        ef, _ = ReceiptExtractedFields.objects.get_or_create(receipt_file=current)
        ef.final_member_name = selected_member.display_name if selected_member else ""
        ef.final_apartment_number = form.cleaned_data.get("apartment_number") or ""
        ef.final_merchant = form.cleaned_data.get("merchant_name") or ""
        ef.final_purchase_date = form.cleaned_data.get("purchase_date")
        ef.final_subtotal = form.cleaned_data.get("subtotal")
        ef.final_tps = form.cleaned_data.get("tps")
        ef.final_tvq = form.cleaned_data.get("tvq")
        ef.final_total = form.cleaned_data.get("total")
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

    def _finalize_bons(self, request, original_bon):
        """Group reviewed receipts by member, create separate bons if needed."""
        from budget.models import SubBudget
        receipts = list(original_bon.receipt_files.order_by("created_at", "pk"))
        house = original_bon.house

        # Group receipts by apartment_number (= member)
        groups = defaultdict(list)
        for receipt in receipts:
            try:
                ef = receipt.extracted_fields
                apt = ef.final_apartment_number or "unknown"
            except ReceiptExtractedFields.DoesNotExist:
                apt = "unknown"
            groups[apt].append(receipt)

        created_bons = []

        if len(groups) <= 1:
            # All same member or no grouping needed — keep original bon
            self._fill_bon_from_receipts(original_bon, receipts)
            created_bons.append(original_bon)
        else:
            # Multiple members — split into separate bons
            first = True
            for apt_code, group_receipts in groups.items():
                if first:
                    # Reuse original bon for first group
                    bon = original_bon
                    first = False
                else:
                    # Create new bon for this group
                    bon = BonDeCommande()
                    bon.house = house
                    bon.budget_year = original_bon.budget_year
                    bon.number = generate_bon_number(house, original_bon.budget_year.year)
                    bon.purchase_date = timezone.now().date()
                    bon.short_description = "(en cours de création)"
                    bon.total = 0
                    bon.sub_budget = original_bon.sub_budget
                    bon.purchaser_member = original_bon.purchaser_member
                    bon.created_by = original_bon.created_by
                    bon.status = BonStatus.READY_FOR_REVIEW
                    bon.entered_date = timezone.now().date()
                    super(BonDeCommande, bon).save()

                    # Move receipts to new bon
                    for r in group_receipts:
                        r.bon_de_commande = bon
                        r.save(update_fields=["bon_de_commande_id"])

                self._fill_bon_from_receipts(bon, group_receipts)
                created_bons.append(bon)

        if len(created_bons) == 1:
            messages.success(request, "Données confirmées. Complétez les détails du bon.")
            return redirect(reverse("bons:complete", kwargs={"pk": created_bons[0].pk}))
        else:
            # Multiple bons created — redirect to summary
            bon_numbers = ", ".join(b.number for b in created_bons)
            messages.success(
                request,
                f"{len(created_bons)} bons de commande créés ({bon_numbers}). "
                f"Complétez chacun individuellement.",
            )
            return redirect(reverse("bons:list"))

    def _fill_bon_from_receipts(self, bon, receipts):
        """Pre-fill bon fields from its confirmed receipt data."""
        update_fields = {}
        first_ef = None
        for r in receipts:
            try:
                first_ef = r.extracted_fields
                break
            except ReceiptExtractedFields.DoesNotExist:
                continue

        if first_ef:
            if first_ef.final_merchant:
                update_fields["merchant_name"] = first_ef.final_merchant
            if first_ef.final_purchase_date:
                update_fields["purchase_date"] = first_ef.final_purchase_date
            if first_ef.sub_budget_id:
                update_fields["sub_budget_id"] = first_ef.sub_budget_id

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

        # Auto-match apartment → member from first receipt
        apt_code = first_ef.final_apartment_number if first_ef else ""
        if apt_code:
            apartment, member = _match_member_for_apartment(bon.house, apt_code)
            if apartment:
                update_fields["purchaser_apartment_id"] = apartment.pk
            if member:
                update_fields["purchaser_member_id"] = member.pk

        if update_fields:
            BonDeCommande.objects.filter(pk=bon.pk).update(**update_fields)

    def _render(self, request, bon, receipt, form, idx, total, matched_member_name, name_mismatch=False):
        return TemplateResponse(request, self.template_name, {
            "bon": bon,
            "receipt": receipt,
            "form": form,
            "current_idx": idx,
            "total_receipts": total,
            "is_last": idx + 1 >= total,
            "matched_member_name": matched_member_name,
            "name_mismatch": name_mismatch,
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
        form = BonSearchForm(request.GET or None)
        results = None
        total_amount = None

        if request.GET and form.is_valid():
            qs = BonDeCommande.objects.select_related(
                "house", "budget_year", "sub_budget", "purchaser_member"
            ).order_by("-purchase_date", "-created_at")

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


# Needed for imports in urls.py — avoid circular import of BudgetYear
from budget.models import BudgetYear
