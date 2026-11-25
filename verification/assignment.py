"""Intelligent verifier assignment (PRD section 9). Deterministic and explainable.

match = domain match x language match x reliability x availability (x 1.1 locality bonus)
        (x 3 when the verifier works at an organisation the claim is about)

Verifiers who work at the organisation a claim is about (a VTU board member for a VTU claim, an
Nvidia engineer for an Nvidia claim) are assigned first, whatever their field. After that, the
higher a verifier's track record (reliability = their weight), the higher they rank.
AI agent verifiers are not assigned here; they research every open claim (research/agents.py).
"""
import re
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


ORG_BOOST = 3.0
# Words that are not an organisation on their own ("Freelance", "Government", "College"...).
GENERIC_ORGS = {"government", "govt", "freelance", "freelancer", "self", "self employed", "none", "na",
                "student", "university", "college", "school", "hospital", "company", "independent",
                "private", "ltd", "inc", "india", "karnataka", "bengaluru", "bangalore", "the", "and"}
ORG_SUFFIXES = re.compile(r"\b(corporation|corp|incorporated|inc|limited|ltd|pvt|private|llp|llc|"
                          r"company|co|technologies|technology|india|group)\b\.?", re.I)
ACRONYM_SKIP = {"of", "the", "and", "for", "in", "at", "&"}
EMAIL_HOSTS = {"mail", "email", "www", "in", "corp", "my"}
# Too common to identify an organisation as a single word ("VTU Board" -> "vtu", not "board").
COMMON_WORDS = GENERIC_ORGS | ACRONYM_SKIP | {
    "board", "department", "dept", "office", "institute", "institution", "ministry", "council",
    "centre", "center", "national", "state", "central", "indian", "science", "sciences", "research",
    "technical", "technological", "engineering", "medical", "education", "examination", "examinations",
    "exam", "exams", "news", "public", "health", "services", "service", "team", "member", "general",
    "city", "district", "corporation", "bank", "authority", "commission", "agency", "foundation",
    "trust", "society", "association", "labs", "lab", "systems", "solutions", "global", "international",
    "academy", "faculty", "staff", "senior", "junior", "developer", "engineer", "professor", "director",
    "secretary", "committee", "cell", "unit", "division", "branch", "head", "chief", "new", "first",
    "times", "daily", "media", "network", "digital", "software", "hardware", "power", "energy",
    "water", "police", "court", "high", "supreme", "municipal", "nagar", "sabha", "university",
    "technologies", "technology", "limited", "private", "group", "company", "taluk", "zilla",
    "panchayat", "gram", "village", "town", "rural", "urban"} | {
    # Places: a place name alone doesn't make someone an insider (locality has its own bonus).
    "karnataka", "bengaluru", "bangalore", "mysuru", "mysore", "mangaluru", "mangalore", "udupi",
    "hassan", "shivamogga", "shimoga", "tumakuru", "tumkur", "belagavi", "belgaum", "kalaburagi",
    "gulbarga", "ballari", "bellary", "vijayapura", "bijapur", "hubballi", "hubli", "dharwad",
    "davanagere", "chitradurga", "chikkamagaluru", "kodagu", "coorg", "mandya", "raichur", "bidar",
    "koppal", "gadag", "haveri", "uttara", "dakshina", "kannada", "chamarajanagar", "kolar",
    "chikkaballapur", "ramanagara", "yadgir", "bagalkot", "delhi", "mumbai", "chennai", "hyderabad",
    "kerala", "india", "bharat"}


def _norm(text):
    return re.sub(r"[^a-z0-9ಀ-೿ ]+", " ", (text or "").lower()).split()


def org_keys(name, short="", domain=""):
    """Ways an organisation is written: 'Visvesvaraya Technological University' -> {'visvesvaraya
    technological university', 'vtu'}; 'NVIDIA Corporation' -> {'nvidia corporation', 'nvidia'};
    the work-email domain 'nvidia.com' -> {'nvidia'}."""
    keys = set()
    words = _norm(name)
    if words:
        keys.add(" ".join(words))
        core = _norm(ORG_SUFFIXES.sub(" ", name))
        if core:
            keys.add(" ".join(core))
        if len(words) >= 2:
            acronym = "".join(w[0] for w in words if w not in ACRONYM_SKIP)
            if len(acronym) >= 3:
                keys.add(acronym)
        # A brand usually comes first: "Nvidia GPU team" -> "nvidia", "Infosys Mysuru campus".
        if words[0] not in COMMON_WORDS and len(words[0]) >= 3 and not words[0].isdigit():
            keys.add(words[0])
        # Abbreviations written inside the name: "VTU Board" -> "vtu", "Board of Examinations, VTU".
        keys |= {w.lower() for w in re.findall(r"\b[A-Z][A-Z0-9]{2,9}\b", name or "")
                 if w.lower() not in COMMON_WORDS}
    if short:
        keys.add(" ".join(_norm(short)))
    if domain:
        labels = [x for x in domain.lower().split(".") if x and x not in EMAIL_HOSTS]
        if labels:
            keys.add(labels[0])
    return {k for k in keys if len(k) >= 3 and k not in GENERIC_ORGS}


