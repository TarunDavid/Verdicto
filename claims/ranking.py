"""Feed ranking (PRD section 11): importance = urgency x reach x domain risk x recency decay."""
import math

from django.utils import timezone

URGENCY = {"high": 3, "medium": 2, "low": 1}
DOMAIN_RISK = {"health": 1.5, "local-news": 1.3, "education": 1.3, "government": 1.2,
               "politics": 1.2, "science": 1.0, "technology": 1.0, "other": 0.9}
HALF_LIFE_HOURS = 24


def importance(claim, now=None):
    age_h = ((now or timezone.now()) - claim.created_at).total_seconds() / 3600
    decay = 0.5 ** (max(age_h, 0) / HALF_LIFE_HOURS)
    reach = math.log(1 + claim.submission_count)
    return URGENCY.get(claim.urgency, 2) * reach * DOMAIN_RISK.get(claim.domain, 1.0) * decay
