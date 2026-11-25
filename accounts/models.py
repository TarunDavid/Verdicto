from functools import cached_property

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

LANGUAGES = [("en", _("English")), ("kn", _("Kannada"))]
DOMAINS = [
    ("health", _("Health")),
    ("education", _("Education")),
    ("government", _("Government")),
    ("local-news", _("Local news")),
    ("technology", _("Technology")),
    ("science", _("Science")),
    ("politics", _("Politics")),
    ("finance", _("Finance")),
    ("other", _("Other")),
]


class User(AbstractUser):
    class Role(models.TextChoices):
        CITIZEN = "citizen", _("Regular user")
        VERIFIER = "verifier", _("Verifier")
        ADMIN = "admin", _("Admin")

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CITIZEN)
    # Login identifier. Private: never rendered on public pages.
    phone = models.CharField(max_length=13, unique=True, null=True, blank=True)
    preferred_language = models.CharField(max_length=2, choices=LANGUAGES, default="en")

    domains = models.JSONField(default=list, blank=True)    # fields of interest / expertise
    district = models.CharField(max_length=60, blank=True)
    # Verifier-only details live in VerifierProfile (see the shortcuts below).
    # Regular users' community-vote track record (shown on profile; a path to becoming a verifier).
    community_correct = models.PositiveIntegerField(default=0)
    community_total = models.PositiveIntegerField(default=0)

    @property
    def community_accuracy(self):
        return round(100 * self.community_correct / self.community_total) if self.community_total else None

    # ---- Shortcuts to VerifierProfile -------------------------------------------------------
    # `user.languages`, `user.work_email_verified`, `user.verifier_approved`, ... read from the
    # profile; setting them (also as create_user(...) keyword arguments) is stored on the profile
    # when the user is saved. Regular users have no profile and get the defaults.

    def _profile(self):
        try:
            return self.verifier_profile
        except VerifierProfile.DoesNotExist:
            return None

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        pending = getattr(self, "_pending_profile", None)
        profile = self._profile()
        if self.role == self.Role.VERIFIER and (pending or profile is None):
            if profile is None:
                profile = VerifierProfile(user=self)
            for name, value in (pending or {}).items():
                setattr(profile, name, value)
            profile.save()
        self._pending_profile = {}

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

    @property
    def is_ai_agent(self):
        profile = self._profile()
        return bool(profile and profile.is_ai_agent)

    # ---- Track record across all fields (shown as "92% accurate in 12 verdicts") ------------
    @cached_property
    def verdict_record(self):
        """(correct, total) over every scored verdict, i.e. votes on claims that became final."""
        agg = self.verifier_stats.aggregate(c=Sum("correct"), t=Sum("total"))
        return agg["c"] or 0, agg["t"] or 0

    @property
    def verdicts_scored(self):
        return self.verdict_record[1]

    @property
    def verdict_accuracy(self):
        correct, total = self.verdict_record
        return round(100 * correct / total) if total else None

    def badges(self):
        out = []
        if self.work_email_verified:
            out.append(f"Work email verified · {self.org_domain}")
        if not self.verifier_stats.exists():
            out.append("New verifier")
        return out


class VerifierProfile(models.Model):
    """Verifier-only details (SPEC §5). One per verifier; regular users don't have one."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="verifier_profile")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    languages = models.JSONField(default=list, blank=True)    # languages they can verify in
    profession = models.CharField(max_length=80, blank=True)
    organisation = models.CharField(max_length=120, blank=True)
    work_email = models.EmailField(blank=True)
    work_email_verified = models.BooleanField(default=False)
    org_domain = models.CharField(max_length=100, blank=True)
    linkedin_url = models.URLField(blank=True)
    # An automated verifier (see research/agents.py): researches claims itself and votes like a
    # verifier, scored on its track record like everyone else. Cannot log in.
    is_ai_agent = models.BooleanField(default=False)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.display_name} ({self.get_status_display()})"


class WorkEmailOTP(models.Model):
    """One-time code for verifying a verifier's work email. Only a hash of the code is stored."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_otps")
    email = models.EmailField()
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


def _shortcut(name, default):
    def getter(self):
        profile = self._profile()
        if profile is not None:
            return getattr(profile, name)
        pending = getattr(self, "_pending_profile", {})
        return pending.get(name, default() if callable(default) else default)

    def setter(self, value):
        self.__dict__.setdefault("_pending_profile", {})[name] = value
        profile = self._profile() if self.pk else None
        if profile is not None:
            setattr(profile, name, value)

    return property(getter, setter)


for _name, _default in [("profession", ""), ("organisation", ""), ("work_email", ""),
                        ("work_email_verified", False), ("org_domain", ""), ("linkedin_url", ""),
                        ("approved_by", None), ("approved_at", None)]:
    setattr(User, _name, _shortcut(_name, _default))


def _languages_get(self):
    profile = self._profile()
    if profile is not None:
        return profile.languages
    return getattr(self, "_pending_profile", {}).get("languages") or [self.preferred_language]


def _approved_get(self):
    profile = self._profile()
    if profile is not None:
        return profile.status == VerifierProfile.Status.APPROVED
    return getattr(self, "_pending_profile", {}).get("status") == VerifierProfile.Status.APPROVED


def _approved_set(self, value):
    _shortcut("status", None).fset(self, VerifierProfile.Status.APPROVED if value else VerifierProfile.Status.PENDING)


User.languages = property(_languages_get, _shortcut("languages", list).fset)
User.verifier_approved = property(_approved_get, _approved_set)
