from django.contrib.auth.models import AbstractUser
from django.db import models

LANGUAGES = [("en", "English"), ("kn", "Kannada")]
DOMAINS = [
    ("health", "Health"),
    ("education", "Education"),
    ("government", "Government"),
    ("local-news", "Local news"),
    ("technology", "Technology"),
    ("science", "Science"),
    ("politics", "Politics"),
    ("other", "Other"),
]


class User(AbstractUser):
    class Role(models.TextChoices):
        CITIZEN = "citizen", "Citizen"
        VERIFIER = "verifier", "Verifier"
        ADMIN = "admin", "Admin"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CITIZEN)
    preferred_language = models.CharField(max_length=2, choices=LANGUAGES, default="en")

    # Verifier profile fields (kept on User for the MVP; see README).
    languages = models.JSONField(default=list, blank=True)
    domains = models.JSONField(default=list, blank=True)
    district = models.CharField(max_length=60, blank=True)
    verifier_approved = models.BooleanField(default=False)
    work_email_verified = models.BooleanField(default=False)
    org_domain = models.CharField(max_length=100, blank=True)

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def can_vote(self):
        return self.role == self.Role.VERIFIER and self.verifier_approved

    def badges(self):
        out = []
        if self.work_email_verified:
            out.append(f"Work email verified · {self.org_domain}")
        if not self.verifier_stats.exists():
            out.append("New verifier")
        return out
