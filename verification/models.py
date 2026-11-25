from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from accounts.models import DOMAINS, LANGUAGES
from claims.models import Claim, Verdict
from claims.validators import validate_safe_url

TRUSTED_SUFFIXES = (".gov.in", ".nic.in", "vtu.ac.in", "pib.gov.in", "altnews.in", "boomlive.in",
                    "factly.in")


def is_trusted_host(host):
    return any(host == d.lstrip(".") or host.endswith("." + d.lstrip(".")) for d in TRUSTED_SUFFIXES)


class Vote(models.Model):
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="votes")
    verifier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="votes")
    verdict = models.CharField(max_length=16, choices=Verdict.choices)
    note = models.CharField(max_length=500, blank=True)
    weight_at_vote = models.FloatField()
    scored_as = models.CharField(max_length=16, choices=Verdict.choices, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["claim", "verifier"], name="one_vote_per_verifier"),
        ]


class Evidence(models.Model):
    vote = models.ForeignKey(Vote, on_delete=models.CASCADE, related_name="evidence")
    url = models.URLField(max_length=500, validators=[validate_safe_url])
    is_official = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        validate_safe_url(self.url)
        host = (urlparse(self.url).hostname or "").lower()
        self.is_official = is_trusted_host(host)
        super().save(*args, **kwargs)


class VerdictLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("VerdictLog is append-only.")

    def delete(self):
        raise ValidationError("VerdictLog is append-only.")


class VerdictLog(models.Model):
    """Append-only: rows can be created, never changed or removed."""
    claim = models.ForeignKey(Claim, on_delete=models.PROTECT, related_name="logs")
    old_status = models.CharField(max_length=12, blank=True)
    new_status = models.CharField(max_length=12)
    old_verdict = models.CharField(max_length=16, blank=True, null=True)
    new_verdict = models.CharField(max_length=16, blank=True, null=True)
    old_confidence = models.FloatField(default=0)
    new_confidence = models.FloatField(default=0)
    reason = models.CharField(max_length=300)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                   blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = VerdictLogQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("VerdictLog is append-only.")
        if not (self.reason or "").strip():
            raise ValidationError("A reason is required.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("VerdictLog is append-only.")


class VerifierStat(models.Model):
    verifier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="verifier_stats")
    domain = models.CharField(max_length=20, choices=DOMAINS)
    language = models.CharField(max_length=2, choices=LANGUAGES)
    correct = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["verifier", "domain", "language"],
                                    name="one_stat_per_verifier_domain_language"),
        ]


class Assignment(models.Model):
    """A claim routed to a verifier by the assignment ranking (PRD section 9)."""

    class Status(models.TextChoices):
        ASSIGNED = "assigned", _("Assigned")
        DONE = "done", _("Done")
        EXPIRED = "expired", _("Expired")

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="assignments")
    verifier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="assignments")
    score = models.FloatField()
    reason = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ASSIGNED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-score"]
        constraints = [
            models.UniqueConstraint(fields=["claim", "verifier"], name="one_assignment_per_verifier"),
        ]


class CommunityVote(models.Model):
    """A regular user's opinion. Low weight (see engine.community_weights); changeable until FINAL."""
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="community_votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="community_votes")
    verdict = models.CharField(max_length=16, choices=Verdict.choices)
    reason = models.CharField(max_length=200, blank=True)
    scored_as = models.CharField(max_length=16, choices=Verdict.choices, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["claim", "user"], name="one_community_vote_per_user"),
        ]
