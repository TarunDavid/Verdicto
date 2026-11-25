"""Duplicate detection with rapidfuzz (SPEC section 7)."""
import re
import unicodedata

from rapidfuzz import fuzz

from .models import Claim

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
BOILERPLATE_RE = re.compile(r"forwarded many times|forwarded", re.I)
THRESHOLD = 80
CANONICAL_THRESHOLD = 88   # stricter: AI restatements share a lot of generic wording


def normalize(text):
    text = (text or "").lower()
    text = URL_RE.sub(" ", text)
    text = BOILERPLATE_RE.sub(" ", text)
    # Keep letters, digits and combining marks (Kannada vowel signs are category M). Drop
    # punctuation, symbols and emoji.
    kept = [ch if unicodedata.category(ch)[0] in "LNM" else " " for ch in text]
    return " ".join("".join(kept).split())


def find_similar(text, source_url="", limit=5, exclude_id=None, canonical=""):
    """Fuzzy match on the original text, plus on the AI's English restatement when available, so a
    Kannada forward finds its English twin (and vice versa)."""
    norm = normalize(text)
    canon = normalize(canonical)
    matches = []
    qs = Claim.objects.filter(duplicate_of__isnull=True).order_by("-created_at")[:300]
    for claim in qs:
        if claim.pk == exclude_id:
            continue
        if source_url and claim.source_url and claim.source_url == source_url:
            matches.append((100, claim))
            continue
        score = fuzz.token_set_ratio(norm, claim.normalized_text) if len(norm) >= 8 else 0
        if score < THRESHOLD:
            score = 0
        if canon and claim.canonical_en:
            c = fuzz.token_set_ratio(canon, normalize(claim.canonical_en))
            if c >= CANONICAL_THRESHOLD:
                score = max(score, c)
        if score:
            matches.append((score, claim))
    matches.sort(key=lambda m: -m[0])
    return matches[:limit]


LOOSE = 45  # candidates worth asking the AI about


def find_same_claim(text, canonical, source_url=""):
    """Best duplicate for a new submission: a strong text match, else an AI-confirmed match among the
    closest candidates (catches Kannada <-> English and reworded forwards). Returns (score, claim) | None."""
    from research import ai

    direct = [m for m in find_similar(text, source_url, canonical=canonical) if m[0] >= 90]
    if direct:
        return direct[0]
    if not canonical:
        return None
    canon = normalize(canonical)
    scored = []
    for claim in Claim.objects.filter(duplicate_of__isnull=True, is_hidden=False).exclude(
            canonical_en="").order_by("-created_at")[:300]:
        s = fuzz.token_set_ratio(canon, normalize(claim.canonical_en))
        if s >= LOOSE:
            scored.append((s, claim))
    scored.sort(key=lambda m: -m[0])
    top = scored[:8]
    match_id = ai.pick_same_claim(canonical, [(c.pk, c.canonical_en) for _, c in top])
    if match_id is None:
        return None
    return next(((95, c) for _, c in top if c.pk == match_id), None)
