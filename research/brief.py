"""The AI brief a human verifier sees next to their vote form: what the AI research found, how the AI
verifiers voted, and a suggested verdict with evidence links they can accept or change.

It only *suggests*. The verifier picks the verdict, writes the note and submits; nothing is voted
automatically on their behalf."""
from claims.models import Claim, Verdict

from .agents import AGENTS, LABEL_TO_VERDICT
from .models import ResearchRun

LABELS = dict(Verdict.choices)
MAX_LINKS = 3


def ai_brief(claim):
    run = claim.research_runs.filter(status=ResearchRun.Status.DONE).first()
    agent_votes = list(claim.votes.filter(verifier__verifier_profile__is_ai_agent=True)
                       .select_related("verifier").prefetch_related("evidence"))
    abstained = claim.assignments.filter(verifier__verifier_profile__is_ai_agent=True,
                                         reason__contains="abstained").count()

    # AI verifiers' consensus, weighted by each agent's track record (weight at vote time).
    weights = {}
    for v in agent_votes:
        weights[v.verdict] = weights.get(v.verdict, 0) + v.weight_at_vote
    total = sum(weights.values())
    tally = sorted(((code, str(LABELS.get(code, code)), sum(1 for v in agent_votes if v.verdict == code))
                    for code in weights), key=lambda r: -weights[r[0]])

    if weights:
        suggested = max(weights, key=weights.get)
        share = round(100 * weights[suggested] / total)
        basis = "agents"
    elif run and LABEL_TO_VERDICT.get(run.label):
        suggested, share, basis = LABEL_TO_VERDICT[run.label], None, "research"
    else:
        suggested, share, basis = None, None, None

    # Evidence: what the agreeing AI verifiers cited first, then the main research's strongest sources.
    links = []
    for v in agent_votes:
        if v.verdict == suggested:
            for e in v.evidence.all():
                if e.url not in links:
                    links.append(e.url)
    if run:
        for s in run.sources.filter(stance__in=["supports", "refutes"]).order_by("-tier_weight"):
            if s.url not in links and len(s.url) <= 500:
                links.append(s.url)
    links = links[:MAX_LINKS]

    draft = ""
    if suggested:
        parts = []
        if agent_votes:
            parts.append(f"AI verifiers: {sum(1 for v in agent_votes if v.verdict == suggested)} of "
                         f"{len(agent_votes) + abstained} said {LABELS[suggested]}")
        if run and run.label:
            score = f" ({run.truth_score:.0f}/100)" if run.truth_score is not None else ""
            parts.append(f"AI research: {run.get_label_display()}{score}")
        draft = "; ".join(parts) + ". I checked: "

    return {
        "run": run,
        "agent_votes": agent_votes,
        "answered": len(agent_votes) + abstained,
        "abstained": abstained,
        "expected": len(AGENTS),
        "tally": tally,
        "suggested": suggested,
        "suggested_label": str(LABELS.get(suggested, "")) if suggested else "",
        "share": share,
        "basis": basis,
        "links": links,
        "draft": draft,
        "is_open": claim.status in (Claim.Status.PENDING, Claim.Status.UNDER_REVIEW),
    }
