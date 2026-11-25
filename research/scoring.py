"""Deterministic truth score (PRD section 10.1). The LLM explains the score; it never sets it."""
from datetime import timedelta

from django.utils import timezone

MIN_EVIDENCE = 1.0   # one official/fact-check source, or two news sources, before committing to a lean


def recency_factor(published, now=None):
    if published is None:
        return 1.0
    age = (now or timezone.now()) - published
    if age <= timedelta(days=30):
        return 1.0
    if age <= timedelta(days=365):
        return 0.9
    return 0.75


def truth_score(sources, now=None):
    """sources: iterable of (stance, tier_weight, published). Returns (score 0-100 | None, label, confidence)."""
    support = refute = 0.0
    for stance, weight, published in sources:
        w = weight * recency_factor(published, now)
        if stance == "supports":
            support += w
        elif stance == "refutes":
            refute += w
    evidence = support + refute
    if evidence < MIN_EVIDENCE:
        return None, "UNCLEAR", "low"
    score = 100 * support / evidence
    if score >= 70:
        label = "LIKELY_TRUE"
    elif score <= 30:
        label = "LIKELY_FALSE"
    else:
        label = "MIXED"
    confidence = "high" if evidence >= 3 else "medium" if evidence >= 1.6 else "low"
    return round(score, 1), label, confidence
