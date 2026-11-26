"""AI agent verifiers.

Each agent is a verifier account (VerifierProfile.is_ai_agent) with its own set of sources. For every
open claim it searches the web and research platforms, reads the full pages it can, judges each source
with Groq (quotes must appear verbatim in the source text), turns the evidence into a verdict with the
same deterministic truth-score formula as the main research, and votes like a verifier: its note is
its summary and its evidence is every source it used. The full research is kept (ResearchRun.agent,
linked to the vote) so anyone can read all of it on the claim page.

There are 26 agents, each with its own name and specialised by field and by sources. As soon as a
claim's first AI research finishes, all of them give their own verdict (or the best-suited
settings.AI_AGENTS_PER_CLAIM, if set: specialists in its field plus generalist fact-checkers, ranked
by track record).

Agents are scored on their past verdicts exactly like people, so their weight goes up or down with
their accuracy. When the evidence is too thin they abstain instead of guessing. All AI agents
together count for at most settings.AI_AGENT_WEIGHT_CAP of the total weight, and by default a claim
still needs at least one human verifier vote to become final (settings.FINAL_MIN_HUMAN_VOTES).
"""
import logging
import threading
import time

from django.conf import settings
from django.db import connection
from django.utils import timezone

from accounts.models import User, VerifierProfile
from claims.models import Claim
from verification.models import Assignment
from verification.services import AlreadyVoted, cast_vote

from . import ai, scoring
from .credibility import TIERS, classify
from .models import ResearchRun, Source

logger = logging.getLogger("verdicto.agents")

ALL = ["health", "education", "government", "local-news", "technology", "science", "politics",
       "finance", "other"]


def _sites(*domains):
    return " OR ".join(f"site:{d}" for d in domains)


# Each agent's own name: Sanskrit/Kannada words for what it does (so they read as names, not as
# real people). The 🤖 badge next to the name still tells readers it is an AI verifier.
NAMES = {
    "ai_news_desk": "Satya",            # truth
    "ai_factcheck_desk": "Pramana",     # proof
    "ai_india_factcheckers": "Sakshi",  # witness
    "ai_global_factcheckers": "Nyaya",  # fairness, justice
    "ai_hoax_hunter": "Tarka",          # logic, reasoning
    "ai_latest_desk": "Nava",           # new
    "ai_global_news": "Vishwa",         # the world
    "ai_reference_desk": "Smriti",      # memory, record
    "ai_public_discussion": "Janavani",  # voice of the people
    "ai_kannada_media": "Kaveri",       # the river of Karnataka
    "ai_research_desk": "Medha",        # intellect
    "ai_health_desk": "Arogya",         # health
    "ai_medical_research": "Dhanvi",    # after Dhanvantari, physician of the gods
    "ai_education_desk": "Vidya",       # learning
    "ai_exams_desk": "Shiksha",         # education
    "ai_official_desk": "Niti",         # policy
    "ai_karnataka_gov": "Sahyadri",     # the Western Ghats
    "ai_karnataka_news": "Tunga",       # a Karnataka river
    "ai_schemes_desk": "Yojana",        # scheme, plan
    "ai_politics_desk": "Loka",         # the people, the public
    "ai_tech_desk": "Yantra",           # machine
    "ai_telecom_desk": "Tarang",        # wave
    "ai_scam_desk": "Raksha",           # protection
    "ai_business_desk": "Vyapara",      # trade
    "ai_finance_desk": "Artha",         # wealth, economics
    "ai_science_desk": "Vigyan",        # science
}


def _agent(username, desk, focus, domains, connectors, templates=None, language=None, languages=None):
    """desk: its job title. domains: the fields it specialises in (ALL = generalist). templates:
    query per connector ("{q}" is the claim's search query). language: search in this language.
    languages: only take claims in these languages."""
    return {"username": username, "first_name": NAMES[username], "last_name": "",
            "desk": desk, "profession": f"{desk} (AI verifier)",
            "domains": domains, "connectors": connectors, "templates": templates or {},
            "language": language, "languages": languages, "focus": focus.split(": ", 1)[-1]}


