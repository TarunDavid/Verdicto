"""Structured errors: JSON for API/HTMX requests, friendly pages otherwise."""
from django.http import JsonResponse
from django.shortcuts import render


def wants_json(request):
    return (request.path.startswith("/api/") or request.headers.get("HX-Request") == "true"
            or "application/json" in request.headers.get("Accept", ""))


def json_error(code, message, status, fields=None):
    return JsonResponse({"error": {"code": code, "message": message, "fields": fields or {}}},
                        status=status)


def error_response(request, status, code, message):
    if wants_json(request):
        return json_error(code, message, status)
    return render(request, "error.html", {"status": status, "message": message}, status=status)


def bad_request(request, exception=None):
    return error_response(request, 400, "bad_request", "The request was not valid.")


def permission_denied(request, exception=None):
    msg = str(exception) if exception and str(exception) else "You do not have permission to do this."
    return error_response(request, 403, "forbidden", msg)


def not_found(request, exception=None):
    return error_response(request, 404, "not_found", "We could not find that page.")


def server_error(request):
    return error_response(request, 500, "server_error", "Something went wrong. Please try again.")


def ratelimited(request, exception=None):
    return error_response(request, 429, "rate_limited", "Too many requests. Please slow down.")
