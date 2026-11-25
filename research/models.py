from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from claims.models import Claim


class ResearchRun(models.Model):
    """One AI research pass for a claim. Provisional only: it never sets the claim's verdict."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    class Label(models.TextChoices):
        LIKELY_TRUE = "LIKELY_TRUE", _("Likely true")
        LIKELY_FALSE = "LIKELY_FALSE", _("Likely false")
        MIXED = "MIXED", _("Mixed evidence / possibly misleading")
        UNCLEAR = "UNCLEAR", _("Unclear (not enough evidence)")

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="research_runs")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED)
    step = models.CharField(max_length=80, default="Queued")
    checkable_claim = models.TextField(blank=True)
    queries = models.JSONField(default=list, blank=True)
    source_filter = models.CharField(max_length=12, default="all")
    deep = models.BooleanField(default=False)
    label = models.CharField(max_length=12, choices=Label.choices, blank=True)
    truth_score = models.FloatField(null=True, blank=True)   # 0-100, from the formula
    confidence = models.CharField(max_length=6, blank=True)  # low / medium / high
    summary = models.TextField(blank=True)
    facts = models.JSONField(default=list, blank=True)
    assumptions = models.JSONField(default=list, blank=True)
    conflicts = models.JSONField(default=list, blank=True)
    models_used = models.JSONField(default=list, blank=True)
    connectors = models.JSONField(default=dict, blank=True)  # name -> "ok (n)" / "error: ..."
    errors = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_running(self):
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)

    @property
    def duration(self):
        if self.started_at and self.finished_at:
            return round((self.finished_at - self.started_at).total_seconds(), 1)
        return None


class Source(models.Model):
    class Stance(models.TextChoices):
        SUPPORTS = "supports", _("Supports")
        REFUTES = "refutes", _("Refutes")
        NEUTRAL = "neutral", _("Neutral")
        IRRELEVANT = "irrelevant", _("Irrelevant")

    run = models.ForeignKey(ResearchRun, on_delete=models.CASCADE, related_name="sources")
    url = models.URLField(max_length=800)
    title = models.CharField(max_length=300, blank=True)
    publisher = models.CharField(max_length=120, blank=True)
    connector = models.CharField(max_length=30)
    tier = models.CharField(max_length=20)         # official / fact-checker / academic / news / ...
    tier_weight = models.FloatField()
    published = models.DateTimeField(null=True, blank=True)
    snippet = models.TextField(blank=True)
    stance = models.CharField(max_length=10, choices=Stance.choices, default=Stance.NEUTRAL)
    quote = models.TextField(blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-tier_weight", "id"]

    def feedback_counts(self):
        votes = list(self.feedback.values_list("useful", flat=True))
        return {"useful": sum(votes), "wrong": len(votes) - sum(votes)}


class SourceFeedback(models.Model):
    """A verifier marks an AI-found source as useful or wrong (helps other verifiers; audit trail)."""
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="feedback")
    verifier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    useful = models.BooleanField()
    created_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "verifier"], name="one_feedback_per_verifier"),
        ]
