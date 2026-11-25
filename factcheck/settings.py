from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost", cast=Csv())
SITE_URL = config("SITE_URL", default="http://127.0.0.1:8000")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "claims",
    "verification",
    "research",
    "chat",
    "django_ratelimit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",                # English / Kannada UI
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",  # every page needs login unless marked
    "factcheck.middleware.UserLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "factcheck.middleware.StructuredErrorMiddleware",
]

ROOT_URLCONF = "factcheck.urls"

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
                "factcheck.context.shell",
                "factcheck.context.assets",
            ],
        },
    },
]

WSGI_APPLICATION = "factcheck.wsgi.application"

def _database(url):
    """SQLite by default; DATABASE_URL=postgres://user:pass@host:5432/name for production
    (needs the psycopg package)."""
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3",
                "OPTIONS": {"timeout": 20}}  # research runs in a background thread
    from urllib.parse import unquote, urlparse
    u = urlparse(url)
    if u.scheme not in ("postgres", "postgresql"):
        raise ValueError("DATABASE_URL must be a postgres:// URL")
    return {"ENGINE": "django.db.backends.postgresql", "NAME": u.path.lstrip("/"),
            "USER": unquote(u.username or ""), "PASSWORD": unquote(u.password or ""),
            "HOST": u.hostname or "", "PORT": str(u.port or 5432), "CONN_MAX_AGE": 60}


DATABASES = {"default": _database(config("DATABASE_URL", default=""))}

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "accounts.backends.PhoneBackend",              # phone + password (app login)
    "django.contrib.auth.backends.ModelBackend",   # username + password (Django admin)
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "chat"
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("kn", "ಕನ್ನಡ")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"   # `collectstatic` target for production
STATICFILES_DIRS = [BASE_DIR / "static"]
# Local Tailwind build + HTMX by default; True loads them from public CDNs (handy while editing classes).
USE_CDN_ASSETS = config("USE_CDN_ASSETS", default=False, cast=bool)
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

# Work-email OTPs print to the console in development. For real email set
# EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend and the EMAIL_HOST_* values.
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = config("EMAIL_HOST", default="localhost")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="Verdicto <no-reply@verdicto.local>")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# django-ratelimit uses the default cache. "locmem" is fine for one process (the demo); use "db"
# (run `manage.py createcachetable`) or a redis:// URL (needs the redis package) with several workers.
CACHE_BACKEND = config("CACHE_BACKEND", default="locmem")
if CACHE_BACKEND == "db":
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache",
                          "LOCATION": "verdicto_cache"}}
elif CACHE_BACKEND.startswith("redis://"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache",
                          "LOCATION": CACHE_BACKEND}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
RATELIMIT_ENABLE = config("RATELIMIT_ENABLE", default=True, cast=bool)
RATELIMIT_VIEW = "factcheck.errors.ratelimited"
SILENCED_SYSTEM_CHECKS = ["django_ratelimit.E003", "django_ratelimit.W001"] if CACHE_BACKEND == "locmem" else []

# AI research (PRD section 7). The app works without a key, using fallbacks.
GROQ_API_KEY = config("GROQ_API_KEY", default="")
GROQ_MODEL = config("GROQ_MODEL", default="openai/gpt-oss-120b")          # assess + summarise
GROQ_FAST_MODEL = config("GROQ_FAST_MODEL", default="qwen/qwen3.8-27b")    # classify + follow-ups
GROQ_VISION_MODEL = config("GROQ_VISION_MODEL", default="qwen/qwen3.8-27b")  # screenshot OCR
GROQ_SEARCH_MODEL = config("GROQ_SEARCH_MODEL", default="openai/gpt-oss-20b")  # browser_search tool
GROQ_TIMEOUT = config("GROQ_TIMEOUT", default=25, cast=int)
GOOGLE_FACTCHECK_API_KEY = config("GOOGLE_FACTCHECK_API_KEY", default="")
SEMANTIC_SCHOLAR_API_KEY = config("SEMANTIC_SCHOLAR_API_KEY", default="")   # optional
REDDIT_CLIENT_ID = config("REDDIT_CLIENT_ID", default="")                   # optional (official API)
REDDIT_CLIENT_SECRET = config("REDDIT_CLIENT_SECRET", default="")
RESEARCH_CONNECTORS = config(
    "RESEARCH_CONNECTORS",
    default="groq_web,google_news,official_news,factcheck,wikipedia,openalex,pubmed,semantic_scholar,reddit",
    cast=Csv(),
)
RESEARCH_CONTACT = config("RESEARCH_CONTACT", default="https://example.org/verdicto")
RESEARCH_SYNC = config("RESEARCH_SYNC", default=False, cast=bool)  # run inline (tests)
# "thread": research runs in a background thread of the web server (simple, the demo default).
# "worker": web requests only queue research; run `manage.py run_worker` as a separate process.
RESEARCH_MODE = config("RESEARCH_MODE", default="thread")
ASSIGN_TOP_N = 3
ASSIGNMENT_TIMEOUT_HOURS = 2

PHONE_OTP_ENABLED = config("PHONE_OTP_ENABLED", default=False, cast=bool)  # SMS code at sign-up
SMS_BACKEND = config("SMS_BACKEND", default="console")   # "console" (dev) or "twilio"
TWILIO_ACCOUNT_SID = config("TWILIO_ACCOUNT_SID", default="")
TWILIO_AUTH_TOKEN = config("TWILIO_AUTH_TOKEN", default="")
TWILIO_FROM_NUMBER = config("TWILIO_FROM_NUMBER", default="")

DEMO_PASSWORD = config("DEMO_PASSWORD", default="demo-pass-2026")
ADMIN_PASSWORD = config("ADMIN_PASSWORD", default="") or DEMO_PASSWORD  # seed_demo's admin account

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False}},
}


# ---------- Production hardening (applies when DEBUG=False) ----------
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=3600, cast=int)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_REFERRER_POLICY = "same-origin"
