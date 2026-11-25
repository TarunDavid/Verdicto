"""AI agent verifiers.

Each agent is a verifier account (VerifierProfile.is_ai_agent) with its own set of sources. For every
open claim it searches the web and research platforms, reads the full pages it can, judges each source
with Groq (quotes must appear verbatim in the source text), turns the evidence into a verdict with the
same deterministic truth-score formula as the main research, and votes like a verifier: its note is
its summary and its evidence is every source it used. The full research is kept (ResearchRun.agent,
linked to the vote) so anyone can read all of it on the claim page.

Agents are scored on their past verdicts exactly like people, so their weight goes up or down with
their accuracy. When the evidence is too thin they abstain instead of guessing. By default a claim
still needs at least one human verifier vote to become final (settings.FINAL_MIN_HUMAN_VOTES).
"""
import logging

from django.conf import settings
from django.utils import timezone

from accounts.models import User, VerifierProfile
from claims.models import Claim
from verification.models import Assignment
from verification.services import AlreadyVoted, cast_vote

from . import ai, scoring
from .credibility import TIERS, classify
from .models import ResearchRun, Source

logger = logging.getLogger("verdicto.agents")

ALL_DOMAINS = ["health", "education", "government", "local-news", "technology", "science", "politics",
               "finance", "other"]

AGENTS = [
    {"username": "ai_news_desk", "first_name": "AI Agent", "last_name": "News Desk",
     "profession": "AI agent: news and official announcements",
     "connectors": ["groq_web", "google_news", "official_news"],
     "query": "{q}",
     "focus": "Live web search, Google News and government-site news; reads the full articles."},
    {"username": "ai_research_desk", "first_name": "AI Agent", "last_name": "Research Desk",
     "profession": "AI agent: research papers and reference works",
     "connectors": ["openalex", "pubmed", "semantic_scholar", "crossref", "wikipedia", "groq_web"],
     "query": "{q} research study evidence",
     "focus": "OpenAlex, PubMed, Semantic Scholar, Crossref, Wikipedia and research-focused web search."},
    {"username": "ai_factcheck_desk", "first_name": "AI Agent", "last_name": "Fact-check Desk",
     "profession": "AI agent: fact-checkers and official sources",
     "connectors": ["factcheck", "groq_web", "official_news", "google_news"],
     "query": "fact check {q}",
     "focus": "Google Fact Check, fact-checking sites, official government sources."},
]
LABEL_TO_VERDICT = {"LIKELY_TRUE": "TRUE", "LIKELY_FALSE": "FALSE", "MIXED": "PARTIALLY_TRUE"}
MAX_EVIDENCE = 12


def ensure_agents():
    """Create (or update) the agent accounts. They can't log in (no phone, unusable password)."""
    agents = []
    for spec in AGENTS:
        user, created = User.objects.get_or_create(username=spec["username"], defaults={
            "first_name": spec["first_name"], "last_name": spec["last_name"], "role": User.Role.VERIFIER,
            "domains": ALL_DOMAINS})
        if created:
            user.set_unusable_password()
            user.save()
        profile = user._profile() or VerifierProfile(user=user)
        profile.is_ai_agent = True
        profile.status = VerifierProfile.Status.APPROVED
        profile.languages = ["en", "kn"]
        profile.profession = spec["profession"]
        profile.organisation = "Verdicto AI"
        profile.approved_at = profile.approved_at or timezone.now()
        profile.save()
        agents.append((user, spec))
    return agents


def run_agents(claim, base_run=None):
    """Let every agent research and vote on the claim, one after another (keeps within Groq's
    per-minute limits). Never raises: a failing agent just doesn't vote."""
    if not ai.available():
        return []
    base_run = base_run or claim.research_runs.filter(status=ResearchRun.Status.DONE).first()
    votes = []
    models = settings.GROQ_AGENT_MODELS or [settings.GROQ_MODEL]
    for i, (agent, spec) in enumerate(ensure_agents()):
        claim.refresh_from_db()
        if claim.status not in (Claim.Status.PENDING, Claim.Status.UNDER_REVIEW) or claim.locked_by_admin:
            break
        done = ResearchRun.all_runs.filter(claim=claim, agent=agent, status=ResearchRun.Status.DONE)
        if claim.votes.filter(verifier=agent).exists() or done.exists():
            continue
        ResearchRun.all_runs.filter(claim=claim, agent=agent).delete()   # an earlier failed attempt
        try:
            # Its own model first, then the others (Groq limits tokens per model, per minute and day).
            order = models[i % len(models):] + models[:i % len(models)]
            vote = run_agent(agent, spec, claim, base_run, order)
            if vote:
                votes.append(vote)
        except Exception:
            logger.exception("Agent %s failed on claim %s", agent.username, claim.pk)
    return votes


