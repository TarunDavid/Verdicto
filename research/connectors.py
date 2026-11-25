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


CONNECTORS = {
    "groq_web": groq_web,
    "google_news": google_news,
    "factcheck": factcheck,
    "wikipedia": wikipedia,
    "openalex": openalex,
    "crossref": crossref,
    "gdelt": gdelt,
}
ACADEMIC = {"openalex", "crossref"}
ACADEMIC_DOMAINS = {"health", "science", "technology"}
