"""Input safety: URL validation and text sanitising."""
import html

import nh3
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

BLOCKED_SCHEMES = ("javascript:", "data:", "vbscript:", "file:")
_url_validator = URLValidator(schemes=["http", "https"])


def validate_safe_url(value):
    """Allow only http/https. Explicitly reject javascript:/data: (case-insensitive, trimmed)."""
    cleaned = (value or "").strip()
    compact = "".join(cleaned.split()).lower()  # defeats "java script:" / tab tricks
    if compact.startswith(BLOCKED_SCHEMES):
        raise ValidationError("This link type is not allowed.", code="blocked_scheme")
    _url_validator(cleaned)
    return cleaned


def sanitize_text(value):
    """Strip every HTML tag with nh3. Templates still autoescape on output."""
    cleaned = nh3.clean(value or "", tags=set())
    return html.unescape(cleaned).strip()
