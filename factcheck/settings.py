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
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",  # every page needs login unless marked
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
            ],
        },
    },
]

WSGI_APPLICATION = "factcheck.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 20},  # research runs in a background thread
    }
}

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
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# django-ratelimit uses the default cache. LocMem is fine for a single-process demo.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
RATELIMIT_ENABLE = config("RATELIMIT_ENABLE", default=True, cast=bool)
RATELIMIT_VIEW = "factcheck.errors.ratelimited"
SILENCED_SYSTEM_CHECKS = ["django_ratelimit.E003", "django_ratelimit.W001"]

# AI research (PRD section 7). The app works without a key, using fallbacks.
GROQ_API_KEY = config("GROQ_API_KEY", default="")
GROQ_MODEL = config("GROQ_MODEL", default="openai/gpt-oss-120b")          # assess + summarise
GROQ_FAST_MODEL = config("GROQ_FAST_MODEL", default="qwen/qwen3.8-27b")    # classify + follow-ups
GROQ_SEARCH_MODEL = config("GROQ_SEARCH_MODEL", default="openai/gpt-oss-20b")  # browser_search tool
GROQ_TIMEOUT = config("GROQ_TIMEOUT", default=25, cast=int)
GOOGLE_FACTCHECK_API_KEY = config("GOOGLE_FACTCHECK_API_KEY", default="")
RESEARCH_CONNECTORS = config(
    "RESEARCH_CONNECTORS",
    default="groq_web,google_news,factcheck,wikipedia,openalex",
    cast=Csv(),
)
RESEARCH_CONTACT = config("RESEARCH_CONTACT", default="https://example.org/verdicto")
RESEARCH_SYNC = config("RESEARCH_SYNC", default=False, cast=bool)  # run inline (tests)
ASSIGN_TOP_N = 3
ASSIGNMENT_TIMEOUT_HOURS = 2

DEMO_PASSWORD = config("DEMO_PASSWORD", default="demo-pass-2026")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False}},
}
