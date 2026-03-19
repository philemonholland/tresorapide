"""Base Django settings for the Tresorapide project."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def get_env(name: str, default: str | None = None) -> str:
    """Return an environment variable or raise when it is required."""
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_bool_env(name: str, default: bool = False) -> bool:
    """Parse a boolean-like environment variable value."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_int_env(name: str, default: int) -> int:
    """Parse an integer environment variable value."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def get_list_env(name: str, default: list[str]) -> list[str]:
    """Parse a comma-separated environment variable into a list of strings."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def get_path_env(name: str, default: Path) -> Path:
    """Parse a filesystem path environment variable into a Path object."""
    value = os.getenv(name)
    if value is None:
        return default
    return Path(value)


SECRET_KEY = get_env(
    "DJANGO_SECRET_KEY",
    "django-insecure-local-dev-key-change-me",
)
DEBUG = get_bool_env("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = get_list_env(
    "DJANGO_ALLOWED_HOSTS",
    ["localhost", "127.0.0.1", "web"],
)
CSRF_TRUSTED_ORIGINS = get_list_env(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    ["http://localhost:8000", "http://127.0.0.1:8000"],
)
SERVE_MEDIA = get_bool_env("DJANGO_SERVE_MEDIA", default=DEBUG)
SERVE_STATIC = get_bool_env("DJANGO_SERVE_STATIC", default=DEBUG)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
    "accounts",
    "houses",
    "members",
    "budget",
    "bons",
    "maintenance",
    "audits",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASE_ENGINE = get_env("DATABASE_ENGINE", "django.db.backends.sqlite3")

if DATABASE_ENGINE == "django.db.backends.postgresql":
    DATABASES = {
        "default": {
            "ENGINE": DATABASE_ENGINE,
            "NAME": get_env("POSTGRES_DB", "tresorapide"),
            "USER": get_env("POSTGRES_USER", "tresorapide"),
            "PASSWORD": get_env("POSTGRES_PASSWORD", "tresorapide"),
            "HOST": get_env("POSTGRES_HOST", "localhost"),
            "PORT": get_env("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": get_int_env("POSTGRES_CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {
                "connect_timeout": get_int_env("POSTGRES_CONNECT_TIMEOUT", 5),
            },
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / get_env("SQLITE_PATH", "db.sqlite3"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "fr"
TIME_ZONE = get_env("DJANGO_TIME_ZONE", "America/Toronto")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = get_path_env("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles")
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = get_path_env("DJANGO_MEDIA_ROOT", BASE_DIR / "media")

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# OpenAI API for receipt analysis (GPT-5.4 Vision)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")
