import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.utils import translation
from django_ratelimit.exceptions import Ratelimited

from . import errors

logger = logging.getLogger("verdicto")


class StructuredErrorMiddleware:
    """Turns exceptions into structured JSON for API/HTMX calls. Never leaks stack traces."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exc):
        if isinstance(exc, Ratelimited):
            return errors.ratelimited(request)
        if not errors.wants_json(request):
            return None  # Django's handler403/404/500 render the HTML error page
        if isinstance(exc, PermissionDenied):
            return errors.permission_denied(request, exc)
        if isinstance(exc, Http404):
            return errors.not_found(request)
        logger.exception("Unhandled error on %s", request.path)
        return errors.server_error(request)


class UserLanguageMiddleware:
    """Use the logged-in user's preferred language unless they picked one with the switcher."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user and user.is_authenticated and settings.LANGUAGE_COOKIE_NAME not in request.COOKIES
                and user.preferred_language in dict(settings.LANGUAGES)):
            translation.activate(user.preferred_language)
            request.LANGUAGE_CODE = user.preferred_language
        return self.get_response(request)


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1", "testserver"}
ERROR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{code} · Verdicto</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0e0d0b;color:#efe9dd;
font:16px/1.5 Georgia,serif;text-align:center}}b{{display:block;font-size:64px;color:#E0383E}}a{{color:#efe9dd}}</style>
</head><body><div><b>{code}</b><p>{message}</p><p><a href="/">Back to Verdicto</a></p></div></body></html>"""
ERROR_MESSAGES = {403: "You don't have access to this page.", 404: "This page doesn't exist.",
                  500: "Something went wrong on our side."}


def is_remote(request):
    """True for visitors coming through a tunnel or any public address, not this laptop."""
    host = (request.META.get("HTTP_HOST") or request.META.get("SERVER_NAME", "")).lower()
    host = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
    return host not in LOCAL_HOSTS


def _error(code):
    return HttpResponse(ERROR_PAGE.format(code=code, message=ERROR_MESSAGES.get(code, ERROR_MESSAGES[500])),
                        status=code)


class TunnelGuardMiddleware:
    """When the app is shared through a tunnel (e.g. a Cloudflare quick tunnel), people using the link:
    - can't reach /admin/ at all (it only works on this laptop, at 127.0.0.1 or localhost);
    - never see Django's debug pages (stack traces, settings, URL lists), even with DEBUG=True.
    Visitors on this laptop see everything as before."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        remote = is_remote(request)
        if remote and request.path.startswith("/admin"):
            return _error(404)
        response = self.get_response(request)
        # Django's debug pages (technical 404/500, CSRF failure help) all say "DEBUG = True".
        if (remote and settings.DEBUG and response.status_code >= 400
                and response.get("Content-Type", "").startswith("text/html")
                and not getattr(response, "streaming", False) and b"DEBUG = True" in response.content):
            return _error(response.status_code if response.status_code in ERROR_MESSAGES else 500)
        return response
