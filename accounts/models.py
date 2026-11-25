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
    ("finance", "Finance"),
    ("other", "Other"),
]


class User(AbstractUser):
    class Role(models.TextChoices):
        CITIZEN = "citizen", "Regular user"
        VERIFIER = "verifier", "Verifier"
        ADMIN = "admin", "Admin"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CITIZEN)
    # Login identifier. Private: never rendered on public pages.
    phone = models.CharField(max_length=13, unique=True, null=True, blank=True)
    preferred_language = models.CharField(max_length=2, choices=LANGUAGES, default="en")

    # Verifier profile fields (kept on User for the MVP; see README).
    languages = models.JSONField(default=list, blank=True)
    domains = models.JSONField(default=list, blank=True)
    district = models.CharField(max_length=60, blank=True)
    verifier_approved = models.BooleanField(default=False)
    work_email_verified = models.BooleanField(default=False)
    org_domain = models.CharField(max_length=100, blank=True)
    profession = models.CharField(max_length=80, blank=True)
    organisation = models.CharField(max_length=120, blank=True)
    work_email = models.EmailField(blank=True)
    linkedin_url = models.URLField(blank=True)

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def initials(self):
        parts = self.display_name.split()
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    @property
    def is_pending_verifier(self):
        return self.role == self.Role.VERIFIER and not self.verifier_approved

    @property
    def can_give_opinion(self):
        """Community vote: everyone except approved verifiers (they vote as verifiers)."""
        return not self.can_vote

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
