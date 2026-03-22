from django.urls import path

from . import views

app_name = "bons"
urlpatterns = [
    path("", views.BonListView.as_view(), name="list"),
    path("search/", views.BonSearchView.as_view(), name="search"),
    path("upload/", views.ReceiptUploadWizardView.as_view(), name="upload"),
    path("pending-scans/", views.PendingScanSessionsView.as_view(), name="pending-scans"),
    path("scan-complete/", views.ScanCompleteView.as_view(), name="scan-complete"),
    path("create/", views.BonCreateView.as_view(), name="create"),
    path("create/manual/", views.BonCreateManualView.as_view(), name="create-manual"),
    path("<int:pk>/", views.BonDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.BonUpdateView.as_view(), name="edit"),
    path("<int:pk>/review/", views.OcrReviewView.as_view(), name="review"),
    path("<int:pk>/complete/", views.BonCompleteView.as_view(), name="complete"),
    path("<int:pk>/validate/", views.BonValidateView.as_view(), name="validate"),
    path("<int:pk>/delete/", views.BonDeleteView.as_view(), name="delete"),
    path("<int:pk>/pdf/", views.BonExportPdfView.as_view(), name="export-pdf"),
    path("<int:pk>/xlsx/", views.BonExportXlsxView.as_view(), name="export-xlsx"),
    path("<int:bon_pk>/receipts/upload/", views.ReceiptUploadToExistingView.as_view(), name="receipt-upload"),
    path("<int:bon_pk>/receipts/<int:receipt_pk>/review/", views.ReceiptReviewSingleView.as_view(), name="receipt-review"),
    path("duplicates/<int:flag_pk>/resolve/", views.ResolveDuplicateFlagView.as_view(), name="resolve-duplicate"),
]