def run_agent(agent, spec, claim, base_run, models):
    from .pipeline import _enrich, _search_all

    info = ai.fallback_understanding(claim.text)
    if base_run is not None and base_run.queries:
        info.update(queries=base_run.queries, checkable_claim=base_run.checkable_claim or claim.text,
                    english_query=(base_run.queries[0] if claim.language == "en" else
                                   claim.canonical_en or base_run.queries[0]))
    assignment, _ = Assignment.objects.get_or_create(
        claim=claim, verifier=agent,
        defaults={"score": 0, "reason": f"AI agent · {spec['focus']}"})
    run = ResearchRun.all_runs.create(claim=claim, agent=agent, status=ResearchRun.Status.RUNNING,
                                      step="Searching", started_at=timezone.now(),
                                      checkable_claim=info["checkable_claim"], queries=info["queries"])

    # 1. Search this agent's own sources, then read the full pages it can.
    raw = _search_all(run, info, claim, enabled=spec["connectors"], query_template=spec["query"], limit=12)
    _enrich(raw, limit=6, from_connectors=("groq_web", "wikipedia", "official_news"), min_snippet=600)

    # 2. Judge each source (verbatim quotes only), 3. score with the same formula as the main research.
    items = []
    for n, r in enumerate(raw, 1):
        tier, weight, publisher = classify(r.get("publisher_url") or r["url"])
        if r.get("force_tier"):
            tier, weight = r["force_tier"], TIERS[r["force_tier"]]
        items.append({**r, "id": n, "tier": tier, "tier_weight": weight, "publisher": publisher})
    result, used = {}, None
    for model in ([models] if isinstance(models, str) else models):
        result, used = ai.assess(info["checkable_claim"], items, claim.language, model=model)
        if used or not items:
            break
    if items and not used:
        # Every model failed (usually a Groq rate limit): not a judgement, so don't abstain. The
        # agent tries again next time (`manage.py run_agents`).
        run.status, run.step, run.finished_at = ResearchRun.Status.FAILED, "AI unavailable", timezone.now()
        run.errors.append("AI assessment failed on every model (rate limit?)")
        run.save()
        assignment.delete()
        return None
    run.models_used.append(used) if used else None
    sources = []
    for it in items:
        stance, quote = result["stances"].get(it["id"], ("neutral", ""))
        sources.append(Source(run=run, url=it["url"][:800], title=it["title"][:300],
                              publisher=it["publisher"][:120], connector=it["connector"], tier=it["tier"],
                              tier_weight=it["tier_weight"], published=it.get("published"),
                              snippet=it["snippet"][:2000], stance=stance, quote=quote))
    Source.objects.bulk_create(sources)
    run.truth_score, run.label, run.confidence = scoring.truth_score(
        (s.stance, s.tier_weight, s.published) for s in sources)
    run.summary = result["summary"]
    run.facts, run.assumptions, run.conflicts = result["facts"], result["assumptions"], result["conflicts"]
    run.status, run.finished_at = ResearchRun.Status.DONE, timezone.now()

    # 4. Vote, or abstain when the evidence is too thin to call (the same bar as the main research:
    #    at least one official/fact-check source or two news sources taking a side).
    verdict = LABEL_TO_VERDICT.get(run.label)
    if not verdict or not run.summary:
        run.step = "Abstained: not enough evidence"
        run.save()
        assignment.status = Assignment.Status.DONE
        assignment.reason = (f"AI agent · abstained: found {len(sources)} sources, not enough evidence "
                             "to call it")[:200]
        assignment.save(update_fields=["status", "reason", "updated_at"])
        return None
    ranked = sorted(sources, key=lambda s: (s.stance not in ("supports", "refutes"), -s.tier_weight))
    evidence = []
    for s in ranked:
        if len(s.url) <= 500 and s.url not in evidence:
            evidence.append(s.url)
    score = f"truth score {run.truth_score:.0f}/100, " if run.truth_score is not None else ""
    suffix = f" ({score}{len(sources)} sources, {run.confidence} confidence)"
    summary = run.summary if len(run.summary) + len(suffix) <= 500 else run.summary[:497 - len(suffix)].rstrip() + "…"
    try:
        vote = cast_vote(claim, agent, verdict, note=summary + suffix, evidence_urls=evidence[:MAX_EVIDENCE])
    except AlreadyVoted:
        vote = None
    run.vote, run.step = vote, "Voted"
    run.save()
    return vote
