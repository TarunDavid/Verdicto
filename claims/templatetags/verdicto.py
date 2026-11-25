from django import template
from django.utils.translation import gettext as _

from claims.models import Claim, Verdict

register = template.Library()
LABELS = {**dict(Claim.Status.choices), **dict(Verdict.choices)}


@register.filter
def status_label(code):
    """Translate a stored status/verdict code (e.g. 'UNDER_REVIEW') for display."""
    if not code:
        return _("NEW")
    return str(LABELS.get(code, code))
