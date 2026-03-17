"""Reimbursement transparency, treasurer workflow, and PDF views."""
from __future__ import annotations

from pathlib import Path

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.access import RoleRequiredMixin, TreasurerRequiredMixin
from budget.models import BudgetCategory, BudgetYear
from reimbursements.forms import (
    ReceiptArchiveForm,
    ReceiptUploadForm,
    ReimbursementFinalValidationForm,
    ReimbursementForm,
    ReimbursementVoidForm,
)
from reimbursements.models import ReceiptFile, Reimbursement, ReimbursementStatus
from reimbursements.pdf import render_reimbursement_package_pdf
from reimbursements.queries import (
    can_user_view_reimbursement,
    optional_pdf_download_url,
    can_user_review_internal_reimbursements,
    transparency_visibility_note,
    visible_reimbursements_for_user,
)
from reimbursements.services import ReimbursementWorkflowService


EDITABLE_REIMBURSEMENT_STATUSES = frozenset(
    {ReimbursementStatus.DRAFT, ReimbursementStatus.SUBMITTED}
)


def _apply_validation_error(
    form,
    error: ValidationError,
    *,
    field_map: dict[str, str | None] | None = None,
) -> None:
    """Map service-level ValidationErrors onto a Django form."""
    field_map = field_map or {}
    if hasattr(error, "message_dict"):
        for field_name, messages_list in error.message_dict.items():
            target_field = field_map.get(field_name, field_name)
            if target_field is not None and target_field not in form.fields:
                target_field = None
            for message in messages_list:
                form.add_error(target_field, message)
        return
    for message in error.messages:
        form.add_error(None, message)


class SuccessMessageFormMixin:
    """Attach a success message to create and update views."""

    success_message = ""

    def form_valid(self, form):  # type: ignore[override]
        """Show a success message after saving."""
        response = super().form_valid(form)
        if self.success_message:
            messages.success(self.request, self.success_message)
        return response


class ReimbursementDetailContextMixin:
    """Build the full detail page context for GET and invalid POST responses."""

    def _build_receipt_archive_entries(
        self,
        reimbursement: Reimbursement,
        *,
        form_overrides: dict[int, ReceiptArchiveForm] | None = None,
    ) -> tuple[list[dict[str, object]], list[ReceiptFile]]:
        """Return active receipt rows with forms, plus archived receipts."""
        form_overrides = form_overrides or {}
        receipt_queryset = reimbursement.receipt_files.select_related("uploaded_by").order_by(
            "created_at",
            "id",
        )
        active_entries: list[dict[str, object]] = []
        archived_receipts: list[ReceiptFile] = []
        for receipt in receipt_queryset:
            if receipt.archived_at is None:
                active_entries.append(
                    {
                        "receipt": receipt,
                        "archive_form": form_overrides.get(
                            receipt.pk,
                            ReceiptArchiveForm(prefix=f"receipt-{receipt.pk}"),
                        ),
                    }
                )
            else:
                archived_receipts.append(receipt)
        return active_entries, archived_receipts

    def build_detail_context(
        self,
        reimbursement: Reimbursement,
        *,
        upload_form: ReceiptUploadForm | None = None,
        validation_form: ReimbursementFinalValidationForm | None = None,
        void_form: ReimbursementVoidForm | None = None,
        archive_form_overrides: dict[int, ReceiptArchiveForm] | None = None,
    ) -> dict[str, object]:
        """Build a consistent context for the reimbursement detail page."""
        can_manage = can_user_review_internal_reimbursements(self.request.user)
        active_receipt_entries, archived_receipts = self._build_receipt_archive_entries(
            reimbursement,
            form_overrides=archive_form_overrides,
        )
        return {
            "reimbursement": reimbursement,
            "active_receipt_entries": active_receipt_entries,
            "archived_receipts": archived_receipts,
            "pdf_download_url": optional_pdf_download_url(reimbursement),
            "pdf_view_url": reverse("reimbursements:pdf-view", args=[reimbursement.pk]),
            "transparency_note": transparency_visibility_note(self.request.user),
            "can_manage": can_manage,
            "can_edit_reimbursement": (
                can_manage and reimbursement.status in EDITABLE_REIMBURSEMENT_STATUSES
            ),
            "can_upload_receipt": can_manage and reimbursement.status != ReimbursementStatus.VOID,
            "can_finalize": can_manage and reimbursement.status == ReimbursementStatus.SUBMITTED,
            "can_void": can_manage and reimbursement.status != ReimbursementStatus.VOID,
            "upload_form": upload_form or ReceiptUploadForm(),
            "validation_form": validation_form
            or ReimbursementFinalValidationForm(reimbursement=reimbursement),
            "void_form": void_form or ReimbursementVoidForm(),
        }


