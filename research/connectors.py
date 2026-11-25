"""Live source connectors (PRD section 8). Official APIs and RSS only; no scraping behind logins.

Each connector: fn(query, language) -> list of {url, title, snippet, published, publisher_url?}.
Google Scholar (no API, scraping forbidden) and Web of Science (needs an institutional key) are
deliberately not included; OpenAlex and Crossref cover scholarly literature legally.
"""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone as dt_tz
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from django.conf import settings

from . import ai
from .fetch import get

TAG_RE = re.compile(r"<[^>]+>")


def _clean(s):
    return html.unescape(TAG_RE.sub("", s or "")).strip()


def _date(value):
    if not value:
        return None
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}", value):
            d = datetime.fromisoformat(value.replace("Z", "+00:00")[:25])
        elif re.match(r"^\d{8}T\d{6}Z$", value):
            d = datetime.strptime(value, "%Y%m%dT%H%M%SZ")
        else:
            d = parsedate_to_datetime(value)
        return d if d.tzinfo else d.replace(tzinfo=dt_tz.utc)
    except (ValueError, TypeError):
        return None


def groq_web(query, language):
    if not ai.available():
        return []
    return [{**r, "published": None} for r in ai.web_search(query)[:6]]


def google_news(query, language):
    hl, ceid = ("kn", "IN:kn") if language == "kn" else ("en-IN", "IN:en")
    resp = get(f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl=IN&ceid={ceid}",
               accept="application/rss+xml,application/xml")
    root = ET.fromstring(resp.content)
    out = []
    for item in root.iter("item"):
        src = item.find("source")
        out.append({
            "url": item.findtext("link", ""),
            "title": _clean(item.findtext("title", "")),
            "snippet": _clean(item.findtext("description", ""))[:600],
            "published": _date(item.findtext("pubDate")),
            "publisher_url": src.get("url") if src is not None else None,
        })
        if len(out) >= 6:
            break
    return out


def factcheck(query, language):
    if not settings.GOOGLE_FACTCHECK_API_KEY:
        return []
    resp = get("https://factchecktools.googleapis.com/v1alpha1/claims:search",
               params={"query": query, "key": settings.GOOGLE_FACTCHECK_API_KEY, "pageSize": 5},
               accept="application/json")
    out = []
    for c in resp.json().get("claims", []):
        for r in c.get("claimReview", [])[:1]:
            out.append({
                "url": r.get("url", ""),
                "title": r.get("title") or c.get("text", "")[:200],
                "snippet": f'Claim reviewed: "{c.get("text", "")[:300]}". Rating by '
                           f'{r.get("publisher", {}).get("name", "fact-checker")}: {r.get("textualRating", "")}.',
                "published": _date(r.get("reviewDate")),
                "force_tier": "fact-checker",
            })
    return out


def wikipedia(query, language):
    host = "kn.wikipedia.org" if language == "kn" else "en.wikipedia.org"
    resp = get(f"https://{host}/w/api.php", params={"action": "query", "list": "search",
               "srsearch": query, "format": "json", "srlimit": 3}, accept="application/json")
    return [{"url": f"https://{host}/wiki/{quote(r['title'].replace(' ', '_'))}",
             "title": r["title"], "snippet": _clean(r.get("snippet", "")), "published": None}
            for r in resp.json().get("query", {}).get("search", [])]


def openalex(query, language):
    resp = get("https://api.openalex.org/works", params={"search": query, "per-page": 3},
               accept="application/json")
    out = []
    for w in resp.json().get("results", []):
        words = {}
        for word, positions in (w.get("abstract_inverted_index") or {}).items():
            for p in positions:
                words[p] = word
        abstract = " ".join(words[i] for i in sorted(words))[:1200]
        out.append({"url": w.get("doi") or w.get("id"), "title": w.get("display_name") or "",
                    "snippet": abstract, "published": _date(w.get("publication_date")),
                    "force_tier": "academic"})
    return out


def crossref(query, language):
    resp = get("https://api.crossref.org/works", params={"query": query, "rows": 3,
               "select": "DOI,title,abstract,issued,URL"}, accept="application/json")
    out = []
    for item in resp.json().get("message", {}).get("items", []):
        parts = (item.get("issued", {}).get("date-parts") or [[None]])[0]
        published = None
        if parts and parts[0]:
            published = datetime(parts[0], (parts + [1, 1])[1] or 1, 1, tzinfo=dt_tz.utc)
        out.append({"url": item.get("URL", ""), "title": " ".join(item.get("title", []))[:300],
                    "snippet": _clean(item.get("abstract", ""))[:1200], "published": published,
                    "force_tier": "academic"})
    return out


