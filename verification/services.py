"""Database side of verification: casting votes, recomputing verdicts, logging, scoring."""
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from claims.models import Claim

from . import engine
from .models import Assignment, CommunityVote, Evidence, VerdictLog, VerifierStat, Vote


class AlreadyVoted(Exception):
    pass


class ClaimClosed(Exception):
    pass


def weight_for(verifier, claim):
    stats = verifier.verifier_stats.values_list("domain", "language", "correct", "total")
    return engine.verifier_weight(stats, claim.domain, claim.language, verifier.work_email_verified)


@transaction.atomic
def cast_vote(claim, verifier, verdict, note="", evidence_urls=()):
    claim = Claim.objects.select_for_update().get(pk=claim.pk)
    try:
        with transaction.atomic():
            vote = Vote.objects.create(claim=claim, verifier=verifier, verdict=verdict, note=note,
                                       weight_at_vote=weight_for(verifier, claim))
    except IntegrityError:
        raise AlreadyVoted("You have already voted on this claim.")
    for url in evidence_urls:
        Evidence.objects.create(vote=vote, url=url)
    Assignment.objects.filter(claim=claim, verifier=verifier).update(status=Assignment.Status.DONE)
    recompute(claim, trigger=vote)
    return vote


def recompute(claim, trigger=None):
    """Recompute after every vote. Log any status/verdict change. Re-score track records."""
    if claim.locked_by_admin:
        return claim
    votes = list(claim.votes.values_list("verdict", "weight_at_vote"))
    community = list(claim.community_votes.values_list("verdict", flat=True))
    result = engine.compute_verdict(votes, community)
    old = (claim.status, claim.verdict, claim.confidence)

    claim.leading = result.leading
    claim.confidence = result.confidence
    if result.status == "FINAL":
        claim.status = Claim.Status.FINAL
    elif claim.status == Claim.Status.RESEARCHING and not result.vote_count:
        pass  # community opinions during AI research don't change the status
    else:
        # Human votes exist but no consensus yet: "Under investigation".
        claim.status = Claim.Status.UNDER_REVIEW if result.vote_count else Claim.Status.PENDING
    claim.verdict = result.verdict
    if result.status == "FINAL" and old[0] != "FINAL":
        claim.finalized_at = timezone.now()
    claim.save()

    if (claim.status, claim.verdict) != old[:2]:
        if isinstance(trigger, CommunityVote):
            reason = (f"Community vote ({trigger.verdict}) moved {result.leading} confidence to "
                      f"{result.confidence:.0%}")
        elif trigger is not None:
            reason = (f"Vote by {trigger.verifier.username} ({trigger.verdict}) moved "
                      f"{result.leading} confidence to {result.confidence:.0%}")
        else:
            reason = f"Recomputed: {result.leading} at {result.confidence:.0%}"
        if claim.status == "FINAL":
            reason += f" with {result.vote_count} votes; verdict is now FINAL {claim.verdict}"
        _log(claim, old, reason, changed_by=None)
        if claim.status == Claim.Status.FINAL:
            notify_threads(claim, f"✅ Verified: {claim.get_verdict_display().upper()} "
                                  f"({claim.confidence:.0%} weighted agreement) by "
                                  f"{result.vote_count} verifiers.")
    score_votes(claim)
    return claim


@transaction.atomic
def cast_community_vote(claim, user, verdict, reason=""):
    """One per user per claim; can be changed until the claim is final."""
    claim = Claim.objects.select_for_update().get(pk=claim.pk)
    if claim.status == Claim.Status.FINAL:
        raise ClaimClosed("This claim already has a final verdict.")
    vote, _ = CommunityVote.objects.update_or_create(claim=claim, user=user,
                                                     defaults={"verdict": verdict, "reason": reason})
    recompute(claim, trigger=vote)
    return vote


def community_summary(claim):
    counts = {}
    for v in claim.community_votes.values_list("verdict", flat=True):
        counts[v] = counts.get(v, 0) + 1
    total = sum(counts.values())
    if not total:
        return None
    top = max(counts, key=counts.get)
    labels = dict(Claim._meta.get_field("verdict").choices)
    return {"total": total, "top": labels.get(top, top), "top_pct": round(100 * counts[top] / total),
            "rows": sorted(((labels.get(k, k), n, round(100 * n / total)) for k, n in counts.items()),
                           key=lambda r: -r[1])}


def notify_threads(claim, text):
    """Post a system message into every chat thread about this claim."""
    from chat.models import ChatMessage
    for thread in claim.threads.all():
        ChatMessage.objects.create(thread=thread, sender=ChatMessage.Sender.SYSTEM, content=text)


@transaction.atomic
def admin_override(claim, admin, verdict, reason):
    if not (reason or "").strip():
        raise ValueError("A reason is required for an override.")
    claim = Claim.objects.select_for_update().get(pk=claim.pk)
    old = (claim.status, claim.verdict, claim.confidence)
    claim.status = Claim.Status.FINAL
    claim.verdict = verdict
    claim.confidence = 1.0
    claim.leading = verdict
    claim.locked_by_admin = True
    claim.finalized_at = claim.finalized_at or timezone.now()
    claim.save()
    _log(claim, old, f"Admin override: {reason.strip()}", changed_by=admin)
    notify_threads(claim, f"✅ Verified: {claim.get_verdict_display().upper()} (set by an admin: "
                          f"{reason.strip()[:120]}).")
    score_votes(claim)
    return claim


def score_votes(claim):
    """Bring every vote's scoring in line with the claim's final verdict (or unscore it)."""
    final = claim.verdict if claim.status == Claim.Status.FINAL else None
    for vote in claim.votes.select_related("verifier"):
        if vote.scored_as == final:
            continue
        dc, dt = engine.score_delta(vote.verdict, vote.scored_as, final)
        stat, _ = VerifierStat.objects.get_or_create(verifier=vote.verifier, domain=claim.domain,
                                                     language=claim.language)
        VerifierStat.objects.filter(pk=stat.pk).update(correct=F("correct") + dc, total=F("total") + dt)
        vote.scored_as = final
        vote.save(update_fields=["scored_as"])


def log_submission(claim, user=None):
    VerdictLog.objects.create(claim=claim, old_status="", new_status=claim.status,
                              reason="Claim submitted for verification", changed_by=user)


def log_status(claim, old_status, reason, changed_by=None):
    """Log a status change that is not a verdict (e.g. AI research finished, moderation)."""
    VerdictLog.objects.create(claim=claim, old_status=old_status, new_status=claim.status,
                              old_verdict=claim.verdict, new_verdict=claim.verdict,
                              old_confidence=claim.confidence, new_confidence=claim.confidence,
                              reason=reason[:300], changed_by=changed_by)


def _log(claim, old, reason, changed_by):
    VerdictLog.objects.create(
        claim=claim, old_status=old[0], new_status=claim.status, old_verdict=old[1],
        new_verdict=claim.verdict, old_confidence=old[2], new_confidence=claim.confidence,
        reason=reason[:300], changed_by=changed_by,
    )


def breakdown(claim):
    """Rows for the explainability table plus the arithmetic string."""
    rows = []
    for vote in claim.votes.select_related("verifier").prefetch_related("evidence"):
        stat = vote.verifier.verifier_stats.filter(domain=claim.domain, language=claim.language).first()
        rows.append({"vote": vote, "record": f"{stat.correct}/{stat.total}" if stat else "new"})
    arithmetic = engine.explain(claim.votes.values_list("verdict", "weight_at_vote"),
                                claim.community_votes.values_list("verdict", flat=True))
    return rows, arithmetic
