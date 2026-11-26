"""AI research pipeline (PRD section 7). Runs in a background thread; never sets a final verdict."""
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from claims.models import Claim
from claims.validators import validate_safe_url
from verification.assignment import assign_verifiers
from verification.services import log_status

from . import ai, connectors, scoring
from .credibility import TIERS, classify
from .fetch import fetch_article
from .models import ResearchRun, Source

logger = logging.getLogger("verdicto.research")
MAX_SOURCES = 10
CONNECTOR_TIMEOUT = 12


SOURCE_FILTERS = {
    "all": None,
    "news": {"groq_web", "google_news", "official_news"},
    "official": {"official_news", "groq_web", "google_news"},   # then keep only official-tier sources
    "factcheck": {"factcheck", "groq_web", "google_news"},
    "research": {"openalex", "crossref", "pubmed", "semantic_scholar", "wikipedia"},
}
FILTER_TIERS = {"official": {"official"}, "factcheck": {"fact-checker"}}


def start_research(claim, source_filter="all", deep=False):
    run = ResearchRun.objects.create(claim=claim, deep=deep,
                                     source_filter=source_filter if source_filter in SOURCE_FILTERS else "all")
    if settings.RESEARCH_SYNC:
        run_research(run.pk)
    elif settings.RESEARCH_MODE == "worker":
        pass  # stays QUEUED; `manage.py run_worker` picks it up
    else:
        transaction.on_commit(lambda: threading.Thread(target=_thread_main, args=(run.pk,),
                                                       daemon=True).start())
    return run


def _thread_main(run_id):
    close_old_connections()
    try:
        run_research(run_id)
    finally:
        close_old_connections()


def _step(run, text):
    run.step = text
    run.save(update_fields=["step", "status"])


def claim_next_queued():
    """Atomically take the oldest queued run (safe with several workers). Returns its id or None."""
    for run_id in ResearchRun.objects.filter(status=ResearchRun.Status.QUEUED).order_by(
            "created_at").values_list("pk", flat=True)[:5]:
        if ResearchRun.objects.filter(pk=run_id, status=ResearchRun.Status.QUEUED).update(
                status=ResearchRun.Status.RUNNING, step="Starting"):
            return run_id
    return None


def recover_stuck(minutes=10):
    """Runs left RUNNING by a crashed process: mark failed so the claim still reaches verifiers."""
    from datetime import timedelta
    cutoff = timezone.now() - timedelta(minutes=minutes)
    for run in ResearchRun.objects.filter(status=ResearchRun.Status.RUNNING, created_at__lt=cutoff):
        run.status, run.step = ResearchRun.Status.FAILED, "Research failed"
        run.errors.append("Interrupted (worker restarted)")
        run.finished_at = timezone.now()
        run.save()
        _finish(run, run.claim)


def run_research(run_id):
    run = ResearchRun.all_runs.select_related("claim").get(pk=run_id)
    claim = run.claim
    run.status, run.started_at = ResearchRun.Status.RUNNING, timezone.now()
    try:
        # 1. Understand
        _step(run, "Understanding the claim")
        info, model = ai.understand(claim.text, claim.language)
        if model:
            run.models_used.append(model)
        run.checkable_claim, run.queries = info["checkable_claim"], info["queries"]
        claim.urgency = info["urgency"]
        if claim.domain == "other" and info["domain"] != "other":
            claim.domain = info["domain"]
        if info.get("organisations"):
            claim.entities = info["organisations"]
        claim.save(update_fields=["urgency", "domain", "entities"])

        # Moderation: refuse to "fact-check" private people's personal lives.
        if info["targets_private_individual"]:
            _moderate(run, claim, info["moderation_reason"] or "Targets a private individual")
            return

        # 2-3. Search live sources in parallel
        _step(run, "Searching news, fact-checkers and research databases")
        raw = _search_all(run, info, claim)

        # 4. Read the top articles for fuller text (skip aggregator redirect links)
        _step(run, f"Reading {min(len(raw), 4)} top articles")
        _enrich(raw)

        # 5. Assess each source + summarise
        _step(run, "Weighing supporting and contradicting evidence")
        items = []
        for i, r in enumerate(raw, 1):
            tier, weight, publisher = classify(r.get("publisher_url") or r["url"])
            if r.get("force_tier"):
                tier, weight = r["force_tier"], TIERS[r["force_tier"]]
            items.append({**r, "id": i, "tier": tier, "tier_weight": weight, "publisher": publisher})
        result, model = ai.assess(run.checkable_claim or claim.text, items, claim.language)
        if model:
            run.models_used.append(model)

        # 6. Score deterministically and save
        sources = []
        for it in items:
            stance, quote = result["stances"].get(it["id"], ("neutral", ""))
            sources.append(Source(run=run, url=it["url"][:800], title=it["title"][:300],
                                  publisher=it["publisher"][:120], connector=it["connector"],
                                  tier=it["tier"], tier_weight=it["tier_weight"],
                                  published=it.get("published"), snippet=it["snippet"][:2000],
                                  stance=stance, quote=quote))
        Source.objects.bulk_create(sources)
        run.truth_score, run.label, run.confidence = scoring.truth_score(
            (s.stance, s.tier_weight, s.published) for s in sources)
        run.summary = result["summary"] or _fallback_summary(sources)
        run.facts, run.assumptions, run.conflicts = (result["facts"], result["assumptions"],
                                                     result["conflicts"])
        run.status = ResearchRun.Status.DONE
    except Exception as exc:  # never block the claim: verifiers still get it
        logger.exception("Research failed for claim %s", claim.pk)
        run.status = ResearchRun.Status.FAILED
        run.errors.append(f"{type(exc).__name__}: {exc}"[:300])
    run.step = "Finished" if run.status == ResearchRun.Status.DONE else "Research failed"
    run.finished_at = timezone.now()
    run.save()
    _finish(run, claim)


