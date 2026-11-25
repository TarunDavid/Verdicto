import logging

from django.core.exceptions import PermissionDenied
from django.http import Http404
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
