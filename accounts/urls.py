"""URL routes for authentication and account management."""
from __future__ import annotations

from django.contrib.auth import views as auth_views
from django.urls import path

from accounts.views import (
    AccountCreateView,
    AccountListView,
    SetupAwareLoginView,
    account_delete_view,
)

app_name = "accounts"

urlpatterns = [
    path(
        "login/",
        SetupAwareLoginView.as_view(
            template_name="registration/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
    path(
        "password/",
        auth_views.PasswordChangeView.as_view(
            template_name="registration/password_change.html",
            success_url="/accounts/password/done/",
        ),
        name="password_change",
    ),
    path(
        "password/done/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="registration/password_change_done.html",
        ),
        name="password_change_done",
    ),
    path(
        "",
        AccountListView.as_view(),
        name="list",
    ),
    path(
        "create/",
        AccountCreateView.as_view(),
        name="create",
    ),
    path(
        "<int:pk>/delete/",
        account_delete_view,
        name="delete",
    ),
]