FACT_CHECKERS_IN = _sites("altnews.in", "boomlive.in", "factly.in", "newschecker.in", "vishvasnews.com",
                          "thequint.com", "indiatoday.in")
FACT_CHECKERS_WORLD = _sites("afp.com", "reuters.com", "snopes.com", "fullfact.org", "apnews.com",
                             "politifact.com")

# 26 agents. Most search Google News restricted to particular trusted sites (no Groq tokens); only a
# few use Groq web search. Each claim gets the best-suited AI_AGENTS_PER_CLAIM of them (see pick_agents).
AGENTS = [
    # --- Generalists (any field) ---
    _agent("ai_news_desk", "News Desk", "AI agent: live web search, Google News and government news",
           ALL, ["groq_web", "google_news", "official_news"]),
    _agent("ai_factcheck_desk", "Fact-check Desk", "AI agent: fact-checkers and official sources",
           ALL, ["factcheck", "groq_web", "official_news", "google_news"], {"groq_web": "fact check {q}"}),
    _agent("ai_india_factcheckers", "Indian Fact-checkers", "AI agent: Alt News, BOOM, Factly, Newschecker, "
           "Vishvas News", ALL, ["google_news", "factcheck"], {"google_news": "{q} " + FACT_CHECKERS_IN}),
    _agent("ai_global_factcheckers", "Global Fact-checkers", "AI agent: AFP, Reuters, AP, Snopes, Full Fact",
           ALL, ["google_news"], {"google_news": "{q} " + FACT_CHECKERS_WORLD}),
    _agent("ai_hoax_hunter", "Hoax Hunter", "AI agent: searches for debunks, hoax and fake-news reports",
           ALL, ["google_news", "groq_web"], {"google_news": "{q} fake OR hoax OR false OR misleading",
                                             "groq_web": "is it true that {q}"}),
    _agent("ai_latest_desk", "Latest Updates Desk", "AI agent: most recent reports and official updates",
           ALL, ["google_news", "official_news"], {"google_news": "{q} latest update"}),
    _agent("ai_global_news", "Global News Desk", "AI agent: BBC, Reuters, AP, The Hindu and GDELT",
           ALL, ["google_news", "gdelt"], {"google_news": "{q} " + _sites("bbc.com", "reuters.com", "apnews.com",
                                                                             "thehindu.com")}),
    _agent("ai_reference_desk", "Reference Desk", "AI agent: Wikipedia and scholarly reference works",
           ALL, ["wikipedia", "openalex"]),
    _agent("ai_public_discussion", "Public Discussion Desk", "AI agent: public discussion (Reddit) and viral-claim "
           "coverage", ALL, ["reddit", "google_news"], {"google_news": "{q} viral claim"}),
    _agent("ai_kannada_media", "Kannada Media Desk", "AI agent: Kannada-language news",
           ALL, ["google_news"], language="kn", languages=["kn"]),
    # --- Specialists ---
    _agent("ai_research_desk", "Research Desk", "AI agent: OpenAlex, PubMed, Semantic Scholar, Crossref, Wikipedia",
           ["science", "health", "technology"],
           ["openalex", "pubmed", "semantic_scholar", "crossref", "wikipedia", "groq_web"],
           {"groq_web": "{q} research study evidence"}),
    _agent("ai_health_desk", "Health Desk", "AI agent: WHO, Health Ministry, ICMR, CDC and PubMed",
           ["health"], ["pubmed", "google_news", "factcheck"],
           {"google_news": "{q} " + _sites("who.int", "mohfw.gov.in", "icmr.gov.in", "cdc.gov", "nhp.gov.in")}),
    _agent("ai_medical_research", "Medical Research Desk", "AI agent: PubMed, Semantic Scholar, OpenAlex",
           ["health", "science"], ["pubmed", "semantic_scholar", "openalex"]),
    _agent("ai_education_desk", "Education Desk", "AI agent: universities, UGC, AICTE and education departments",
           ["education"], ["google_news", "official_news"],
           {"google_news": "{q} " + _sites("ac.in", "edu.in", "ugc.gov.in", "aicte-india.org", "schooleducation.kar.nic.in")}),
    _agent("ai_exams_desk", "Exams & Universities Desk", "AI agent: official exam notices and university circulars",
           ["education"], ["groq_web", "google_news"],
           {"groq_web": "{q} official notice circular", "google_news": "{q} exam notice"}),
    _agent("ai_official_desk", "Official Sources Desk", "AI agent: PIB, MyGov and india.gov.in",
           ["government", "politics", "education", "health"], ["official_news", "google_news"],
           {"google_news": "{q} " + _sites("pib.gov.in", "mygov.in", "india.gov.in")}),
    _agent("ai_karnataka_gov", "Karnataka Government Desk", "AI agent: Karnataka government sites",
           ["government", "local-news", "education"], ["google_news", "official_news"],
           {"google_news": "{q} " + _sites("karnataka.gov.in", "kar.nic.in")}),
    _agent("ai_karnataka_news", "Karnataka News Desk", "AI agent: Karnataka news coverage",
           ["local-news", "government", "politics", "education"], ["google_news"],
           {"google_news": "{q} Karnataka"}),
    _agent("ai_schemes_desk", "Government Schemes Desk", "AI agent: welfare schemes, ration, pensions, jobs",
           ["government", "finance"], ["google_news", "official_news"],
           {"google_news": "{q} scheme " + _sites("gov.in", "pib.gov.in", "nic.in")}),
    _agent("ai_politics_desk", "Politics & Elections Desk", "AI agent: Election Commission and political coverage",
           ["politics", "government"], ["google_news", "factcheck"],
           {"google_news": "{q} " + _sites("eci.gov.in", "ceokarnataka.kar.nic.in", "thehindu.com",
                                           "indianexpress.com")}),
    _agent("ai_tech_desk", "Technology Desk", "AI agent: technology news and web search",
           ["technology"], ["google_news", "groq_web"],
           {"google_news": "{q} technology", "groq_web": "{q} tech news"}),
    _agent("ai_telecom_desk", "Telecom & 5G Desk", "AI agent: TRAI, Department of Telecom, WHO, research",
           ["technology", "science", "health"], ["google_news", "openalex"],
           {"google_news": "{q} " + _sites("trai.gov.in", "dot.gov.in", "who.int")}),
    _agent("ai_scam_desk", "Scams & Cyber-safety Desk", "AI agent: scam, phishing and fraud reports",
           ["technology", "finance", "government"], ["google_news"],
           {"google_news": "{q} scam OR fraud OR phishing OR fake"}),
    _agent("ai_business_desk", "Companies & Business Desk", "AI agent: Reuters, Mint, Economic Times, company "
           "announcements", ["finance", "technology"], ["google_news", "groq_web"],
           {"google_news": "{q} " + _sites("reuters.com", "livemint.com", "economictimes.indiatimes.com",
                                           "business-standard.com"),
            "groq_web": "{q} company official announcement press release"}),
    _agent("ai_finance_desk", "Finance & Markets Desk", "AI agent: RBI, SEBI and market news",
           ["finance"], ["google_news", "official_news"],
           {"google_news": "{q} " + _sites("rbi.org.in", "sebi.gov.in", "moneycontrol.com")}),
    _agent("ai_science_desk", "Science Desk", "AI agent: Nature, Science, ISRO, OpenAlex, Crossref",
           ["science", "technology"], ["openalex", "crossref", "wikipedia", "google_news"],
           {"google_news": "{q} " + _sites("nature.com", "science.org", "isro.gov.in", "newscientist.com")}),
]
LABEL_TO_VERDICT = {"LIKELY_TRUE": "TRUE", "LIKELY_FALSE": "FALSE", "MIXED": "PARTIALLY_TRUE"}
MAX_EVIDENCE = 12


