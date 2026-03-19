from django.urls import path

from . import views

app_name = "members"

urlpatterns = [
    path("", views.MemberListView.as_view(), name="member-list"),
    path("create/", views.MemberCreateView.as_view(), name="member-create"),
    path("<int:pk>/", views.MemberDetailView.as_view(), name="member-detail"),
    path("<int:pk>/edit/", views.MemberUpdateView.as_view(), name="member-edit"),
    path("apartments/", views.ApartmentListView.as_view(), name="apartment-list"),
    path("apartments/create/", views.ApartmentCreateView.as_view(), name="apartment-create"),
    path("apartments/<int:pk>/edit/", views.ApartmentUpdateView.as_view(), name="apartment-edit"),
    path("residencies/create/", views.ResidencyCreateView.as_view(), name="residency-create"),
    path("residencies/<int:pk>/edit/", views.ResidencyUpdateView.as_view(), name="residency-edit"),
]