def _search_all(run, info, claim, enabled=None, templates=None, limit=None, language=None, cache=None,
                per_connector=4):
    """Query the connectors in parallel.

    AI agent verifiers (research/agents.py) pass their own `enabled` connectors, query `templates`
    per connector (e.g. {"google_news": "{q} site:altnews.in OR site:boomlive.in"}), a `language`
    and a shared `cache` so agents running on the same claim don't repeat the same request.
    """
    if enabled is None:
        enabled = [n for n in settings.RESEARCH_CONNECTORS if n in connectors.CONNECTORS]
        if claim.domain not in connectors.ACADEMIC_DOMAINS:
            enabled = [n for n in enabled if n not in connectors.ACADEMIC]
    enabled = [n for n in enabled if n in connectors.CONNECTORS]
    templates = templates or {}
    lang = language or claim.language
    queries = info["queries"][:3] or [claim.text[:120]]
    jobs = []
    for name in enabled:
        # Academic / encyclopedic / fact-check APIs work best with one English query.
        if name in ("google_news", "official_news"):
            qs = queries[:2]
        else:  # one call each: keeps within Groq's per-minute token limits
            qs = [info["english_query"] or queries[0]]
        for q in qs:
            jobs.append((name, templates.get(name, "{q}").format(q=q)))
    results, seen, seen_titles = [], set(), set()
    status = {}

    def keep(name, found):
        kept = 0
        for r in found:
            url = (r.get("url") or "").strip()
            try:
                validate_safe_url(url)
            except Exception:
                continue
            title_key = (r.get("title") or "").strip().lower()[:80]
            if url in seen or (title_key and title_key in seen_titles):
                continue
            seen.add(url)
            if title_key:
                seen_titles.add(title_key)
            results.append({**r, "url": url, "connector": name,
                            "title": r.get("title") or "", "snippet": r.get("snippet") or ""})
            kept += 1
        prev = status.get(name, "")
        count = kept + (int(prev.split("(")[1].rstrip(")")) if prev.startswith("ok") else 0)
        status[name] = f"ok ({count})"

    todo = []
    for name, q in jobs:
        if cache is not None and (name, q, lang) in cache:
            keep(name, cache[(name, q, lang)])
        else:
            todo.append((name, q))
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(connectors.CONNECTORS[n], q, lang): (n, q) for n, q in todo}
        try:
            for fut in as_completed(futures, timeout=CONNECTOR_TIMEOUT * 2):
                name, q = futures[fut]
                try:
                    found = fut.result()
                except Exception as exc:
                    status[name] = f"error: {type(exc).__name__}"
                    continue
                if cache is not None:
                    cache[(name, q, lang)] = [dict(r) for r in found]
                keep(name, found)
        except TimeoutError:
            for fut, (name, _q) in futures.items():
                if not fut.done():
                    status.setdefault(name, "error: timeout")
    run.connectors = status
    tiers = FILTER_TIERS.get(run.source_filter)
    if tiers:
        results = [r for r in results if (r.get("force_tier") or
                   classify(r.get("publisher_url") or r["url"])[0]) in tiers]
    # Prefer on-topic, credible, diverse sources: sources that mention the claim's key words first
    # (so an off-topic government PDF doesn't push out a relevant fact-check), then by credibility;
    # cap per connector.
    terms = _key_terms([claim.text, claim.canonical_en or "", info.get("checkable_claim") or ""],
                       claim.entities)

    def credibility(r):
        return TIERS.get(r.get("force_tier"), 0) or classify(r.get("publisher_url") or r["url"])[1]

    for r in results:
        r["_relevance"] = _relevance(r, terms)
    if sum(1 for r in results if r["_relevance"]) >= 3:     # enough on-topic results: drop the rest
        results = [r for r in results if r["_relevance"]]
    results.sort(key=lambda r: (-min(r["_relevance"], 6), -credibility(r)))
    capped, per = [], {}
    for r in results:
        if per.get(r["connector"], 0) < per_connector:
            capped.append(r)
            per[r["connector"]] = per.get(r["connector"], 0) + 1
    return capped[:limit or (MAX_SOURCES + (4 if run.deep else 0))]


