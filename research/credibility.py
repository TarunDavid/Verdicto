"""Source credibility tiers (PRD section 10.1). Simple, transparent domain lists."""
from urllib.parse import urlparse

TIERS = {
    "official": 1.0,
    "fact-checker": 1.0,
    "academic": 0.9,
    "news": 0.8,
    "reference": 0.6,
    "web": 0.5,
    "social": 0.3,
}
OFFICIAL = (".gov.in", ".nic.in", ".gov", "who.int", "vtu.ac.in", "pib.gov.in", "un.org", "cdc.gov",
            "icmr.gov.in", "rbi.org.in", "eci.gov.in")
FACT_CHECKERS = ("factly.in", "altnews.in", "boomlive.in", "newschecker.in", "factcheck.org",
                 "snopes.com", "fullfact.org", "politifact.com", "vishvasnews.com", "thequint.com",
                 "indiatoday.in/fact-check", "factcheck.afp.com", "reuters.com/fact-check")
ACADEMIC = ("doi.org", "openalex.org", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "nature.com",
            "sciencedirect.com", "thelancet.com", "nejm.org", "bmj.com", "springer.com", "arxiv.org",
            "semanticscholar.org", ".ac.in", ".edu")
NEWS = ("thehindu.com", "deccanherald.com", "indianexpress.com", "hindustantimes.com",
        "timesofindia.indiatimes.com", "ndtv.com", "prajavani.net", "vijaykarnataka.com",
        "udayavani.com", "bbc.com", "bbc.co.uk", "reuters.com", "apnews.com", "livemint.com",
        "thenewsminute.com", "newindianexpress.com", "deccanchronicle.com", "starofmysore.com", "scroll.in", "theprint.in", "news18.com", "indiatoday.in",
        "kannadaprabha.com", "publictv.in", "economictimes.indiatimes.com")
REFERENCE = ("wikipedia.org",)
SOCIAL = ("reddit.com", "x.com", "twitter.com", "facebook.com", "instagram.com", "youtube.com",
          "t.me", "sharechat.com")


def _matches(host, path, patterns):
    full = host + path
    for p in patterns:
        if "/" in p:
            if full.startswith(p) or full.startswith("www." + p):
                return True
        elif p.startswith("."):
            if host.endswith(p):
                return True
        elif host == p or host.endswith("." + p):
            return True
    return False


def classify(url):
    """Return (tier, weight, publisher) for a URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    for tier, patterns in (("fact-checker", FACT_CHECKERS), ("official", OFFICIAL),
                           ("academic", ACADEMIC), ("news", NEWS), ("reference", REFERENCE),
                           ("social", SOCIAL)):
        if _matches(host, path, patterns):
            return tier, TIERS[tier], host.removeprefix("www.")
    return "web", TIERS["web"], host.removeprefix("www.")
