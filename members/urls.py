"""Treasurer-facing member and residency routes."""

from __future__ import annotations

from django.urls import path

from members.views import (
    ApartmentCreateView,
    ApartmentListView,
    ApartmentUpdateView,
    MemberCreateView,
    MemberListView,
    MemberUpdateView,
    MembersDashboardView,
    ResidencyCreateView,
    ResidencyListView,
    ResidencyUpdateView,
)

app_name = "members"

urlpatterns = [
    path("", MembersDashboardView.as_view(), name="index"),
    path("members/", MemberListView.as_view(), name="member-list"),
    path("members/new/", MemberCreateView.as_view(), name="member-create"),
    path("members/<int:pk>/edit/", MemberUpdateView.as_view(), name="member-edit"),
    path("apartments/", ApartmentListView.as_view(), name="apartment-list"),
    path("apartments/new/", ApartmentCreateView.as_view(), name="apartment-create"),
    path("apartments/<int:pk>/edit/", ApartmentUpdateView.as_view(), name="apartment-edit"),
    path("residencies/", ResidencyListView.as_view(), name="residency-list"),
    path("residencies/new/", ResidencyCreateView.as_view(), name="residency-create"),
    path("residencies/<int:pk>/edit/", ResidencyUpdateView.as_view(), name="residency-edit"),
]
