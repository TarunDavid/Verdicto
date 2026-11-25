from django.db import models

from claims.models import Claim


class ResearchRun(models.Model):
    """One AI research pass for a claim. Provisional only: it never sets the claim's verdict."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    class Label(models.TextChoices):
        LIKELY_TRUE = "LIKELY_TRUE", "Likely true"
        LIKELY_FALSE = "LIKELY_FALSE", "Likely false"
        MIXED = "MIXED", "Mixed evidence / possibly misleading"
        UNCLEAR = "UNCLEAR", "Unclear (not enough evidence)"

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
        SUPPORTS = "supports", "Supports"
        REFUTES = "refutes", "Refutes"
        NEUTRAL = "neutral", "Neutral"
        IRRELEVANT = "irrelevant", "Irrelevant"

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