class ReimbursementTransparencyListView(RoleRequiredMixin, ListView):
    """Browse read-only reimbursement history."""

    model = Reimbursement
    template_name = "reimbursements/list.html"
    context_object_name = "reimbursements"
    paginate_by = 25

    def get_base_queryset(self):
        """Return visible reimbursements for the current user."""

        return visible_reimbursements_for_user(self.request.user).order_by(
            "-expense_date",
            "-created_at",
            "-id",
        )

    def apply_filters(self, queryset):
        """Apply search and structured filter inputs to a queryset."""
        requested_status = self.request.GET.get("status", "").strip()
        query = self.request.GET.get("q", "").strip()
        budget_year = self.request.GET.get("year", "").strip()
        budget_category = self.request.GET.get("category", "").strip()
        valid_statuses = {value for value, _label in ReimbursementStatus.choices}
        if requested_status in valid_statuses:
            queryset = queryset.filter(status=requested_status)
        if query:
            queryset = queryset.filter(
                Q(reference_code__icontains=query)
                | Q(title__icontains=query)
                | Q(member_name_snapshot__icontains=query)
                | Q(budget_category_code_snapshot__icontains=query)
                | Q(budget_category_name_snapshot__icontains=query)
                | Q(apartment_display_snapshot__icontains=query)
            )
        if budget_year.isdigit():
            queryset = queryset.filter(budget_year_id=int(budget_year))
        if budget_category.isdigit():
            queryset = queryset.filter(budget_category_id=int(budget_category))
        return queryset

    def get_queryset(self):
        """Apply optional status filters to the visible reimbursements."""

        return self.apply_filters(self.get_base_queryset())

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Add view metadata and aggregate counts."""

        context = super().get_context_data(**kwargs)
        current_queryset = self.object_list
        can_manage = can_user_review_internal_reimbursements(self.request.user)
        context.update(
            {
                "page_title": (
                    "Reimbursement workflows" if can_manage else "Reimbursement transparency"
                ),
                "page_intro": (
                    "Create, edit, review, and finalize reimbursements from the treasurer "
                    "workspace."
                    if can_manage
                    else (
                        "Read-only reimbursement history, including archived and void records "
                        "when they are visible to your role."
                    )
                ),
                "transparency_note": transparency_visibility_note(self.request.user),
                "status_choices": ReimbursementStatus.choices,
                "current_status": self.request.GET.get("status", "").strip(),
                "current_query": self.request.GET.get("q", "").strip(),
                "current_year": self.request.GET.get("year", "").strip(),
                "current_category": self.request.GET.get("category", "").strip(),
                "budget_years": BudgetYear.objects.order_by("-start_date", "-id"),
                "budget_categories": BudgetCategory.objects.select_related("budget_year").order_by(
                    "-budget_year__start_date",
                    "sort_order",
                    "code",
                    "id",
                ),
                "archive_url": reverse("reimbursements:archive"),
                "can_manage": can_manage,
                "summary": current_queryset.aggregate(
                    reimbursement_count=Count("id"),
                    archived_count=Count("id", filter=Q(archived_at__isnull=False)),
                    void_count=Count("id", filter=Q(status=ReimbursementStatus.VOID)),
                ),
            }
        )
        return context


class ReimbursementArchiveListView(ReimbursementTransparencyListView):
    """Browse archive-sensitive reimbursement history."""

    template_name = "reimbursements/archive.html"

    def get_queryset(self):
        """Only show archived or void reimbursements."""

        queryset = super().get_base_queryset().filter(
            Q(archived_at__isnull=False) | Q(status=ReimbursementStatus.VOID)
        )
        return self.apply_filters(queryset)

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Rename the archive history page."""

        context = super().get_context_data(**kwargs)
        context["page_title"] = "Archive and void history"
        context["page_intro"] = (
            "Historical reimbursements that were archived or voided remain available "
            "for transparency and audit review."
        )
        return context


class ReimbursementTransparencyDetailView(
    ReimbursementDetailContextMixin,
    RoleRequiredMixin,
    DetailView,
):
    """Show a single reimbursement with receipt/archive details."""

    model = Reimbursement
    template_name = "reimbursements/detail.html"
    context_object_name = "reimbursement"

    def get_queryset(self):
        """Restrict reimbursement detail access by role-safe visibility."""

        return visible_reimbursements_for_user(self.request.user)

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        """Expose receipt visibility and optional PDF links."""

        context = super().get_context_data(**kwargs)
        context.update(self.build_detail_context(self.object))
        return context


class ReimbursementSaveViewMixin(SuccessMessageFormMixin):
    """Persist created_by without exposing it in server-rendered forms."""

    def form_valid(self, form):  # type: ignore[override]
        """Save the reimbursement while setting the creator on first save."""
        self.object = form.save(commit=False)
        if self.object.pk is None and self.object.created_by_id is None:
            self.object.created_by = self.request.user
        self.object.save()
        form.save_m2m()
        if self.success_message:
            messages.success(self.request, self.success_message)
        return HttpResponseRedirect(self.get_success_url())