def claim_org_keys(claim):
    keys = set()
    for e in claim.entities or []:
        keys |= org_keys(e.get("name", ""), e.get("short", ""))
    return keys


def org_match(profile, claim, claim_keys=None):
    """The organisation (as written by the verifier) if the claim is about it, else ''."""
    if profile is None or not (profile.organisation or profile.org_domain):
        return ""
    mine = org_keys(profile.organisation, domain=profile.org_domain if profile.work_email_verified else "")
    if not mine:
        return ""
    keys = claim_org_keys(claim) if claim_keys is None else claim_keys
    if mine & keys:
        return profile.organisation or profile.org_domain
    haystack = " " + " ".join(_norm(f"{claim.text} {claim.canonical_en}")) + " "
    if any(f" {k} " in haystack for k in mine):
        return profile.organisation or profile.org_domain
    return ""


def rank_verifiers(claim, exclude_ids=()):
    verifiers = (User.objects.filter(role=User.Role.VERIFIER, verifier_profile__status="approved",
                                     verifier_profile__is_ai_agent=False, is_active=True)
                 .select_related("verifier_profile")
                 .exclude(pk__in=exclude_ids)
                 .annotate(open_tasks=Count("assignments",
                                            filter=Q(assignments__status=Assignment.Status.ASSIGNED)))
                 .prefetch_related("verifier_stats"))
    ranked = []
    keys = claim_org_keys(claim)
    for v in verifiers:
        domains = set(v.domains or [])
        org = org_match(v.verifier_profile, claim, keys)
        if org:
            domain_match, why = 1.0, f"works at {org} (the claim is about it)"
        elif claim.domain in domains:
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
        score = domain_match * lang_match * reliability * availability * locality * (ORG_BOOST if org else 1)
        acc = v.verdict_accuracy
        record = f"{acc}% accurate in {v.verdicts_scored}" if acc is not None else "new"
        reason = (f"{why} · {'speaks ' + claim.get_language_display() if lang_match == 1 else 'other language'}"
                  f" · {record} · weight {reliability:.2f} · {v.open_tasks} open"
                  f"{' · same district' if locality > 1 else ''}")
        ranked.append((v, round(score, 4), reason, bool(org)))
    # Insiders first, then by score (track record x fit x availability).
    ranked.sort(key=lambda r: (not r[3], -r[1]))
    return [r[:3] for r in ranked]


def assign_verifiers(claim, n=None):
    """Assign the top-N eligible verifiers who are not already assigned and have not voted."""
    n = n or settings.ASSIGN_TOP_N
    active = claim.assignments.filter(status__in=[Assignment.Status.ASSIGNED, Assignment.Status.DONE],
                                      verifier__verifier_profile__is_ai_agent=False).count()
    needed = max(0, n - active)
    if not needed:
        return []
    exclude = set(claim.assignments.values_list("verifier_id", flat=True))
    exclude |= set(claim.votes.values_list("verifier_id", flat=True))
    created = []
    for v, score, reason in rank_verifiers(claim, exclude)[:needed]:
        created.append(Assignment.objects.create(claim=claim, verifier=v, score=score, reason=reason))
    return created


def assign_open_claims_to(verifier):
    """When a verifier is approved, hand them the open claims about their own organisation
    (even if those claims already have their usual number of verifiers)."""
    from claims.models import Claim
    profile = verifier._profile()
    if profile is None or profile.is_ai_agent or not (profile.organisation or profile.org_domain):
        return []
    created = []
    open_claims = (Claim.objects.filter(status__in=["PENDING", "UNDER_REVIEW"], duplicate_of__isnull=True)
                   .exclude(assignments__verifier=verifier).exclude(votes__verifier=verifier))
    for claim in open_claims:
        org = org_match(profile, claim)
        if org:
            reliability = engine.verifier_weight(
                verifier.verifier_stats.values_list("domain", "language", "correct", "total"),
                claim.domain, claim.language, profile.work_email_verified)
            created.append(Assignment.objects.create(
                claim=claim, verifier=verifier, score=round(reliability * ORG_BOOST, 4),
                reason=f"works at {org} (the claim is about it) · weight {reliability:.2f}"))
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
