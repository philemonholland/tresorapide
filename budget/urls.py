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
    path('<int:budget_year_pk>/expenses/add/', views.ExpenseCreateView.as_view(), name='expense-create'),
    path('expenses/<int:pk>/edit/', views.ExpenseUpdateView.as_view(), name='expense-edit'),
]