def gdelt(query, language):
    resp = get("https://api.gdeltproject.org/api/v2/doc/doc",
               params={"query": query, "mode": "artlist", "format": "json", "maxrecords": 5,
                       "sort": "datedesc", "sourcecountry": "IN"}, accept="application/json")
    return [{"url": a["url"], "title": a.get("title", ""), "snippet": a.get("title", ""),
             "published": _date(a.get("seendate"))} for a in resp.json().get("articles", [])]


def pubmed(query, language):
    """PubMed (NCBI E-utilities, free): peer-reviewed medical literature."""
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    ids = get(base + "esearch.fcgi", params={"db": "pubmed", "term": query, "retmode": "json",
              "retmax": 3, "sort": "relevance"}, accept="application/json").json()
    idlist = ids.get("esearchresult", {}).get("idlist", [])
    if not idlist:
        return []
    summary = get(base + "esummary.fcgi", params={"db": "pubmed", "id": ",".join(idlist),
                  "retmode": "json"}, accept="application/json").json().get("result", {})
    out = []
    for pmid in idlist:
        item = summary.get(pmid, {})
        out.append({"url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "title": item.get("title", ""),
                    "snippet": f'{item.get("title", "")} {item.get("fulljournalname", "")}'.strip(),
                    "published": _date((item.get("sortpubdate") or "")[:10].replace("/", "-")),
                    "force_tier": "academic"})
    return out


def semantic_scholar(query, language):
    """Semantic Scholar paper search. Needs SEMANTIC_SCHOLAR_API_KEY (unauthenticated calls are
    rate-limited to near zero)."""
    if not settings.SEMANTIC_SCHOLAR_API_KEY:
        return []
    import httpx
    from .fetch import user_agent
    resp = httpx.get("https://api.semanticscholar.org/graph/v1/paper/search", timeout=8,
                     params={"query": query, "limit": 3, "fields": "title,url,abstract,publicationDate"},
                     headers={"x-api-key": settings.SEMANTIC_SCHOLAR_API_KEY, "User-Agent": user_agent()})
    resp.raise_for_status()
    return [{"url": p.get("url") or "", "title": p.get("title") or "", "snippet": (p.get("abstract") or "")[:1200],
             "published": _date(p.get("publicationDate")), "force_tier": "academic"}
            for p in resp.json().get("data", [])]


OFFICIAL_SITES = ("pib.gov.in", "karnataka.gov.in", "nic.in", "vtu.ac.in", "mohfw.gov.in", "who.int")


def official_news(query, language):
    """Official announcements via Google News RSS restricted to government domains. (PIB's own RSS
    blocks automated clients, so we don't hit it directly.)"""
    sites = " OR ".join(f"site:{d}" for d in OFFICIAL_SITES)
    return google_news(f"{query} ({sites})", language)


_reddit_token = {"value": None, "expires": 0}


def reddit(query, language):
    """Reddit official API (app-only OAuth). Needs REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET."""
    if not (settings.REDDIT_CLIENT_ID and settings.REDDIT_CLIENT_SECRET):
        return []
    import time

    import httpx
    from .fetch import user_agent
    if not _reddit_token["value"] or _reddit_token["expires"] < time.time():
        tok = httpx.post("https://www.reddit.com/api/v1/access_token", timeout=8,
                         data={"grant_type": "client_credentials"},
                         auth=(settings.REDDIT_CLIENT_ID, settings.REDDIT_CLIENT_SECRET),
                         headers={"User-Agent": user_agent()})
        tok.raise_for_status()
        _reddit_token.update(value=tok.json()["access_token"], expires=time.time() + 3000)
    resp = httpx.get("https://oauth.reddit.com/search", timeout=8,
                     params={"q": query, "limit": 4, "sort": "relevance", "t": "month"},
                     headers={"Authorization": f"Bearer {_reddit_token['value']}", "User-Agent": user_agent()})
    resp.raise_for_status()
    return [{"url": "https://www.reddit.com" + c["data"]["permalink"], "title": c["data"].get("title", ""),
             "snippet": (c["data"].get("selftext") or c["data"].get("title", ""))[:800],
             "published": datetime.fromtimestamp(c["data"].get("created_utc", 0), tz=dt_tz.utc),
             "force_tier": "social"} for c in resp.json().get("data", {}).get("children", [])]


CONNECTORS = {
    "groq_web": groq_web,
    "google_news": google_news,
    "factcheck": factcheck,
    "wikipedia": wikipedia,
    "openalex": openalex,
    "crossref": crossref,
    "gdelt": gdelt,
    "pubmed": pubmed,
    "semantic_scholar": semantic_scholar,
    "official_news": official_news,
    "reddit": reddit,
}
ACADEMIC = {"openalex", "crossref", "pubmed", "semantic_scholar"}
ACADEMIC_DOMAINS = {"health", "science", "technology"}
