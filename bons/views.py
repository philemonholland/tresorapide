from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View
from django.views.generic.edit import FormView
from django.contrib import messages

from accounts.access import RoleRequiredMixin, TreasurerRequiredMixin
from .models import (
    BonDeCommande, BonStatus, ReceiptFile, ReceiptExtractedFields, OcrStatus,
)
from .forms import (
    BonDeCommandeForm, ReceiptUploadForm, BonValidateForm,
    MultiReceiptUploadForm, OcrReviewForm,
)
from .services import generate_bon_number
from .ocr_service import ReceiptOcrService


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
        qs = super().get_queryset().select_related(
            "budget_year", "sub_budget", "purchaser_member"
        )
        qs = _filter_by_house(qs, self.request.user)

        status = self.request.GET.get("status")
        if status and status in BonStatus.values:
            qs = qs.filter(status=status)

        budget_year = self.request.GET.get("budget_year")
        if budget_year:
            qs = qs.filter(budget_year_id=budget_year)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_manage"] = self.request.user.can_manage_financials
        ctx["status_choices"] = BonStatus.choices
        ctx["current_status"] = self.request.GET.get("status", "")
        ctx["current_budget_year"] = self.request.GET.get("budget_year", "")
        user = self.request.user
        if user.is_gestionnaire:
            ctx["budget_years"] = BudgetYear.objects.filter(is_active=True)
        else:
            ctx["budget_years"] = BudgetYear.objects.filter(
                house=user.house, is_active=True
            )
        return ctx