class ReimbursementCreateView(
    TreasurerRequiredMixin,
    ReimbursementSaveViewMixin,
    CreateView,
):
    """Create a reimbursement from the treasurer workflow list."""

    model = Reimbursement
    form_class = ReimbursementForm
    template_name = "reimbursements/form.html"
    success_message = "Reimbursement created."

    def get_success_url(self) -> str:
        """Return to the detail page after creation."""
        return reverse("reimbursements:detail", args=[self.object.pk])


class ReimbursementUpdateView(
    TreasurerRequiredMixin,
    ReimbursementSaveViewMixin,
    UpdateView,
):
    """Edit a draft or submitted reimbursement."""

    model = Reimbursement
    form_class = ReimbursementForm
    template_name = "reimbursements/form.html"
    success_message = "Reimbursement updated."

    def get_object(self, queryset=None):  # type: ignore[override]
        """Only allow editing before final validation or voiding."""
        obj = super().get_object(queryset)
        if obj.status not in EDITABLE_REIMBURSEMENT_STATUSES:
            raise Http404("Only draft or submitted reimbursements can be edited.")
        return obj

    def get_success_url(self) -> str:
        """Return to the detail page after editing."""
        return reverse("reimbursements:detail", args=[self.object.pk])


class ReimbursementActionViewMixin(ReimbursementDetailContextMixin):
    """Shared helpers for POST-driven reimbursement actions."""

    workflow_service = ReimbursementWorkflowService()

    def get_reimbursement(self, pk: int) -> Reimbursement:
        """Return the reimbursement that the action will operate on."""
        return get_object_or_404(Reimbursement.objects.select_related("budget_year", "budget_category"), pk=pk)

    def render_detail(
        self,
        request,
        reimbursement: Reimbursement,
        *,
        upload_form: ReceiptUploadForm | None = None,
        validation_form: ReimbursementFinalValidationForm | None = None,
        void_form: ReimbursementVoidForm | None = None,
        archive_form_overrides: dict[int, ReceiptArchiveForm] | None = None,
        status: int = 200,
    ) -> HttpResponse:
        """Render the detail template with any bound invalid forms."""
        context = self.build_detail_context(
            reimbursement,
            upload_form=upload_form,
            validation_form=validation_form,
            void_form=void_form,
            archive_form_overrides=archive_form_overrides,
        )
        return render(
            request,
            "reimbursements/detail.html",
            context,
            status=status,
        )


class ReceiptUploadView(TreasurerRequiredMixin, ReimbursementActionViewMixin, View):
    """Upload a receipt from the reimbursement detail page."""

    http_method_names = ["post"]

    def post(self, request, *args: object, **kwargs: object) -> HttpResponse:
        """Attach a file to the reimbursement archive."""
        reimbursement = self.get_reimbursement(kwargs["pk"])
        form = ReceiptUploadForm(request.POST, request.FILES)
        if reimbursement.status == ReimbursementStatus.VOID:
            form.add_error(None, "Voided reimbursements cannot accept new receipt uploads.")
        if not form.is_valid():
            return self.render_detail(request, reimbursement, upload_form=form, status=400)
        receipt = form.save(commit=False)
        receipt.reimbursement = reimbursement
        receipt.uploaded_by = request.user
        receipt.save()
        messages.success(request, "Receipt uploaded.")
        return HttpResponseRedirect(reverse("reimbursements:detail", args=[reimbursement.pk]))


class ReceiptArchiveView(TreasurerRequiredMixin, ReimbursementActionViewMixin, View):
    """Archive a receipt file while preserving the reimbursement detail workflow."""

    http_method_names = ["post"]

    def post(self, request, *args: object, **kwargs: object) -> HttpResponse:
        """Archive a receipt using the workflow service."""
        receipt = get_object_or_404(
            ReceiptFile.objects.select_related("reimbursement"),
            pk=kwargs["pk"],
        )
        reimbursement = receipt.reimbursement
        prefix = f"receipt-{receipt.pk}"
        form = ReceiptArchiveForm(request.POST, prefix=prefix)
        if form.is_valid():
            try:
                self.workflow_service.archive_receipt(
                    receipt,
                    actor=request.user,
                    reason=form.cleaned_data["reason"],
                )
            except ValidationError as error:
                _apply_validation_error(
                    form,
                    error,
                    field_map={"archive_reason": "reason", "archived_at": None, "file": None},
                )
            else:
                messages.success(request, "Receipt archived.")
                return HttpResponseRedirect(
                    reverse("reimbursements:detail", args=[reimbursement.pk])
                )
        return self.render_detail(
            request,
            reimbursement,
            archive_form_overrides={receipt.pk: form},
            status=400,
        )


