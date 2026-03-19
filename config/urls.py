"""Top-level URL configuration for the Tresorapide project."""
from __future__ import annotations

from importlib.util import find_spec

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve


def optional_include(prefix: str, module_path: str, namespace: str):
    """Return a namespaced include when the target urlconf exists."""

    if find_spec(module_path) is None:
        return None
    return path(prefix, include((module_path, namespace), namespace=namespace))


urlpatterns = [
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("api/", include("core.api_urls")),
    path("admin/", admin.site.urls),
]

optional_patterns = [
    optional_include("houses/", "houses.urls", "houses"),
    optional_include("members/", "members.urls", "members"),
    optional_include("budget/", "budget.urls", "budget"),
    optional_include("bons/", "bons.urls", "bons"),
    optional_include("maintenance/", "maintenance.urls", "maintenance"),
    optional_include("audits/", "audits.urls", "audits"),
]
urlpatterns += [pattern for pattern in optional_patterns if pattern is not None]

if settings.SERVE_MEDIA:
    urlpatterns += [
        re_path(
            r"^media/(?P<path>.*)$",
            serve,
            {"document_root": settings.MEDIA_ROOT, "show_indexes": False},
        )
    ]

if settings.DEBUG or settings.SERVE_STATIC:
    urlpatterns += [
        re_path(
            r"^static/(?P<path>.*)$",
            serve,
            {"document_root": settings.STATIC_ROOT, "show_indexes": False},
        )
    ]