class BonDetailView(RoleRequiredMixin, DetailView):
    model = BonDeCommande
    template_name = "bons/detail.html"
    context_object_name = "bon"

    def get_queryset(self):
        qs = super().get_queryset().select_related(
            "house", "budget_year", "sub_budget",
            "purchaser_member", "purchaser_apartment",
            "approver_member", "created_by", "validated_by",
        )
        return _filter_by_house(qs, self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["receipts"] = self.object.receipt_files.all()
        ctx["can_manage"] = self.request.user.can_manage_financials
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

class ReceiptUploadWizardView(TreasurerRequiredMixin, FormView):
    """Step 1: Upload receipt files → create DRAFT bon → run OCR."""
    template_name = "bons/upload.html"
    form_class = MultiReceiptUploadForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["house"] = self.request.user.house
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ocr_available"] = ReceiptOcrService.is_available()
        return ctx

    def form_valid(self, form):
        user = self.request.user
        house = user.house
        budget_year = form.cleaned_data["budget_year"]
        files = self.request.FILES.getlist("files")

        if not files:
            messages.error(self.request, "Veuillez sélectionner au moins un fichier.")
            return self.form_invalid(form)

        # Create a DRAFT bon with minimal required fields
        from budget.models import SubBudget
        default_sub = SubBudget.objects.filter(
            budget_year=budget_year, is_active=True
        ).order_by("sort_order", "trace_code").first()

        bon = BonDeCommande(
            house=house,
            budget_year=budget_year,
            number=generate_bon_number(house, budget_year.year),
            purchase_date=timezone.now().date(),
            short_description="(en cours de création)",
            total=0,
            sub_budget=default_sub,
            purchaser_member_id=user.member_id if user.member_id else None,
            created_by=user,
            status=BonStatus.DRAFT,
        )
        # Skip full_clean for draft — required fields will be filled at step 3
        bon.save_base(raw=True)
        # Set timestamps that auto_now_add would have set
        if not bon.created_at:
            BonDeCommande.objects.filter(pk=bon.pk).update(
                created_at=timezone.now(),
                entered_date=timezone.now().date(),
            )
            bon.refresh_from_db()

        ocr_available = ReceiptOcrService.is_available()
        ocr_warning_shown = False

        for f in files:
            receipt = ReceiptFile.objects.create(
                bon_de_commande=bon,
                file=f,
                original_filename=f.name,
                content_type=getattr(f, "content_type", ""),
                uploaded_by=user,
            )
            if ocr_available:
                ReceiptOcrService.process_receipt(receipt)
            elif not ocr_warning_shown:
                ocr_warning_shown = True

        if not ocr_available:
            messages.warning(
                self.request,
                "L'OCR n'est pas disponible. Veuillez saisir les informations manuellement.",
            )

        bon.status = BonStatus.READY_FOR_REVIEW
        BonDeCommande.objects.filter(pk=bon.pk).update(status=BonStatus.READY_FOR_REVIEW)

        messages.success(
            self.request,
            f"{len(files)} reçu(s) téléversé(s). Vérifiez les données extraites.",
        )
        return redirect(reverse("bons:review", kwargs={"pk": bon.pk}))


# ---------------------------------------------------------------------------
# OCR Workflow — Step 2: Review OCR results
# ---------------------------------------------------------------------------

class OcrReviewView(TreasurerRequiredMixin, View):
    """Step 2: Show extracted data beside receipt images for correction."""
    template_name = "bons/review.html"

    def _get_bon(self, request, pk):
        return get_object_or_404(
            _filter_by_house(BonDeCommande.objects.all(), request.user),
            pk=pk,
            status__in=[BonStatus.DRAFT, BonStatus.READY_FOR_REVIEW],
        )

    def _build_forms(self, receipts, post_data=None):
        """Build one OcrReviewForm per receipt, pre-filled from OCR data."""
        forms = []
        for receipt in receipts:
            prefix = f"receipt_{receipt.pk}"
            initial = {}
            try:
                ef = receipt.extracted_fields
                initial = {
                    "merchant_name": ef.final_merchant or ef.merchant_candidate,
                    "purchase_date": ef.final_purchase_date or ef.purchase_date_candidate,
                    "subtotal": ef.final_subtotal if ef.final_subtotal is not None else ef.subtotal_candidate,
                    "tps": ef.final_tps if ef.final_tps is not None else ef.tps_candidate,
                    "tvq": ef.final_tvq if ef.final_tvq is not None else ef.tvq_candidate,
                    "total": ef.final_total if ef.final_total is not None else ef.total_candidate,
                }
            except ReceiptExtractedFields.DoesNotExist:
                pass
            form = OcrReviewForm(data=post_data, prefix=prefix, initial=initial)
            forms.append((receipt, form))
        return forms

    def get(self, request, pk):
        bon = self._get_bon(request, pk)
        receipts = bon.receipt_files.all()
        receipt_forms = self._build_forms(receipts)
        return self._render(request, bon, receipt_forms)

    def post(self, request, pk):
        bon = self._get_bon(request, pk)
        receipts = bon.receipt_files.all()
        receipt_forms = self._build_forms(receipts, post_data=request.POST)

        all_valid = all(form.is_valid() for _, form in receipt_forms)
        if not all_valid:
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
            return self._render(request, bon, receipt_forms)

        # Save confirmed values
        for receipt, form in receipt_forms:
            ef, _ = ReceiptExtractedFields.objects.get_or_create(
                receipt_file=receipt,
            )
            ef.final_merchant = form.cleaned_data.get("merchant_name") or ""
            ef.final_purchase_date = form.cleaned_data.get("purchase_date")
            ef.final_subtotal = form.cleaned_data.get("subtotal")
            ef.final_tps = form.cleaned_data.get("tps")
            ef.final_tvq = form.cleaned_data.get("tvq")
            ef.final_total = form.cleaned_data.get("total")
            ef.confirmed_by = request.user
            ef.confirmed_at = timezone.now()
            ef.save()

            if receipt.ocr_status in (OcrStatus.EXTRACTED, OcrStatus.NOT_REQUESTED, OcrStatus.FAILED):
                receipt.ocr_status = OcrStatus.CORRECTED
                receipt.save(update_fields=["ocr_status"])

        # Pre-fill bon from confirmed data (use first receipt as primary)
        first_ef = ReceiptExtractedFields.objects.filter(
            receipt_file__bon_de_commande=bon,
        ).order_by("receipt_file__created_at").first()

        update_fields = {}
        if first_ef:
            if first_ef.final_merchant:
                update_fields["merchant_name"] = first_ef.final_merchant
            if first_ef.final_purchase_date:
                update_fields["purchase_date"] = first_ef.final_purchase_date
            if first_ef.final_subtotal is not None:
                update_fields["subtotal"] = first_ef.final_subtotal
            if first_ef.final_tps is not None:
                update_fields["tps"] = first_ef.final_tps
            if first_ef.final_tvq is not None:
                update_fields["tvq"] = first_ef.final_tvq
            if first_ef.final_total is not None:
                update_fields["total"] = first_ef.final_total

        if update_fields:
            BonDeCommande.objects.filter(pk=bon.pk).update(**update_fields)

        messages.success(request, "Données confirmées. Complétez les détails du bon.")
        return redirect(reverse("bons:complete", kwargs={"pk": bon.pk}))

    def _render(self, request, bon, receipt_forms):
        from django.template.response import TemplateResponse
        return TemplateResponse(request, self.template_name, {
            "bon": bon,
            "receipt_forms": receipt_forms,
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


# Needed for imports in urls.py — avoid circular import of BudgetYear
from budget.models import BudgetYear
