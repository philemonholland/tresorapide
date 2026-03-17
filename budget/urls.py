"""Read-only budget transparency routes."""
from __future__ import annotations

from django.urls import path

from budget.views import (
    BudgetCategoryCreateView,
    BudgetCategoryDetailView,
    BudgetCategoryUpdateView,
    BudgetYearCreateView,
    BudgetYearDetailView,
    BudgetYearListView,
    BudgetYearUpdateView,
)

app_name = "budget"

urlpatterns = [
    path("", BudgetYearListView.as_view(), name="list"),
    path("years/new/", BudgetYearCreateView.as_view(), name="year-create"),
    path("years/<int:pk>/", BudgetYearDetailView.as_view(), name="year-detail"),
    path("years/<int:pk>/edit/", BudgetYearUpdateView.as_view(), name="year-edit"),
    path(
        "years/<int:year_pk>/categories/new/",
        BudgetCategoryCreateView.as_view(),
        name="category-create",
    ),
    path("categories/<int:pk>/", BudgetCategoryDetailView.as_view(), name="category-detail"),
    path(
        "categories/<int:pk>/edit/",
        BudgetCategoryUpdateView.as_view(),
        name="category-edit",
    ),
]
