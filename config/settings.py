"""Application settings loaded from the process environment or an ignored .env file."""

import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", default=not bool(os.environ.get("DJANGO_SECRET_KEY")))
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or (secrets.token_urlsafe(50) if DEBUG else "")
if not DEBUG and (
    not SECRET_KEY or SECRET_KEY.startswith("django-insecure-") or len(SECRET_KEY) < 50
):
    raise ImproperlyConfigured(
        "Set DJANGO_SECRET_KEY to a unique secret of at least 50 characters."
    )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS to the deployed hostnames.")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts.apps.AccountsConfig",
    "catalog.apps.CatalogConfig",
    "commerce.apps.CommerceConfig",
    "promotions.apps.PromotionsConfig",
    "reviews.apps.ReviewsConfig",
    "sitecore.apps.SitecoreConfig",
    "storefront.apps.StorefrontConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    *([] if DEBUG else ["whitenoise.middleware.WhiteNoiseMiddleware"]),
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "sitecore.security.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "sitecore.context_processors.storefront_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


def database_config() -> dict[str, object]:
    database_url = os.environ.get("DATABASE_URL", "sqlite:///db.sqlite3")
    parsed = urlsplit(database_url)
    if parsed.scheme in {"sqlite", "sqlite3"}:
        name = unquote(parsed.path.lstrip("/")) or ":memory:"
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / name if name != ":memory:" else name,
        }
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DATABASE_URL must use sqlite or postgresql.")

    query = parse_qs(parsed.query)
    options: dict[str, str] = {}
    if "sslmode" in query:
        options["sslmode"] = query["sslmode"][0]
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
        "CONN_MAX_AGE": 60,
        "OPTIONS": options,
    }


DATABASES = {"default": database_config()}
if not DEBUG and not os.environ.get("DATABASE_URL", "").startswith(
    ("postgres://", "postgresql://")
):
    raise ImproperlyConfigured("Deployed environments must use PostgreSQL through DATABASE_URL.")

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
CURRENCY_LOCALE = os.environ.get("CURRENCY_LOCALE", "en_US")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "storefront:account"
LOGOUT_REDIRECT_URL = "storefront:home"

EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Morrow <store@example.invalid>")
if not DEBUG and (not EMAIL_HOST or DEFAULT_FROM_EMAIL.endswith("example.invalid>")):
    raise ImproperlyConfigured(
        "Configure EMAIL_HOST and a real DEFAULT_FROM_EMAIL before deployment."
    )

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)
SECURE_HSTS_SECONDS = int(
    os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000")
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", default=False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if env_bool("DJANGO_TRUST_PROXY_SSL_HEADER", default=False)
    else None
)
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
CART_MAX_QUANTITY = int(os.environ.get("CART_MAX_QUANTITY", "10"))
SUPPLIER_STOCK_MAX_AGE_HOURS = int(os.environ.get("SUPPLIER_STOCK_MAX_AGE_HOURS", "24"))
if not 1 <= CART_MAX_QUANTITY <= 10:
    raise ImproperlyConfigured("CART_MAX_QUANTITY must be between one and ten.")
if SUPPLIER_STOCK_MAX_AGE_HOURS < 1:
    raise ImproperlyConfigured("SUPPLIER_STOCK_MAX_AGE_HOURS must be positive.")

PAYMENT_ENVIRONMENT = os.environ.get("PAYMENT_ENVIRONMENT", "sandbox").strip().lower()
if PAYMENT_ENVIRONMENT not in {"sandbox", "production"}:
    raise ImproperlyConfigured("PAYMENT_ENVIRONMENT must be 'sandbox' or 'production'.")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "")
PAYPAL_WEBHOOK_ID = os.environ.get("PAYPAL_WEBHOOK_ID", "")
POINTS_PROGRAM_ENABLED = env_bool("POINTS_PROGRAM_ENABLED", default=False)
GIVEAWAYS_ENABLED = env_bool("GIVEAWAYS_ENABLED", default=False)
REVIEW_RATE_LIMIT_HOURLY = int(os.environ.get("REVIEW_RATE_LIMIT_HOURLY", "5"))
if REVIEW_RATE_LIMIT_HOURLY < 1:
    raise ImproperlyConfigured("REVIEW_RATE_LIMIT_HOURLY must be positive.")
