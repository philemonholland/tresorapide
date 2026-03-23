from django.urls import path
from . import views

app_name = 'budget'
urlpatterns = [
    path('', views.BudgetYearListView.as_view(), name='year-list'),
    path('<int:pk>/', views.BudgetYearDetailView.as_view(), name='year-detail'),
    path('create/', views.BudgetYearCreateView.as_view(), name='year-create'),
    path('<int:pk>/edit/', views.BudgetYearUpdateView.as_view(), name='year-edit'),
    path('<int:budget_year_pk>/sub-budgets/create/', views.SubBudgetCreateView.as_view(), name='subbudget-create'),
    path('sub-budgets/<int:pk>/edit/', views.SubBudgetUpdateView.as_view(), name='subbudget-edit'),
    path('<int:budget_year_pk>/expenses/', views.ExpenseLedgerView.as_view(), name='expense-ledger'),
    path('<int:budget_year_pk>/expenses/export/pdf/', views.ExpenseLedgerExportPDFView.as_view(), name='expense-ledger-pdf'),
    path('<int:budget_year_pk>/expenses/export/xlsx/', views.ExpenseLedgerExportXLSXView.as_view(), name='expense-ledger-xlsx'),
    path('<int:budget_year_pk>/expenses/add/', views.ExpenseCreateView.as_view(), name='expense-create'),
    path('expenses/<int:pk>/edit/', views.ExpenseUpdateView.as_view(), name='expense-edit'),
    path('expenses/<int:pk>/receipts/', views.ExpenseReceiptsView.as_view(), name='expense-receipts'),
    path('expenses/<int:pk>/cancel/', views.ExpenseCancelView.as_view(), name='expense-cancel'),
    path('expenses/<int:pk>/reactivate/', views.ExpenseReactivateView.as_view(), name='expense-reactivate'),
    # Grand Livre
    path('grand-livre/', views.GrandLivreListView.as_view(), name='grand-livre-list'),
    path('grand-livre/upload/', views.GrandLivreUploadView.as_view(), name='grand-livre-upload'),
    path('grand-livre/<int:pk>/', views.GrandLivreDetailView.as_view(), name='grand-livre-detail'),
    path('grand-livre/<int:pk>/validate/', views.GrandLivreValidateView.as_view(), name='grand-livre-validate'),
    path('grand-livre/<int:pk>/entries/<int:entry_pk>/edit/', views.GrandLivreEntryEditView.as_view(), name='grand-livre-entry-edit'),
]
