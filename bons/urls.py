from django.urls import path

from . import views

app_name = "bons"
urlpatterns = [
    path("", views.BonListView.as_view(), name="list"),
    path("upload/", views.ReceiptUploadWizardView.as_view(), name="upload"),
    path("create/", views.BonCreateView.as_view(), name="create"),
    path("create/manual/", views.BonCreateManualView.as_view(), name="create-manual"),
    path("<int:pk>/", views.BonDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.BonUpdateView.as_view(), name="edit"),
    path("<int:pk>/review/", views.OcrReviewView.as_view(), name="review"),
    path("<int:pk>/complete/", views.BonCompleteView.as_view(), name="complete"),
    path("<int:pk>/validate/", views.BonValidateView.as_view(), name="validate"),
    path("<int:bon_pk>/receipts/upload/", views.ReceiptUploadToExistingView.as_view(), name="receipt-upload"),
]
