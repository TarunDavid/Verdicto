"""Role checks. Every view that needs a role uses one of these."""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def role_required(check, message="You do not have permission to do this."):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if request.path.startswith("/api/"):
                    raise PermissionDenied("Login required.")
                return redirect_to_login(request.get_full_path())
            if not check(request.user):
                raise PermissionDenied(message)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


login_required = role_required(lambda u: True)
verifier_required = role_required(
    lambda u: u.can_vote, "Only approved verifiers can vote."
)
admin_required = role_required(
    lambda u: u.is_admin_role, "Only admins can do this."
)
