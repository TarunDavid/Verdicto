"""Duplicate detection with rapidfuzz (SPEC section 7)."""
import re
import unicodedata

from rapidfuzz import fuzz

from .models import Claim

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
BOILERPLATE_RE = re.compile(r"forwarded many times|forwarded", re.I)
THRESHOLD = 80


def normalize(text):
    text = (text or "").lower()
    text = URL_RE.sub(" ", text)
    text = BOILERPLATE_RE.sub(" ", text)
    # Keep letters, digits and combining marks (Kannada vowel signs are category M). Drop
    # punctuation, symbols and emoji.
    kept = [ch if unicodedata.category(ch)[0] in "LNM" else " " for ch in text]
    return " ".join("".join(kept).split())


def find_similar(text, source_url="", limit=5, exclude_id=None):
    norm = normalize(text)
    matches = []
    qs = Claim.objects.filter(duplicate_of__isnull=True).order_by("-created_at")[:300]
    for claim in qs:
        if claim.pk == exclude_id:
            continue
        if source_url and claim.source_url and claim.source_url == source_url:
            matches.append((100, claim))
            continue
        if len(norm) < 8:
            continue
        score = fuzz.token_set_ratio(norm, claim.normalized_text)
        if score >= THRESHOLD:
            matches.append((score, claim))
    matches.sort(key=lambda m: -m[0])
    return matches[:limit]