def ensure_agents():
    """Create (or update) the agent accounts. They can't log in (no phone, unusable password)."""
    agents = []
    for spec in AGENTS:
        user, created = User.objects.get_or_create(username=spec["username"], defaults={
            "first_name": spec["first_name"], "last_name": spec["last_name"], "role": User.Role.VERIFIER})
        if created:
            user.set_unusable_password()
        user.first_name, user.last_name, user.domains = spec["first_name"], spec["last_name"], spec["domains"]
        user.save()
        profile = user._profile() or VerifierProfile(user=user)
        profile.is_ai_agent = True
        profile.status = VerifierProfile.Status.APPROVED
        profile.languages = spec["languages"] or ["en", "kn"]
        profile.profession = spec["profession"][:80]
        profile.organisation = "Verdicto AI"
        profile.approved_at = profile.approved_at or timezone.now()
        profile.save()
        agents.append((user, spec))
    return agents


def pick_agents(claim, agents, n):
    """The best-suited n agents for a claim, like assigning human verifiers: specialists in the
    claim's field first, then generalists, each ranked by track record (weight). About a third of
    the places are kept for generalists so fact-checkers always look too."""
    from verification import engine
    specialists, generalists = [], []
    for user, spec in agents:
        if spec["languages"] and claim.language not in spec["languages"]:
            continue
        stats = user.verifier_stats.values_list("domain", "language", "correct", "total")
        weight = engine.verifier_weight(stats, claim.domain, claim.language)
        if spec["languages"]:                       # a language specialist for this claim
            specialists.append((weight + 0.1, user, spec))
        elif spec["domains"] is ALL:
            generalists.append((weight, user, spec))
        elif claim.domain in spec["domains"]:
            specialists.append((weight + (0.05 if spec["domains"][0] == claim.domain else 0), user, spec))
    order = {s["username"]: i for i, s in enumerate(AGENTS)}
    rank = lambda items: [x[1:] for x in sorted(items, key=lambda x: (-x[0], order[x[2]["username"]]))]
    specialists, generalists = rank(specialists), rank(generalists)
    n_special = min(len(specialists), max(n - max(2, n // 3), 0))
    picked = specialists[:n_special] + generalists[:n - n_special]
    return picked + specialists[n_special:n_special + n - len(picked)]


def chosen_agents(claim, n=None):
    """All agents (n = 0, the default), or the best-suited n (settings.AI_AGENTS_PER_CLAIM)."""
    n = settings.AI_AGENTS_PER_CLAIM if n is None else n
    roster = ensure_agents()
    return roster if not n or n >= len(roster) else pick_agents(claim, roster, n)


def run_agents(claim, base_run=None, n=None, workers=None, retries=None):
    """Every chosen agent researches the claim and gives its own verdict. They work in parallel
    (`workers` at a time, one Groq model each), share searches and pages already read, and an agent
    that hits a Groq rate limit on every model is retried after AI_AGENT_RETRY_DELAY seconds.
    Never raises: a failing agent just doesn't vote. Returns the votes cast."""
    if not ai.available():
        return []
    base_run = base_run or claim.research_runs.filter(status=ResearchRun.Status.DONE).first()
    workers = settings.AI_AGENT_WORKERS if workers is None else workers
    retries = settings.AI_AGENT_RETRIES if retries is None else retries
    models = settings.GROQ_AGENT_MODELS or [settings.GROQ_MODEL]
    shared = {"search": {}, "pages": {}}
    todo = list(enumerate(chosen_agents(claim, n)))
    votes = []
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(settings.AI_AGENT_RETRY_DELAY)   # Groq's per-minute token budget refills
        failed = []
        lanes = [[item for item in todo if item[0] % workers == w] for w in range(workers)]

        def lane(items, threaded):
            for i, (agent, spec) in items:
                outcome, vote = _one_agent(claim, agent, spec, base_run,
                                           models[i % len(models):] + models[:i % len(models)], shared)
                if vote:
                    votes.append(vote)
                elif outcome == "failed":
                    failed.append((i, (agent, spec)))
                elif outcome == "closed":
                    break
            if threaded:
                connection.close()

        if workers <= 1:
            lane(todo, False)
        else:
            threads = [threading.Thread(target=lane, args=(items, True)) for items in lanes if items]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        todo = sorted(failed, key=lambda x: x[0])
        if not todo:
            break
    return votes


# Agents search and call the AI in parallel, but write to the database one at a time (short writes;
# SQLite allows a single writer).
DB_LOCK = threading.Lock()


def _one_agent(claim, agent, spec, base_run, models, shared):
    """('voted' | 'abstained' | 'failed' | 'skipped' | 'closed', vote or None)"""
    with DB_LOCK:
        claim = Claim.objects.get(pk=claim.pk)
        if claim.status not in (Claim.Status.PENDING, Claim.Status.UNDER_REVIEW) or claim.locked_by_admin:
            return "closed", None
        done = ResearchRun.all_runs.filter(claim=claim, agent=agent, status=ResearchRun.Status.DONE)
        if claim.votes.filter(verifier=agent).exists() or done.exists():
            return "skipped", None
        ResearchRun.all_runs.filter(claim=claim, agent=agent).delete()   # an earlier failed attempt
    try:
        return run_agent(agent, spec, claim, base_run, models, shared)
    except Exception:
        logger.exception("Agent %s failed on claim %s", agent.username, claim.pk)
        return "failed", None


def run_agent(agent, spec, claim, base_run, models, shared=None):
    from .pipeline import _enrich, _search_all

    info = ai.fallback_understanding(claim.text)
    if base_run is not None and base_run.queries:
        info.update(queries=base_run.queries, checkable_claim=base_run.checkable_claim or claim.text,
                    english_query=(base_run.queries[0] if claim.language == "en" else
                                   claim.canonical_en or base_run.queries[0]))
    with DB_LOCK:
        assignment, _ = Assignment.objects.get_or_create(
            claim=claim, verifier=agent,
            defaults={"score": 0, "reason": f"AI verifier · {spec['focus']}"[:200]})
        run = ResearchRun.all_runs.create(claim=claim, agent=agent, status=ResearchRun.Status.RUNNING,
                                          step="Searching", started_at=timezone.now(),
                                          checkable_claim=info["checkable_claim"], queries=info["queries"])

    # 1. Search this agent's own sources, then read the full pages it can.
    shared = shared if shared is not None else {"search": {}, "pages": {}}
    raw = _search_all(run, info, claim, enabled=spec["connectors"], templates=spec["templates"], limit=12,
                      language=spec["language"], cache=shared["search"], per_connector=8)
    _enrich(raw, limit=6, from_connectors=("groq_web", "wikipedia", "official_news", "google_news", "gdelt"),
            min_snippet=600, cache=shared["pages"])

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
    with DB_LOCK:
        return _record(agent, claim, run, assignment, items, result, used)


def _record(agent, claim, run, assignment, items, result, used):
    """Save the agent's research and cast (or withhold) its vote. Called under DB_LOCK."""
    if items and not used:
        # Every model failed (usually a Groq rate limit): not a judgement, so don't abstain. The
        # agent tries again next time (`manage.py run_agents`).
        run.status, run.step, run.finished_at = ResearchRun.Status.FAILED, "AI unavailable", timezone.now()
        run.errors.append("AI assessment failed on every model (rate limit?)")
        run.save()
        assignment.delete()
        return "failed", None
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
        assignment.reason = (f"AI verifier · abstained: found {len(sources)} sources, not enough evidence "
                             "to call it")[:200]
        assignment.save(update_fields=["status", "reason", "updated_at"])
        return "abstained", None
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
    return ("voted", vote) if vote else ("skipped", None)
