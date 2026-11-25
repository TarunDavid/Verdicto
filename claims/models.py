import secrets

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from accounts.models import DOMAINS, LANGUAGES

from .validators import validate_safe_url


def new_slug():
    return secrets.token_urlsafe(6)


def screenshot_path(instance, filename):
    """Random file names: never trust (or reveal) the uploaded name."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    return f"screenshots/{secrets.token_hex(16)}.{ext}"


class Verdict(models.TextChoices):
    TRUE = "TRUE", _("True")
    FALSE = "FALSE", _("False")
    PARTIALLY_TRUE = "PARTIALLY_TRUE", _("Partially true")
    MISLEADING = "MISLEADING", _("Misleading")
    UNVERIFIED = "UNVERIFIED", _("Unverified (insufficient evidence)")


URGENCY = [("low", "Low"), ("medium", "Medium"), ("high", "High")]


class Claim(models.Model):
    class Status(models.TextChoices):
        RESEARCHING = "RESEARCHING", "AI researching"
        PENDING = "PENDING", "Pending verification"
        UNDER_REVIEW = "UNDER_REVIEW", "Under investigation"
        FINAL = "FINAL", "Verified"
        REJECTED = "REJECTED", "Rejected"

    text = models.TextField(max_length=2000)
    normalized_text = models.TextField(blank=True)
    source_url = models.URLField(blank=True, validators=[validate_safe_url], max_length=500)
    screenshot = models.ImageField(upload_to=screenshot_path, blank=True)
    language = models.CharField(max_length=2, choices=LANGUAGES, default="en")
    domain = models.CharField(max_length=20, choices=DOMAINS, default="other")
    district = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    verdict = models.CharField(max_length=16, choices=Verdict.choices, null=True, blank=True)
    leading = models.CharField(max_length=16, choices=Verdict.choices, null=True, blank=True)
    confidence = models.FloatField(default=0.0)
    locked_by_admin = models.BooleanField(default=False)
    urgency = models.CharField(max_length=6, choices=URGENCY, default="medium")
    is_hidden = models.BooleanField(default=False, help_text="Hidden by moderation; not shown publicly.")
    moderation_note = models.CharField(max_length=200, blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                     related_name="claims")
    submission_count = models.PositiveIntegerField(default=1)
    duplicate_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True)
    share_slug = models.CharField(max_length=12, unique=True, default=new_slug)
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.text[:60]

    @property
    def display_verdict(self):
        return self.verdict if self.status == self.Status.FINAL else None

    @property
    def confidence_pct(self):
        return round(self.confidence * 100)
