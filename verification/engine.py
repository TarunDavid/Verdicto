"""Weighted verdict engine (SPEC section 6). Pure functions, no database access."""
from dataclasses import dataclass

OPTIONS = ("FALSE", "TRUE", "MISLEADING", "PARTIALLY_TRUE", "UNVERIFIED")  # order breaks ties
PRIOR_A = 2
PRIOR_B = 2
VERIFIED_PRIOR_A = 3   # trust bonus for a verified work email
MIN_VOTES = 3
FINAL_CONFIDENCE = 0.70


def beta_weight(correct, total, work_email_verified=False):
    """weight = (correct + a) / (total + a + b). New verifier = 0.5, or 0.6 with a verified work email."""
    a = VERIFIED_PRIOR_A if work_email_verified else PRIOR_A
    return (correct + a) / (total + a + PRIOR_B)


def lookup_record(stats, domain, language):
    """stats: iterable of (domain, language, correct, total).

    1. exact (domain, language) stat
    2. otherwise the sum across languages for that domain
    3. otherwise (0, 0) -> the prior
    """
    stats = list(stats)
    for d, lang, correct, total in stats:
        if d == domain and lang == language:
            return correct, total
    same_domain = [(c, t) for d, _lang, c, t in stats if d == domain]
    if same_domain:
        return sum(c for c, _ in same_domain), sum(t for _, t in same_domain)
    return 0, 0


def verifier_weight(stats, domain, language, work_email_verified=False):
    correct, total = lookup_record(stats, domain, language)
    return beta_weight(correct, total, work_email_verified)


@dataclass
class VerdictResult:
    totals: dict
    leading: str | None
    confidence: float
    status: str          # "FINAL" or "UNDER_REVIEW"
    verdict: str | None  # set only when FINAL
    vote_count: int


def compute_verdict(votes):
    """votes: iterable of (verdict, weight). Returns a VerdictResult."""
    votes = list(votes)
    totals = {opt: 0.0 for opt in OPTIONS}
    for verdict, weight in votes:
        totals[verdict] += weight
    total_weight = sum(totals.values())
    if not votes or total_weight == 0:
        return VerdictResult(totals, None, 0.0, "UNDER_REVIEW", None, 0)

    leading = max(OPTIONS, key=lambda o: (totals[o], -OPTIONS.index(o)))
    confidence = totals[leading] / total_weight
    is_final = len(votes) >= MIN_VOTES and confidence >= FINAL_CONFIDENCE
    return VerdictResult(
        totals=totals,
        leading=leading,
        confidence=confidence,
        status="FINAL" if is_final else "UNDER_REVIEW",
        verdict=leading if is_final else None,
        vote_count=len(votes),
    )


def explain(votes):
    """Human-readable arithmetic, e.g. 'FALSE = 0.83 + 0.67 = 1.50; TRUE = 0.50; confidence = 75%'."""
    votes = list(votes)
    result = compute_verdict(votes)
    parts = []
    for opt in OPTIONS:
        weights = [w for v, w in votes if v == opt]
        if not weights:
            continue
        if len(weights) == 1:
            parts.append(f"{opt} = {weights[0]:.2f}")
        else:
            parts.append(f"{opt} = {' + '.join(f'{w:.2f}' for w in weights)} = {sum(weights):.2f}")
    parts.append(f"confidence = {result.confidence:.0%}")
    return "; ".join(parts)


def score_delta(vote_verdict, previous_final, new_final):
    """Track-record change for one vote, with no double counting.

    Returns (correct_delta, total_delta): undo the old scoring (if any), then apply the new one.
    """
    correct_delta = total_delta = 0
    if previous_final is not None:
        total_delta -= 1
        correct_delta -= int(vote_verdict == previous_final)
    if new_final is not None:
        total_delta += 1
        correct_delta += int(vote_verdict == new_final)
    return correct_delta, total_delta