class ReimbursementFinalizeValidationView(
    TreasurerRequiredMixin,
    ReimbursementActionViewMixin,
    View,
):
    """Finalize a submitted reimbursement through the service layer."""

    http_method_names = ["post"]

    def post(self, request, *args: object, **kwargs: object) -> HttpResponse:
        """Validate and approve the reimbursement."""
        reimbursement = self.get_reimbursement(kwargs["pk"])
        form = ReimbursementFinalValidationForm(
            request.POST,
            reimbursement=reimbursement,
        )
        if form.is_valid():
            try:
                self.workflow_service.finalize_validation(
                    reimbursement,
                    actor=request.user,
                    validation_input=form.to_validation_input(),
                )
            except ValidationError as error:
                _apply_validation_error(
                    form,
                    error,
                    field_map={
                        "receipt_signed_by_member": "approver_member",
                        "signed_receipt_received_at": "signed_receipt_received",
                        "signature_verified_at": "signature_verified",
                        "amount_approved": "approved_amount",
                        "receipt_files": None,
                        "approved_by": None,
                        "status": None,
                    },
                )
            else:
                messages.success(request, "Reimbursement validated and approved.")
                return HttpResponseRedirect(
                    reverse("reimbursements:detail", args=[reimbursement.pk])
                )
        return self.render_detail(
            request,
            reimbursement,
            validation_form=form,
            status=400,
        )


class ReimbursementVoidView(TreasurerRequiredMixin, ReimbursementActionViewMixin, View):
    """Void a reimbursement through the workflow service."""

    http_method_names = ["post"]

    def post(self, request, *args: object, **kwargs: object) -> HttpResponse:
        """Void the reimbursement and return to its detail page."""
        reimbursement = self.get_reimbursement(kwargs["pk"])
        form = ReimbursementVoidForm(request.POST)
        if form.is_valid():
            try:
                self.workflow_service.void_reimbursement(
                    reimbursement,
                    actor=request.user,
                    reason=form.cleaned_data["reason"],
                )
            except ValidationError as error:
                _apply_validation_error(
                    form,
                    error,
                    field_map={"void_reason": "reason", "status": None, "approved_by": None},
                )
            else:
                messages.success(request, "Reimbursement voided.")
                return HttpResponseRedirect(
                    reverse("reimbursements:detail", args=[reimbursement.pk])
                )
        return self.render_detail(request, reimbursement, void_form=form, status=400)


class BaseReimbursementPdfView(RoleRequiredMixin, View):
    """Shared permission checks and PDF rendering for reimbursement packages."""

    as_attachment = True

    def get_reimbursement(self, request, pk: int) -> Reimbursement:
        """Restrict PDF access to role-appropriate visible reimbursements."""
        return get_object_or_404(visible_reimbursements_for_user(request.user), pk=pk)

    def get_filename(self, reimbursement: Reimbursement) -> str:
        """Return a stable archive-friendly filename."""
        return f"{reimbursement.reference_code.lower()}-package.pdf"

    def get_content_disposition(self) -> str:
        """Return the attachment mode for the response."""
        return "attachment" if self.as_attachment else "inline"

    def get(self, request, *args: object, **kwargs: object) -> HttpResponse:
        """Render and return the reimbursement PDF package."""
        reimbursement = self.get_reimbursement(request, kwargs["pk"])
        pdf_bytes = render_reimbursement_package_pdf(reimbursement)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'{self.get_content_disposition()}; filename="{self.get_filename(reimbursement)}"'
        )
        return response


class ReimbursementPdfDownloadView(BaseReimbursementPdfView):
    """Download the reimbursement PDF package."""

    as_attachment = True


class ReimbursementPdfView(BaseReimbursementPdfView):
    """Open the reimbursement PDF package inline."""

    as_attachment = False


class ReceiptFileDownloadView(RoleRequiredMixin, View):
    """Serve receipt files through a permission-checked route."""

    http_method_names = ["get", "head", "options"]

    def get(self, request, *args: object, **kwargs: object) -> FileResponse:
        """Return an authenticated download for a visible receipt file."""

        receipt = get_object_or_404(
            ReceiptFile.objects.select_related("reimbursement"),
            pk=kwargs["pk"],
        )
        if not can_user_view_reimbursement(request.user, receipt.reimbursement):
            raise Http404("Receipt file not found.")
        if not receipt.file or not receipt.file.name:
            raise Http404("Receipt file is unavailable.")
        if not receipt.file.storage.exists(receipt.file.name):
            raise Http404("Receipt file is unavailable.")
        download_name = receipt.original_filename or Path(receipt.file.name).name
        return FileResponse(
            receipt.file.open("rb"),
            as_attachment=True,
            filename=download_name,
        )
