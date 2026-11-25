"""Intelligent verifier assignment (PRD section 9). Deterministic and explainable.

match = domain match x language match x reliability x availability (x 1.1 locality bonus)
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from accounts.models import User

from . import engine
from .models import Assignment

RELATED = {
    "health": {"science"},
    "science": {"health", "technology"},
    "technology": {"science"},
    "education": {"government"},
    "government": {"education", "local-news", "politics"},
    "local-news": {"government"},
    "politics": {"government"},
}


def rank_verifiers(claim, exclude_ids=()):
    verifiers = (User.objects.filter(role=User.Role.VERIFIER, verifier_approved=True, is_active=True)
                 .exclude(pk__in=exclude_ids)
                 .annotate(open_tasks=Count("assignments",
                                            filter=Q(assignments__status=Assignment.Status.ASSIGNED)))
                 .prefetch_related("verifier_stats"))
    ranked = []
    for v in verifiers:
        domains = set(v.domains or [])
        if claim.domain in domains:
            domain_match, why = 1.0, claim.get_domain_display()
        elif RELATED.get(claim.domain, set()) & domains:
            domain_match, why = 0.6, "related domain"
        elif claim.domain == "other":
            domain_match, why = 0.5, "general"
        else:
            continue
        lang_match = 1.0 if claim.language in (v.languages or []) else 0.5
        stats = [(s.domain, s.language, s.correct, s.total) for s in v.verifier_stats.all()]
        reliability = engine.verifier_weight(stats, claim.domain, claim.language, v.work_email_verified)
        availability = 1 / (1 + v.open_tasks)
        locality = 1.1 if claim.district and v.district and claim.district == v.district else 1.0
        score = domain_match * lang_match * reliability * availability * locality
        reason = (f"{why} · {'speaks ' + claim.get_language_display() if lang_match == 1 else 'other language'}"
                  f" · reliability {reliability:.2f} · {v.open_tasks} open"
                  f"{' · same district' if locality > 1 else ''}")
        ranked.append((v, round(score, 4), reason))
    ranked.sort(key=lambda r: -r[1])
    return ranked


def assign_verifiers(claim, n=None):
    """Assign the top-N eligible verifiers who are not already assigned and have not voted."""
    n = n or settings.ASSIGN_TOP_N
    active = claim.assignments.filter(status__in=[Assignment.Status.ASSIGNED, Assignment.Status.DONE]).count()
    needed = max(0, n - active)
    if not needed:
        return []
    exclude = set(claim.assignments.values_list("verifier_id", flat=True))
    exclude |= set(claim.votes.values_list("verifier_id", flat=True))
    created = []
    for v, score, reason in rank_verifiers(claim, exclude)[:needed]:
        created.append(Assignment.objects.create(claim=claim, verifier=v, score=score, reason=reason))
    return created


def reassign_stale(now=None):
    """Expire assignments with no vote after the timeout and hand the claim to the next verifier."""
    cutoff = (now or timezone.now()) - timedelta(hours=settings.ASSIGNMENT_TIMEOUT_HOURS)
    stale = Assignment.objects.filter(status=Assignment.Status.ASSIGNED, created_at__lt=cutoff,
                                      claim__status__in=["PENDING", "UNDER_REVIEW"])
    claims = set()
    for a in stale.select_related("claim"):
        a.status = Assignment.Status.EXPIRED
        a.save(update_fields=["status", "updated_at"])
        claims.add(a.claim)
    for claim in claims:
        assign_verifiers(claim)
    return len(claims)