def _key_terms(texts, entities=()):
    """Words from the claim itself, weighted: names and numbers ("WhatsApp", "VTU", "499") count 3,
    other content words 1. Search-query words like "fact check" are deliberately not used."""
    terms = {}
    for t in texts:
        for i, w in enumerate(re.findall(WORD, t or "")):
            low = w.lower()
            if low in ai.STOPWORDS or low in GENERIC_TERMS or (len(low) < 4 and not low.isdigit()) or len(low) < 3:
                continue
            name = (any(c.isdigit() for c in w) or any(c.isupper() for c in w[1:])   # 499, WhatsApp, VTU
                    or (i > 0 and w[0].isupper()))                                     # Mysuru mid-sentence
            terms[low] = max(terms.get(low, 0), 3 if name else 1)
    for e in entities or ():
        for w in re.findall(WORD, f"{e.get('name', '')} {e.get('short', '')}"):
            if len(w) >= 3 and w.lower() not in ai.STOPWORDS:
                terms[w.lower()] = 3
    return terms


WORD = re.compile(r"[\w\u0C80-\u0CFF']+")   # \w alone splits Kannada words at vowel signs
GENERIC_TERMS = {"fact", "check", "rumor", "rumour", "viral", "claim", "claims", "true", "false", "fake",
                 "people", "news", "report", "reports", "says", "said"}


def _relevance(r, terms):
    text = f"{r.get('title', '')} {r.get('snippet', '')[:800]}".lower()
    return sum(weight for term, weight in terms.items() if term in text)


def _enrich(raw, limit=4, from_connectors=("groq_web", "gdelt"), min_snippet=300, cache=None):
    """Read the full pages of the top results (safe fetcher: no private addresses, size/time caps).
    `cache` (url -> (title, text)) lets AI agents on the same claim share pages already read."""
    targets = [r for r in raw if "news.google.com" not in r["url"] and len(r["snippet"]) < min_snippet
               and r["connector"] in from_connectors][:limit]
    if not targets:
        return

    def work(r):
        try:
            if cache is not None and r["url"] in cache:
                title, text = cache[r["url"]]
            else:
                title, text = fetch_article(r["url"])
                if cache is not None:
                    cache[r["url"]] = (title, text)
            if text:
                r["snippet"] = text
            if title and not r["title"]:
                r["title"] = title
        except Exception:
            pass  # blocked/slow site: keep the snippet we have

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(work, targets))


def _fallback_summary(sources):
    if not sources:
        return "No sources were found automatically. Human verifiers will investigate."
    return (f"Found {len(sources)} possibly related sources. Automatic assessment is unavailable "
            "(AI not configured or unreachable), so stances were not judged. Verifiers will review them.")


def _moderate(run, claim, reason):
    claim.is_hidden = True
    claim.moderation_note = f"Auto-held: {reason}"[:200]
    old = claim.status
    claim.status = Claim.Status.REJECTED
    claim.save(update_fields=["is_hidden", "moderation_note", "status"])
    log_status(claim, old, f"Held for moderation: {reason}")
    run.status, run.step = ResearchRun.Status.DONE, "Held for moderation"
    run.label, run.summary = "", ("We don't fact-check claims about the private lives of private "
                                  "individuals. This submission has been held for an admin to review.")
    run.finished_at = timezone.now()
    run.save()


def _finish(run, claim):
    claim.refresh_from_db()
    if claim.status == Claim.Status.RESEARCHING:
        claim.status = Claim.Status.PENDING
        claim.save(update_fields=["status"])
        if run.status == ResearchRun.Status.DONE:
            score = f" ({run.truth_score:.0f}/100)" if run.truth_score is not None else ""
            reason = (f"AI provisional research finished: {run.get_label_display()}{score}. "
                      "Not a verdict; awaiting human verifiers.")
        else:
            reason = "AI research failed; sent to human verifiers without it."
        log_status(claim, Claim.Status.RESEARCHING, reason)
    if claim.status in (Claim.Status.PENDING, Claim.Status.UNDER_REVIEW):
        assign_verifiers(claim)
        if settings.AI_AGENTS_ENABLED and run.status == ResearchRun.Status.DONE:
            from .agents import run_agents
            run_agents(claim, run)
