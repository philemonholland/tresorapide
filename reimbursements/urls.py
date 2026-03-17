"""Read-only reimbursement transparency routes."""
from __future__ import annotations

from django.urls import path

from reimbursements.views import (
    ReceiptFileDownloadView,
    ReceiptArchiveView,
    ReceiptUploadView,
    ReimbursementArchiveListView,
    ReimbursementCreateView,
    ReimbursementFinalizeValidationView,
    ReimbursementPdfDownloadView,
    ReimbursementPdfView,
    ReimbursementTransparencyDetailView,
    ReimbursementTransparencyListView,
    ReimbursementUpdateView,
    ReimbursementVoidView,
)

app_name = "reimbursements"

urlpatterns = [
    path("", ReimbursementTransparencyListView.as_view(), name="list"),
    path("new/", ReimbursementCreateView.as_view(), name="create"),
    path("archive/", ReimbursementArchiveListView.as_view(), name="archive"),
    path("<int:pk>/", ReimbursementTransparencyDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", ReimbursementUpdateView.as_view(), name="edit"),
    path("<int:pk>/finalize/", ReimbursementFinalizeValidationView.as_view(), name="finalize"),
    path("<int:pk>/void/", ReimbursementVoidView.as_view(), name="void"),
    path("<int:pk>/receipts/upload/", ReceiptUploadView.as_view(), name="receipt-upload"),
    path("<int:pk>/pdf/", ReimbursementPdfDownloadView.as_view(), name="pdf-download"),
    path("<int:pk>/pdf/view/", ReimbursementPdfView.as_view(), name="pdf-view"),
    path(
        "receipts/<int:pk>/download/",
        ReceiptFileDownloadView.as_view(),
        name="receipt-download",
    ),
    path(
        "receipts/<int:pk>/archive/",
        ReceiptArchiveView.as_view(),
        name="receipt-archive",
    ),
]
