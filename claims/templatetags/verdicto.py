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


@register.simple_tag
def agent_tally(claim):
    """How the AI verifiers have answered on a claim, for the chat card and the claim page:
    {"answered", "expected", "working", "abstained", "rows": [(verdict code, label, count)]}."""
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    from research.agents import AGENTS
    from research.models import ResearchRun

    verdicts = list(claim.votes.filter(verifier__verifier_profile__is_ai_agent=True)
                    .values_list("verdict", flat=True))
    abstained = claim.assignments.filter(verifier__verifier_profile__is_ai_agent=True,
                                         reason__contains="abstained").count()
    expected = min(settings.AI_AGENTS_PER_CLAIM or len(AGENTS), len(AGENTS))
    answered = len(verdicts) + abstained
    is_open = claim.status in (Claim.Status.PENDING, Claim.Status.UNDER_REVIEW)
    recent = claim.created_at >= timezone.now() - timedelta(minutes=20)
    running = ResearchRun.all_runs.filter(claim=claim, agent__isnull=False, status="running").exists()
    working = bool(settings.AI_AGENTS_ENABLED and is_open and answered < expected and (running or recent))
    counts = {}
    for v in verdicts:
        counts[v] = counts.get(v, 0) + 1
    rows = [(code, str(LABELS.get(code, code)), n) for code, n in sorted(counts.items(), key=lambda x: -x[1])]
    return {"answered": answered, "expected": expected, "working": working, "abstained": abstained,
            "voted": len(verdicts), "rows": rows, "pending_slots": range(max(0, expected - answered))}
